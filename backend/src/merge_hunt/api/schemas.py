"""
file: schemas.py
importable: ScanRequest, ScanCreated

The shapes the API accepts and returns where a domain class does not fit
as it is. Values coming from outside are checked here with Pydantic, so the
domain dataclasses behind them never see a negative day count or a star range
upside down. A request that fails the checks gets FastAPI's 422 answer with
the field that was wrong.

description: Request and response bodies of the API and the checks on them.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.pipeline.discovery import MAX_CANDIDATES, DiscoveryOptions, SearchSort

# --- Scans ---

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

    @model_validator(mode="after")
    def _ranges_in_order(self) -> Self:
        if self.min_stars > self.max_stars:
            raise ValueError("min_stars is larger than max_stars")
        if self.min_prs > self.max_prs:
            raise ValueError("min_prs is larger than max_prs")
        return self

    def discovery_options(self) -> DiscoveryOptions:
        return DiscoveryOptions(**self.model_dump(exclude={"window_days", "commit_days"}))

    def metric_options(self) -> MetricOptions:
        return MetricOptions(window_days=self.window_days, commit_days=self.commit_days)

class ScanCreated(BaseModel):
    id: int
