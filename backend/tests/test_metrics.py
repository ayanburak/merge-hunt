"""
file: test_metrics.py

Tests for domain/metrics.py, one test per approved case. The numbers in the
compute_metrics tests are the examples the definitions were agreed on, so a
failure here means the code no longer matches the design.

description: Checks every metric rule against fixed pull requests, fixed commits and a fixed now.
"""

from datetime import UTC, datetime, timedelta

import pytest

from merge_hunt.domain.metrics import (
    MetricOptions,
    Outcome,
    compute_metrics,
    count_commits,
    first_response_at,
    is_bot,
    is_external,
    outcome,
)
from merge_hunt.domain.models import Actor, CloseSource, Commit, Interaction, PullRequest

# --- Helpers ---

# Every date is counted back from a fixed now, so the tests give the same
# result on any day they run.
NOW = datetime(2026, 10, 1, tzinfo=UTC)
OPTIONS = MetricOptions()

AUTHOR = Actor("alice", False)
MAINTAINER = Actor("maya", False)
STRANGER = Actor("sam", False)
STALE_BOT = Actor("stale[bot]", True)

def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)

# An external pull request opened sixty days ago and still open, each test
# only changes what it is about.
def make_pr(created_at: datetime = days_ago(60), association: str = "NONE", author: Actor = AUTHOR, **fields) -> PullRequest:
    return PullRequest(number=1, author=author, author_association=association, created_at=created_at, **fields)

def maintainer_comment(at: datetime) -> Interaction:
    return Interaction(MAINTAINER, "MEMBER", at)

# Merged two days after opening, so it is answered and never ignored.
def quick_merge(created_at: datetime = days_ago(60)) -> PullRequest:
    merged = created_at + timedelta(days=2)
    return make_pr(created_at, merged_at=merged, closed_at=merged, closed_by=MAINTAINER)

def rejected_after(days: float, created_at: datetime = days_ago(60)) -> PullRequest:
    closed = created_at + timedelta(days=days)
    return make_pr(created_at, closed_at=closed, closed_by=MAINTAINER)

def open_without_response(created_at: datetime = days_ago(60)) -> PullRequest:
    return make_pr(created_at)

# --- is_bot ---

def test_is_bot_github_flag():
    assert is_bot(Actor("helper", True), OPTIONS)

def test_is_bot_login_ending_in_bot():
    assert is_bot(Actor("renovate[bot]", False), OPTIONS)

def test_is_bot_extra_list_ignores_case():
    options = MetricOptions(extra_bots=frozenset({"Release-Helper"}))
    assert is_bot(Actor("release-helper", False), options)

def test_is_bot_normal_user():
    assert not is_bot(AUTHOR, OPTIONS)

# --- is_external ---

@pytest.mark.parametrize("association", ["NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"])
def test_is_external_outside_associations(association):
    assert is_external(make_pr(association=association), OPTIONS)

@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_is_external_team_associations(association):
    assert not is_external(make_pr(association=association), OPTIONS)

def test_is_external_contributor_follows_option():
    pr = make_pr(association="CONTRIBUTOR")
    assert not is_external(pr, OPTIONS)
    assert is_external(pr, MetricOptions(count_contributor_as_external=True))

def test_is_external_bot_author_is_not_external():
    assert not is_external(make_pr(author=Actor("dependabot[bot]", True)), OPTIONS)

# --- outcome ---

def test_outcome_merged_is_accepted():
    assert outcome(quick_merge(), OPTIONS) is Outcome.ACCEPTED

@pytest.mark.parametrize("source", [CloseSource.COMMIT, CloseSource.PULL_REQUEST])
def test_outcome_closed_by_commit_or_pr_is_accepted(source):
    pr = make_pr(closed_at=days_ago(50), closed_by=MAINTAINER, closed_via=source)
    assert outcome(pr, OPTIONS) is Outcome.ACCEPTED

def test_outcome_maintainer_close_is_rejected():
    assert outcome(rejected_after(5), OPTIONS) is Outcome.REJECTED

def test_outcome_author_close_is_withdrawn():
    pr = make_pr(closed_at=days_ago(50), closed_by=AUTHOR)
    assert outcome(pr, OPTIONS) is Outcome.WITHDRAWN

def test_outcome_bot_close_is_bot_closed():
    assert outcome(make_pr(closed_at=days_ago(50), closed_by=STALE_BOT), OPTIONS) is Outcome.BOT_CLOSED
    options = MetricOptions(extra_bots=frozenset({"cleanup-script"}))
    pr = make_pr(closed_at=days_ago(50), closed_by=Actor("cleanup-script", False))
    assert outcome(pr, options) is Outcome.BOT_CLOSED

def test_outcome_open():
    assert outcome(open_without_response(), OPTIONS) is Outcome.OPEN

# --- first_response_at ---

def test_first_response_earliest_maintainer_comment():
    pr = make_pr(interactions=(maintainer_comment(days_ago(50)), maintainer_comment(days_ago(55))))
    assert first_response_at(pr, OPTIONS) == days_ago(55)

# The author is a MEMBER here, otherwise their comment would be skipped for
# not being a maintainer and the test would prove nothing.
def test_first_response_skips_author_comments():
    pr = make_pr(association="MEMBER", interactions=(Interaction(AUTHOR, "MEMBER", days_ago(55)),))
    assert first_response_at(pr, OPTIONS) is None

