"""
file: test_scoring.py

Tests for domain/scoring.py. The Wilson numbers are the examples the method
was agreed on, so a failure there means the formula changed.

description: Checks the filters, the Wilson bounds and the score against fixed repos and a fixed now.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.models import Actor, Commit, PullRequest, Repo
from merge_hunt.domain.scoring import INSUFFICIENT_DATA, FilterProfile, Range, Weights, score_repo, wilson_bounds

# --- Helpers ---

NOW = datetime(2026, 10, 1, tzinfo=UTC)
PROFILE = FilterProfile()
REPO = Repo(1, "pallets", "click", 3000, 300, "Python", "MIT", (), 40, NOW, None)

AUTHOR = Actor("alice", False)
MAINTAINER = Actor("maya", False)

def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)

# Every pull request is opened sixty days ago by a newcomer and closed by a
# maintainer after the given days, so it is answered and never ignored.
def merged(after: float = 2) -> PullRequest:
    closed = days_ago(60 - after)
    return PullRequest(1, AUTHOR, "NONE", days_ago(60), merged_at=closed, closed_at=closed, closed_by=MAINTAINER)

def rejected(after: float = 5) -> PullRequest:
    return PullRequest(1, AUTHOR, "NONE", days_ago(60), closed_at=days_ago(60 - after), closed_by=MAINTAINER)

def unanswered() -> PullRequest:
    return PullRequest(1, AUTHOR, "NONE", days_ago(60))

def commits(count: int) -> list[Commit]:
    return [Commit(MAINTAINER, days_ago(1)) for _ in range(count)]

# Twelve merged and three rejected, a merge rate of 0.8 with a median answer
# of two days.
def good_prs() -> list[PullRequest]:
    return [merged() for _ in range(12)] + [rejected() for _ in range(3)]

# --- wilson_bounds ---

@pytest.mark.parametrize(("part", "whole", "low"), [(12, 12, 0.76), (200, 200, 0.98), (10, 12, 0.55), (160, 200, 0.74)])
def test_wilson_lower_bound_examples(part, whole, low):
    assert wilson_bounds(part, whole)[0] == pytest.approx(low, abs=0.005)

def test_wilson_empty_sample_is_full_range():
    assert wilson_bounds(0, 0) == (0.0, 1.0)

def test_wilson_bounds_stay_between_zero_and_one():
    low, high = wilson_bounds(0, 5)
    assert low == pytest.approx(0) and 0 < high < 1
    assert wilson_bounds(5, 5)[1] == pytest.approx(1)

# --- Range ---

def test_range_empty_lets_everything_in():
    assert Range().contains(-5) and Range().contains(1e9)

def test_range_ends_are_inclusive():
    allowed = Range(low=0.5, high=0.8)
    assert allowed.contains(0.5) and allowed.contains(0.8)
    assert not allowed.contains(0.49) and not allowed.contains(0.81)

# --- Filters ---

def test_good_repo_passes_default_filters():
    result = score_repo(REPO, good_prs(), commits(30), PROFILE, NOW)
    assert result.passed and not result.insufficient
    assert result.commit_count == 30

def test_failed_filters_are_named():
    prs = [merged() for _ in range(4)] + [rejected() for _ in range(8)]
    result = score_repo(REPO, prs, commits(3), PROFILE, NOW)
    assert result.failed_filters == ("commits", "merge_rate")

def test_ignored_rate_filter_uses_upper_end():
    prs = good_prs() + [unanswered() for _ in range(10)]
    assert score_repo(REPO, prs, commits(30), PROFILE, NOW).failed_filters == ("ignored_rate",)

def test_response_days_filter_off_by_default_and_works_when_set():
    slow = [merged(after=20) for _ in range(12)]
    assert score_repo(REPO, slow, commits(30), PROFILE, NOW).passed
    profile = replace(PROFILE, response_days=Range(high=7))
    assert score_repo(REPO, slow, commits(30), profile, NOW).failed_filters == ("response_days",)

# Nine pull requests are below min_sample, every rate is None and only the
# commit filter can still fail.
def test_insufficient_repo_is_marked_not_dropped():
    result = score_repo(REPO, [rejected() for _ in range(9)], commits(30), PROFILE, NOW)
    assert result.insufficient and result.passed

def test_hide_insufficient_fails_the_repo():
    profile = replace(PROFILE, hide_insufficient=True)
    result = score_repo(REPO, [rejected() for _ in range(9)], commits(30), profile, NOW)
    assert result.failed_filters == (INSUFFICIENT_DATA,)

# --- Score ---

def test_score_is_weighted_average_of_parts():
    result = score_repo(REPO, good_prs(), commits(30), PROFILE, NOW)
    merge_part = wilson_bounds(12, 15)[0]
    ignored_part = 1 - wilson_bounds(0, 15)[1]
    response_part = 1 - 2 / 30
    expected = 0.4 * merge_part + 0.25 * ignored_part + 0.2 * response_part + 0.15 * 0.5
    assert result.score == pytest.approx(expected)

# No pull request at all gives 0 for both rates, only the missing response
# time hands its weight to the other three parts.
def test_no_pull_requests_scores_rates_as_zero():
    result = score_repo(REPO, [], commits(30), PROFILE, NOW)
    assert result.score == pytest.approx(0.15 * 0.5 / 0.8)

# Nine decided pull requests are below min_sample, the rates are None on screen
# but still score through their counts.
def test_rates_below_min_sample_still_scored():
    result = score_repo(REPO, [merged() for _ in range(9)], [], PROFILE, NOW)
    assert result.metrics.merge_rate is None
    expected = 0.4 * wilson_bounds(9, 9)[0] + 0.25 * (1 - wilson_bounds(0, 9)[1])
    assert result.score == pytest.approx(expected / 0.8)

def test_bigger_sample_scores_higher_at_same_rate():
    small = score_repo(REPO, [merged() for _ in range(12)], commits(60), PROFILE, NOW)
    large = score_repo(REPO, [merged() for _ in range(200)], commits(60), PROFILE, NOW)
    assert small.score < large.score

def test_response_part_reaches_zero_at_ignored_limit():
    weights = Weights(merge_rate=0, ignored_rate=0, response_days=1, commits=0)
    profile = replace(PROFILE, weights=weights)
    assert score_repo(REPO, [merged(after=0) for _ in range(12)], [], profile, NOW).score == pytest.approx(1)
    assert score_repo(REPO, [merged(after=45) for _ in range(12)], [], profile, NOW).score == 0

def test_commit_part_caps_at_target():
    profile = replace(PROFILE, weights=Weights(merge_rate=0, ignored_rate=0, response_days=0, commits=1))
    assert score_repo(REPO, [], commits(90), profile, NOW).score == 1
    assert score_repo(REPO, [], commits(30), profile, NOW).score == 0.5
    assert score_repo(REPO, [], commits(30), replace(profile, commit_target=0), NOW).score == 1

def test_metric_options_reach_the_metrics():
    profile = replace(PROFILE, metrics=MetricOptions(min_sample=1))
    assert score_repo(REPO, [merged()], [], profile, NOW).metrics.merge_rate == 1.0
