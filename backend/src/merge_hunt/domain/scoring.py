"""
file: scoring.py
importable: Range, Weights, FilterProfile, ScoredRepo, wilson_bounds, score_repo, INSUFFICIENT_DATA

score_repo(repo, prs, commits, profile, now) is the entry point, it works out a
repo's metrics with metrics.py, checks them against the user's FilterProfile
and gives the repo a score between 0 and 1. Like metrics.py nothing in here
talks to GitHub or the database, the same input always gives the same output.

Hard filters and soft score do different jobs. A filter is a Range on the raw
value, a repo outside it fails and failed_filters names the filter. A failed
repo is still returned with its score, the caller decides whether to show it.
A metric that is None because of too little data never fails a filter, the
repo is marked insufficient instead, and only with hide_insufficient on does
that count as a failed filter of its own, INSUFFICIENT_DATA.

The score is the weighted average of four parts, each between 0 and 1. The two
rates go in as Wilson bounds and not as they are, so 12 out of 12 scores lower
than 200 out of 200. They go in even below min_sample, the bound already
pulls a small sample down, and with no pull request at all both parts are 0.
Only a missing response time is left out, its weight is shared by the others.

description: Filters and scores a repo against the user's profile from its raw data.
"""

# NOTE: The two rates used to drop out of the score like the response time
# when they were None. In the October 2026 trial schemathesis had 9 decided
# pull requests, one below min_sample, all its weight went to commits and it
# topped the list with 1.00. A repo with no outside pull requests would have
# done the same. Burak chose to score the rates from their counts instead.
#
# NOTE: The filters look at the raw rate and the score at the Wilson bound on
# purpose. The user sets a filter like at least 50 percent while looking at
# the rate the UI shows, the bound is only there to push small samples down
# the list.

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

from merge_hunt.domain.metrics import MetricOptions, RepoMetrics, compute_metrics, count_commits
from merge_hunt.domain.models import Commit, PullRequest, Repo

# --- Constants ---

# 1.96 gives the 95 percent interval, the usual choice.
Z_95 = 1.96

# The name a repo fails with when it is insufficient and the user hides those.
INSUFFICIENT_DATA = "insufficient_data"

# --- Profile ---

# Either end can be left empty, both empty means the filter is off.
@dataclass(frozen=True, slots=True)
class Range:
    low: float | None = None
    high: float | None = None

    def contains(self, value: float) -> bool:
        return (self.low is None or value >= self.low) and (self.high is None or value <= self.high)

# They do not have to add up to one, score_repo divides by the sum of the
# weights it actually uses.
@dataclass(frozen=True, slots=True)
class Weights:
    merge_rate: float = 0.4
    ignored_rate: float = 0.25
    response_days: float = 0.2
    commits: float = 0.15

# One set of the user's choices. The filter fields carry the names of the
# metrics they check, those names are what failed_filters reports. Frozen
# dataclasses are allowed as defaults since they can not be changed.
@dataclass(frozen=True, slots=True)
class FilterProfile:
    metrics: MetricOptions = MetricOptions()
    commits: Range = Range(low=10)
    merge_rate: Range = Range(low=0.5)
    ignored_rate: Range = Range(high=0.3)
    response_days: Range = Range()
    weights: Weights = Weights()
    commit_target: int = 60
    hide_insufficient: bool = False

# --- Result ---

# metrics and commit_count are kept so the UI can show the numbers behind the
# score and the reason a repo failed.
@dataclass(frozen=True, slots=True)
class ScoredRepo:
    repo: Repo
    metrics: RepoMetrics
    commit_count: int
    score: float
    failed_filters: tuple[str, ...]
    insufficient: bool

    @property
    def passed(self) -> bool:
        return not self.failed_filters

# --- Scoring ---

# The Wilson score interval. It stays between 0 and 1 and behaves at 0 and at
# 100 percent, where the simpler normal interval collapses to a single point.
# An empty sample says nothing, so the rate can be anywhere from 0 to 1.
def wilson_bounds(part: int, whole: int, z: float = Z_95) -> tuple[float, float]:
    """Gives the range a rate most likely sits in, judging by its sample size.

    Args:
        part: How many of the sample had the property, for example accepted
            pull requests.
        whole: The size of the sample.
        z: How wide the range is, the default is the 95 percent range.

    Returns:
        The lower and the upper bound, both between 0 and 1. An empty sample
        gives 0 and 1.
    """
    if whole == 0:
        return 0.0, 1.0
    rate = part / whole
    z2 = z * z
    center = rate + z2 / (2 * whole)
    spread = z * sqrt(rate * (1 - rate) / whole + z2 / (4 * whole * whole))
    return (center - spread) / (1 + z2 / whole), (center + spread) / (1 + z2 / whole)

# commit_target and ignored_after_days come from the UI, a zero there would
# divide by zero, so zero means full marks for commits and no marks for
# response time, where every pull request already counts as ignored.
def _score(metrics: RepoMetrics, commit_count: int, profile: FilterProfile) -> float:
    weights = profile.weights
    target = profile.commit_target
    merge_low, _ = wilson_bounds(metrics.accepted, metrics.accepted + metrics.rejected)
    _, ignored_high = wilson_bounds(metrics.ignored, metrics.evaluable)
    parts = [
        (weights.commits, min(1.0, commit_count / target) if target > 0 else 1.0),
        (weights.merge_rate, merge_low),
        (weights.ignored_rate, 1 - ignored_high),
    ]
    if metrics.median_response_days is not None:
        limit = profile.metrics.ignored_after_days
        parts.append((weights.response_days, max(0.0, 1 - metrics.median_response_days / limit) if limit > 0 else 0.0))
    total = sum(weight for weight, _ in parts)
    return sum(weight * value for weight, value in parts) / total if total > 0 else 0.0

def score_repo(
    repo: Repo,
    prs: Iterable[PullRequest],
    commits: Iterable[Commit],
    profile: FilterProfile,
    now: datetime,
) -> ScoredRepo:
    """Checks one repo against the user's filters and scores it.

    Args:
        repo: What discovery found out about the repo.
        prs: Every stored pull request of the repo.
        commits: Every stored commit of the repo.
        profile: The user's filters, weights and metric settings.
        now: The moment the windows are counted back from.

    Returns:
        The repo with its metrics, score and the names of the filters it
        failed, an empty tuple when it passed all of them.
    """
    metrics = compute_metrics(prs, profile.metrics, now)
    commit_count = count_commits(commits, profile.metrics, now)
    insufficient = None in (metrics.merge_rate, metrics.ignored_rate, metrics.median_response_days)
    checks = (
        ("commits", profile.commits, commit_count),
        ("merge_rate", profile.merge_rate, metrics.merge_rate),
        ("ignored_rate", profile.ignored_rate, metrics.ignored_rate),
        ("response_days", profile.response_days, metrics.median_response_days),
    )
    failed = [name for name, allowed, value in checks if value is not None and not allowed.contains(value)]
    if insufficient and profile.hide_insufficient:
        failed.append(INSUFFICIENT_DATA)
    return ScoredRepo(repo, metrics, commit_count, _score(metrics, commit_count, profile), tuple(failed), insufficient)
