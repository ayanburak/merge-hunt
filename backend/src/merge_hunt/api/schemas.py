"""
file: schemas.py
importable: ScanRequest, ScanCreated, ProfileSettings, ProfileWrite, ProfileAnswer

The shapes the API accepts and returns where a domain class does not fit
as it is. Values coming from outside are checked here with Pydantic, so the
domain dataclasses behind them never see a negative day count or a star range
upside down. A request that fails the checks gets FastAPI's 422 answer with
the field that was wrong.

description: Request and response bodies of the API and the checks on them.
"""

# XXX: A body with a bare NaN is refused by ProfileSettings, but FastAPI's 422
# answer repeats the input and JSON can not carry NaN, so the client gets a 500.
# NaN is not valid JSON and no browser sends it, the request is refused either way.

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.scoring import FilterProfile, Range, SavedProfile, Weights
from merge_hunt.pipeline.discovery import MAX_CANDIDATES, DiscoveryOptions, SearchSort
from merge_hunt.pipeline.issue_finder import DEFAULT_ISSUE_LABELS

# --- Scans ---

LabelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# The defaults repeat the ones in DiscoveryOptions and MetricOptions, a field
# left out of the request gets the same value the domain would give it.
class ScanRequest(BaseModel):
    language: str | None = None
    min_stars: int = Field(200, ge=0)
    max_stars: int = Field(5000, ge=0)
    pushed_within_days: int = Field(90, ge=1)
    topic: str | None = None
    license: str | None = None
    min_good_first_issues: int = Field(1, ge=0)
    min_help_wanted_issues: int = Field(0, ge=0)
    max_candidates: int = Field(100, ge=1, le=MAX_CANDIDATES)
    sort: SearchSort = SearchSort.STARS_DESC
    min_prs: int = Field(30, ge=0)
    max_prs: int = Field(500, ge=0)
    window_days: int = Field(180, ge=1)
    commit_days: int = Field(90, ge=1)
    issue_labels: list[LabelName] = Field(list(DEFAULT_ISSUE_LABELS), min_length=1)

    @model_validator(mode="after")
    def _ranges_in_order(self) -> Self:
        if self.min_stars > self.max_stars:
            raise ValueError("min_stars is larger than max_stars")
        if self.min_prs > self.max_prs:
            raise ValueError("min_prs is larger than max_prs")
        return self

    def discovery_options(self) -> DiscoveryOptions:
        return DiscoveryOptions(**self.model_dump(exclude={"window_days", "commit_days", "issue_labels"}))

    def metric_options(self) -> MetricOptions:
        return MetricOptions(window_days=self.window_days, commit_days=self.commit_days)

class ScanCreated(BaseModel):
    id: int

# --- Profiles ---

# The fields are the domain's own dataclasses, so the defaults live only in
# FilterProfile and are not repeated here like in ScanRequest. Pydantic checks
# the types of those dataclasses and the validator below their values.
# allow_inf_nan is off because a NaN weight would make a score JSON can not carry.
_DEFAULT = FilterProfile()

class ProfileSettings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    metrics: MetricOptions = _DEFAULT.metrics
    commits: Range = _DEFAULT.commits
    merge_rate: Range = _DEFAULT.merge_rate
    ignored_rate: Range = _DEFAULT.ignored_rate
    response_days: Range = _DEFAULT.response_days
    weights: Weights = _DEFAULT.weights
    commit_target: int = Field(_DEFAULT.commit_target, ge=0)
    hide_insufficient: bool = _DEFAULT.hide_insufficient

    @model_validator(mode="after")
    def _values_make_sense(self) -> Self:
        ranges = {
            "commits": self.commits,
            "merge_rate": self.merge_rate,
            "ignored_rate": self.ignored_rate,
            "response_days": self.response_days,
        }
        for name, allowed in ranges.items():
            ends = [end for end in (allowed.low, allowed.high) if end is not None]
            if any(end < 0 for end in ends):
                raise ValueError(f"{name} has a negative end")
            if len(ends) == 2 and allowed.low > allowed.high:
                raise ValueError(f"{name} has a low end above its high end")
        for name in ("merge_rate", "ignored_rate"):
            if any(end is not None and end > 1 for end in (ranges[name].low, ranges[name].high)):
                raise ValueError(f"{name} is a rate, its ends can not be above 1")
        weights = self.weights
        if min(weights.merge_rate, weights.ignored_rate, weights.response_days, weights.commits) < 0:
            raise ValueError("weights can not be negative")
        metrics = self.metrics
        if min(metrics.external_until_merges, metrics.ignored_after_days) < 0:
            raise ValueError("external_until_merges and ignored_after_days can not be negative")
        if min(metrics.window_days, metrics.commit_days, metrics.min_sample) < 1:
            raise ValueError("window_days, commit_days and min_sample have to be at least 1")
        return self

    def filter_profile(self) -> FilterProfile:
        return FilterProfile(**dict(self))

ProfileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

# The body of POST /profiles and PUT /profiles/{id}.
class ProfileWrite(BaseModel):
    name: ProfileName
    settings: ProfileSettings = ProfileSettings()

# How a saved profile goes out. The settings pass through ProfileSettings, that
# fills in fields added after the profile was saved.
class ProfileAnswer(BaseModel):
    id: int
    name: str
    settings: ProfileSettings
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, saved: SavedProfile) -> Self:
        return cls(
            id=saved.id,
            name=saved.name,
            settings=ProfileSettings.model_validate(saved.settings),
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )
