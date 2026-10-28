"""
file: metrics.py
importable: MetricOptions, Outcome, RepoMetrics, is_bot, is_external, outcome, first_response_at, compute_metrics

compute_metrics(prs, options, now) is the entry point, it takes the raw pull
requests of one repo and returns a RepoMetrics. The smaller functions work on
a single pull request and are public so the scoring step and the tests can use
them directly. Nothing in here talks to GitHub or the database and the same
input always gives the same output, that is why now is passed in and never
read from the clock. now has to be timezone aware like the datetimes in
models.py, comparing it with them fails otherwise.

A rate in RepoMetrics is None when fewer pull requests than options.min_sample
went into it. The counts next to it are always filled, so the UI can say
there is not enough data and still show how many pull requests there were.

description: Calculates the outside contribution metrics of a repo from its raw pull request data.
"""

# NOTE: A pull request with both closed_by and closed_via empty counts as
# rejected. A rejection is defined as a close by anyone other than the author
# or a bot, and an unknown closer is neither as far as we can tell. Deleted
# accounts should not end up here, the enrichment step will get them from
# GitHub as the ghost account.
#
# XXX: Because deleted accounts all share the ghost login, a pull request whose
# author and closer were both deleted later looks like the author closed it and
# counts as withdrawn. There is nothing in the data to tell them apart.

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median

from merge_hunt.domain.models import Actor, PullRequest

# --- Associations ---

# CONTRIBUTOR is in neither set, MetricOptions decides which side it is on.
# Any value that is not listed counts as internal, models.py explains why.
_EXTERNAL = frozenset({"NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"})
_MAINTAINER = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_DAY = timedelta(days=1)

# --- Options and Results ---

# The defaults are what the UI starts with. Frozen like the models, so one
# options object can be shared between calls without anyone changing it.
@dataclass(frozen=True, slots=True)
class MetricOptions:
    count_contributor_as_external: bool = False
    extra_bots: frozenset[str] = frozenset()
    ignored_after_days: int = 30
    window_days: int = 180
    min_sample: int = 10

# WITHDRAWN, BOT_CLOSED and OPEN stay out of the merge rate, none of them is a
# maintainer's decision.
class Outcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    BOT_CLOSED = "BOT_CLOSED"
    OPEN = "OPEN"

# Each rate sits next to the counts it was made from, so the UI can show the
# rate together with how many pull requests it came from.
@dataclass(frozen=True, slots=True)
class RepoMetrics:
    accepted: int
    rejected: int
    merge_rate: float | None
    ignored: int
    evaluable: int
    ignored_rate: float | None
    responded: int
    median_response_days: float | None

# --- Single Pull Request ---

# The user's bot list is typed by hand in the UI, so its names are compared
# without caring about upper and lower case. The list is short enough that
# lowering it on every call costs nothing.
def is_bot(actor: Actor, options: MetricOptions) -> bool:
    """Tells whether an account is a bot.

    GitHub's own bot flag, a login ending in [bot] and the names in
    options.extra_bots all count.
    """
    if actor.is_bot or actor.login.endswith("[bot]"):
        return True
    login = actor.login.lower()
    return any(login == name.lower() for name in options.extra_bots)

# The bot check comes first, Dependabot and Renovate show up as NONE and would
# pass as outside contributors otherwise.
def is_external(pr: PullRequest, options: MetricOptions) -> bool:
    """Tells whether a pull request came from outside the project.

    Returns:
        True for a pull request opened by a person who is not part of the
        team. Pull requests opened by bots are never external.
    """
    if is_bot(pr.author, options):
        return False
    if pr.author_association == "CONTRIBUTOR":
        return options.count_contributor_as_external
    return pr.author_association in _EXTERNAL

