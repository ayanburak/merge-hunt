"""
file: metrics.py
importable: MetricOptions, Outcome, RepoMetrics, is_bot, is_external, outcome, first_response_at, compute_metrics, count_commits

compute_metrics(prs, options, now) is the entry point, it takes the raw pull
requests of one repo and returns a RepoMetrics. The smaller functions work on
a single pull request and are public so the scoring step and the tests can use
them directly. is_external also needs how many of the author's pull requests
were already accepted when this one was opened, compute_metrics works that
out from the whole list. Nothing in here talks to GitHub or the database and the same
input always gives the same output, that is why now is passed in and never
read from the clock. now has to be timezone aware like the datetimes in
models.py, comparing it with them fails otherwise.

A rate in RepoMetrics is None when fewer pull requests than options.min_sample
went into it. The counts next to it are always filled, so the UI can say
there is not enough data and still show how many pull requests there were.

count_commits(commits, options, now) is separate from compute_metrics, it
counts the commits people made in the last options.commit_days days.

description: Calculates the outside contribution and commit activity metrics of a repo from its raw data.
"""

# NOTE: GitHub's author_association is the author's role today and not when
# the pull request was opened. Someone whose first pull request got merged is
# CONTRIBUTOR on that same pull request afterwards. In a trial with sixteen
# repos in October 2026, 32 of 41 merged CONTRIBUTOR pull requests were the
# author's first merge in the repo. That is why a CONTRIBUTOR is judged by the
# merges that came before the pull request and not by the label alone. Only
# the stored data is counted, so someone whose merges are all older than the
# window looks new again, which is close enough for a person who has been away
# that long.
#
# NOTE: A closed pull request with both closed_by and closed_via empty counts
# as withdrawn. GitHub hides the closed event of some accounts, most likely the
# ones it flagged as spam. In pallets/click thirteen pull requests came back
# like this in October 2026, in seven the author wrote that they were closing
# it themselves and none showed a maintainer closing it. Deleted accounts do
# not end up here, parsing.py turns them into the ghost account.
#
# XXX: Because deleted accounts all share the ghost login, a pull request whose
# author and closer were both deleted later looks like the author closed it and
# counts as withdrawn. There is nothing in the data to tell them apart.

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median

from merge_hunt.domain.models import Actor, Commit, PullRequest

# --- Associations ---

# CONTRIBUTOR is in neither set, its earlier merges decide which side it is on.
# Any value that is not listed counts as internal, models.py explains why.
_EXTERNAL = frozenset({"NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"})
_MAINTAINER = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

_DAY = timedelta(days=1)

# --- Options and Results ---

# The defaults are what the UI starts with. Frozen like the models, so one
# options object can be shared between calls without anyone changing it.
# excluded_labels is compared without caring about case like extra_bots. A
# pull request carrying one of them is left out of every metric, some repos
# label and close a flood of spam that would bury the real rejections.
# external_until_merges is how many accepted pull requests a CONTRIBUTOR may
# have had before this one and still count as external. 1 means only people
# with no merge yet, 0 never counts a CONTRIBUTOR and a large number always does.
@dataclass(frozen=True, slots=True)
class MetricOptions:
    external_until_merges: int = 1
    extra_bots: frozenset[str] = frozenset()
    excluded_labels: frozenset[str] = frozenset({"spam", "invalid", "rejected AI"})
    ignored_after_days: int = 30
    window_days: int = 180
    min_sample: int = 10
    commit_days: int = 90

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
def is_external(pr: PullRequest, prior_merges: int, options: MetricOptions) -> bool:
    """Tells whether a pull request came from outside the project.

    Args:
        pr: The pull request.
        prior_merges: How many of the author's pull requests in the same repo
            had been accepted before this one was opened. Only matters for a
            CONTRIBUTOR, GitHub labels the author by their role today.
        options: The user's settings.

    Returns:
        True for a pull request opened by a person who is not part of the
        team. Pull requests opened by bots are never external.
    """
    if is_bot(pr.author, options):
        return False
    if pr.author_association == "CONTRIBUTOR":
        return prior_merges < options.external_until_merges
    return pr.author_association in _EXTERNAL

# The order matters. closed_via is checked before the closer, because a commit
# that closes a pull request can also leave the person who pushed it as closer
# and that would look like a rejection.
def outcome(pr: PullRequest, options: MetricOptions) -> Outcome:
    """Decides how a pull request ended.

    A merge or a close by a commit or another pull request is ACCEPTED, a
    close by the author or by an account GitHub does not show is WITHDRAWN, a
    close by a bot is BOT_CLOSED and a close by anyone else is REJECTED.
    """
    if pr.merged_at is not None or pr.closed_via is not None:
        return Outcome.ACCEPTED
    if pr.closed_at is None:
        return Outcome.OPEN
    if pr.closed_by is None:
        return Outcome.WITHDRAWN
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

# The time of an accept is the merge, or the close for a pull request closed
# by a commit or another pull request. GitHub makes someone a CONTRIBUTOR at
# that moment, so it is what the pull requests opened later are compared with.
# The lists come back sorted for bisect.
def _merge_times(prs: Iterable[PullRequest], options: MetricOptions) -> dict[str, list[datetime]]:
    times: dict[str, list[datetime]] = defaultdict(list)
    for pr in prs:
        if outcome(pr, options) is Outcome.ACCEPTED:
            times[pr.author.login].append(pr.merged_at or pr.closed_at)
    for merges in times.values():
        merges.sort()
    return times

# Lowered on every call like extra_bots in is_bot, both lists are short.
def _is_excluded(pr: PullRequest, options: MetricOptions) -> bool:
    excluded = {label.lower() for label in options.excluded_labels}
    return any(label.lower() in excluded for label in pr.labels)

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
# same answer for it. The merge times are gathered from every pull request
# first, also the ones outside the window or with an excluded label, a merge is
# a merge whatever else is going on.
def compute_metrics(prs: Iterable[PullRequest], options: MetricOptions, now: datetime) -> RepoMetrics:
    """Calculates the outside contribution metrics of one repo.

    Only external pull requests opened in the last options.window_days days
    and carrying none of options.excluded_labels are counted.

    Args:
        prs: Every pull request of the repo, the ones outside the window, not
            external or with an excluded label are skipped here. All of them
            still count as earlier merges of their author.
        options: The user's settings.
        now: The moment the open pull requests are measured against.

    Returns:
        The rates with the counts they were made from. A rate is None when
        its count is below options.min_sample.
    """
    prs = tuple(prs)
    since = now - timedelta(days=options.window_days)
    limit = timedelta(days=options.ignored_after_days)
    merges = _merge_times(prs, options)
    accepted = rejected = ignored = evaluable = 0
    response_days: list[float] = []

    for pr in prs:
        if pr.created_at < since or _is_excluded(pr, options):
            continue
        prior = bisect_left(merges.get(pr.author.login, []), pr.created_at)
        if not is_external(pr, prior, options):
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

# --- Commits ---

# Bots are dropped here and not while fetching, so a name the user adds to
# extra_bots later also clears the commits that are already stored.
def count_commits(commits: Iterable[Commit], options: MetricOptions, now: datetime) -> int:
    """Counts the commits people made in the last options.commit_days days.

    Args:
        commits: The commits on the repo's default branch, older ones are
            skipped here.
        options: The user's settings.
        now: The moment the days are counted back from.

    Returns:
        The number of commits in the window, bot commits left out.
    """
    since = now - timedelta(days=options.commit_days)
    return sum(1 for commit in commits if commit.committed_at >= since and not is_bot(commit.author, options))