@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR"])
def test_first_response_skips_non_maintainers(association):
    pr = make_pr(interactions=(Interaction(STRANGER, association, days_ago(55)),))
    assert first_response_at(pr, OPTIONS) is None

def test_first_response_skips_bot_with_maintainer_association():
    pr = make_pr(interactions=(Interaction(Actor("ci-helper", True), "MEMBER", days_ago(55)),))
    assert first_response_at(pr, OPTIONS) is None

def test_first_response_silent_close_counts():
    assert first_response_at(rejected_after(20), OPTIONS) == days_ago(40)

def test_first_response_author_close_does_not_count():
    pr = make_pr(closed_at=days_ago(50), closed_by=AUTHOR)
    assert first_response_at(pr, OPTIONS) is None

def test_first_response_none_without_any_response():
    assert first_response_at(open_without_response(), OPTIONS) is None

def test_first_response_skips_comment_after_close():
    pr = make_pr(closed_at=days_ago(50), closed_by=AUTHOR, interactions=(maintainer_comment(days_ago(10)),))
    assert first_response_at(pr, OPTIONS) is None

# --- compute_metrics ---

def test_compute_skips_prs_outside_window():
    prs = [quick_merge(days_ago(200)), quick_merge(days_ago(100))]
    metrics = compute_metrics(prs, OPTIONS, NOW)
    assert metrics.accepted == 1
    assert metrics.responded == 1

# Withdrawn and open pull requests are added to show they stay out of the rate.
def test_compute_merge_rate_example():
    prs = [quick_merge() for _ in range(12)] + [rejected_after(5) for _ in range(3)]
    prs += [make_pr(closed_at=days_ago(50), closed_by=AUTHOR), open_without_response()]
    metrics = compute_metrics(prs, OPTIONS, NOW)
    assert (metrics.accepted, metrics.rejected) == (12, 3)
    assert metrics.merge_rate == 0.8

def test_compute_ignored_rate_example():
    prs = [open_without_response() for _ in range(4)] + [quick_merge() for _ in range(14)]
    metrics = compute_metrics(prs, OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (4, 18)
    assert metrics.ignored_rate == 4 / 18

def test_compute_young_open_prs_left_out_of_ignored():
    young = days_ago(10)
    prs = [quick_merge() for _ in range(10)]
    prs += [open_without_response(young), make_pr(young, interactions=(maintainer_comment(days_ago(9)),))]
    metrics = compute_metrics(prs, OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (0, 10)

def test_compute_withdrawn_after_response_not_ignored():
    pr = make_pr(closed_at=days_ago(20), closed_by=AUTHOR, interactions=(maintainer_comment(days_ago(55)),))
    metrics = compute_metrics([pr], OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (0, 1)

# A wider window is needed, the slowest answer takes two hundred days and the
# pull request has to be opened inside the window.
def test_compute_median_response_example():
    created = days_ago(250)
    prs = [rejected_after(days, created) for days in (1, 2, 2, 3, 200)]
    metrics = compute_metrics(prs, MetricOptions(window_days=365, min_sample=5), NOW)
    assert metrics.responded == 5
    assert metrics.median_response_days == 2.0

def test_compute_small_sample_empties_only_that_metric():
    prs = [quick_merge() for _ in range(9)] + [open_without_response() for _ in range(3)]
    metrics = compute_metrics(prs, OPTIONS, NOW)
    assert metrics.accepted == 9
    assert metrics.merge_rate is None
    assert metrics.median_response_days is None
    assert metrics.ignored_rate == 3 / 12

def test_compute_quick_withdrawal_without_response_left_out():
    pr = make_pr(closed_at=days_ago(50), closed_by=AUTHOR)
    metrics = compute_metrics([pr], OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (0, 0)

def test_compute_quick_bot_close_without_response_left_out():
    pr = make_pr(closed_at=days_ago(55), closed_by=STALE_BOT)
    metrics = compute_metrics([pr], OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (0, 0)

def test_compute_late_first_response_still_ignored():
    pr = make_pr(
        merged_at=days_ago(10),
        closed_at=days_ago(10),
        closed_by=MAINTAINER,
        interactions=(maintainer_comment(days_ago(15)),),
    )
    metrics = compute_metrics([pr], OPTIONS, NOW)
    assert (metrics.ignored, metrics.evaluable) == (1, 1)

# --- count_commits ---

def commit(days: float, author: Actor = AUTHOR) -> Commit:
    return Commit(author=author, committed_at=days_ago(days))

def test_count_commits_skips_bots():
    commits = [commit(5), commit(6, Actor("dependabot[bot]", True)), commit(7)]
    assert count_commits(commits, OPTIONS, NOW) == 2

def test_count_commits_respects_extra_bots():
    options = MetricOptions(extra_bots=frozenset({"Release-Helper"}))
    commits = [commit(5), commit(6, Actor("release-helper", False))]
    assert count_commits(commits, options, NOW) == 1

# Exactly commit_days ago is still inside, a minute earlier is not.
def test_count_commits_only_counts_last_commit_days():
    edge = OPTIONS.commit_days
    commits = [commit(1), commit(edge), commit(edge + 1 / (24 * 60))]
    assert count_commits(commits, OPTIONS, NOW) == 2

def test_count_commits_with_no_commits_is_zero():
    assert count_commits([], OPTIONS, NOW) == 0