# The order matters. closed_via is checked before the closer, because a commit
# that closes a pull request can also leave the person who pushed it as closer
# and that would look like a rejection.
def outcome(pr: PullRequest, options: MetricOptions) -> Outcome:
    """Decides how a pull request ended.

    A merge or a close by a commit or another pull request is ACCEPTED, a
    close by the author is WITHDRAWN, a close by a bot is BOT_CLOSED and a
    close by anyone else is REJECTED.
    """
    if pr.merged_at is not None or pr.closed_via is not None:
        return Outcome.ACCEPTED
    if pr.closed_at is None:
        return Outcome.OPEN
    if pr.closed_by is None:
        return Outcome.REJECTED
    if is_bot(pr.closed_by, options):
        return Outcome.BOT_CLOSED
    if pr.closed_by.login == pr.author.login:
        return Outcome.WITHDRAWN
    return Outcome.REJECTED

# Closing counts as a response only when outcome says a maintainer made the
# call, that is ACCEPTED or REJECTED. Comments and reviews after the close are
# skipped, nobody is waiting on a closed pull request and a polite comment
# written later would turn a quick withdrawal into an ignored one.
def first_response_at(pr: PullRequest, options: MetricOptions) -> datetime | None:
    """Finds when a maintainer first responded to a pull request.

    A response is a comment or review by a non bot OWNER, MEMBER or
    COLLABORATOR other than the author, or the maintainer's close itself.

    Returns:
        Time of the earliest response, None if there was none before the
        pull request closed.
    """
    times = [
        item.at
        for item in pr.interactions
        if item.association in _MAINTAINER
        and not is_bot(item.actor, options)
        and item.actor.login != pr.author.login
        and (pr.closed_at is None or item.at <= pr.closed_at)
    ]
    result = outcome(pr, options)
    if result is Outcome.ACCEPTED:
        times.append(pr.merged_at or pr.closed_at)
    elif result is Outcome.REJECTED:
        times.append(pr.closed_at)
    return min(times, default=None)

# --- Repo Metrics ---

# Answers three ways. None keeps the pull request out of the ignored rate, that
# is an open one younger than the limit or one closed before the limit without
# a response. True and False both count as evaluable. The wait runs until the
# first response, a response coming after the limit does not undo it.
def _is_ignored(pr: PullRequest, response: datetime | None, limit: timedelta, now: datetime) -> bool | None:
    if pr.closed_at is None and now - pr.created_at < limit:
        return None
    waited = (response or pr.closed_at or now) - pr.created_at
    if waited >= limit:
        return True
    return False if response is not None else None

# whole is checked against zero too, a min_sample of zero from the UI would
# divide by zero otherwise.
def _rate(part: int, whole: int, min_sample: int) -> float | None:
    if whole == 0 or whole < min_sample:
        return None
    return part / whole

# Every metric is counted in one pass over the pull requests, so outcome and
# first_response_at run once per pull request and all three metrics see the
# same answer for it.
def compute_metrics(prs: Iterable[PullRequest], options: MetricOptions, now: datetime) -> RepoMetrics:
    """Calculates the outside contribution metrics of one repo.

    Only external pull requests opened in the last options.window_days days
    are counted.

    Args:
        prs: Every pull request of the repo, the ones outside the window or
            not external are skipped here.
        options: The user's settings.
        now: The moment the open pull requests are measured against.

    Returns:
        The rates with the counts they were made from. A rate is None when
        its count is below options.min_sample.
    """
    since = now - timedelta(days=options.window_days)
    limit = timedelta(days=options.ignored_after_days)
    accepted = rejected = ignored = evaluable = 0
    response_days: list[float] = []

    for pr in prs:
        if pr.created_at < since or not is_external(pr, options):
            continue

        result = outcome(pr, options)
        if result is Outcome.ACCEPTED:
            accepted += 1
        elif result is Outcome.REJECTED:
            rejected += 1

        response = first_response_at(pr, options)
        if response is not None:
            response_days.append((response - pr.created_at) / _DAY)

        state = _is_ignored(pr, response, limit, now)
        if state is not None:
            evaluable += 1
            ignored += state

    enough = response_days and len(response_days) >= options.min_sample
    return RepoMetrics(
        accepted=accepted,
        rejected=rejected,
        merge_rate=_rate(accepted, accepted + rejected, options.min_sample),
        ignored=ignored,
        evaluable=evaluable,
        ignored_rate=_rate(ignored, evaluable, options.min_sample),
        responded=len(response_days),
        median_response_days=median(response_days) if enough else None,
    )
