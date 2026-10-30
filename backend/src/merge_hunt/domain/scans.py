"""
file: scans.py
importable: Scan, ScanDetail, ScanFailure, ScanStatus, ScanStage

A scan is one run of discovery and enrichment that the user started. Scan is
how the database gives it back, with its state and its counts, the UI asks for
it every few seconds while the scan runs. jobs/scans.py writes it, storage
keeps it and api sends it out, so it lives here where all three can see it.

description: The state of one scan as the UI sees it.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# --- Values ---

# INTERRUPTED is set when the app starts and finds a scan still RUNNING, the
# process stopped in the middle of it. FAILED is only for an error that stops
# the whole scan, a single repo that fails is counted in Scan.failed.
class ScanStatus(StrEnum):
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"

class ScanStage(StrEnum):
    DISCOVERY = "DISCOVERY"
    ENRICHMENT = "ENRICHMENT"

# --- Scan ---

# candidates is what the search found and skipped how many of them fell
# outside the pull request range, both stay zero until discovery is done.
# done and failed count the enriched repos, a fresh repo counts as done.
# waiting_until is set while GitHub's limits make the scan wait. options holds
# what the scan was started with, as the JSON it was saved as.
@dataclass(frozen=True, slots=True)
class Scan:
    id: int
    status: ScanStatus
    stage: ScanStage
    started_at: datetime
    finished_at: datetime | None
    options: dict
    candidates: int
    skipped: int
    done: int
    failed: int
    waiting_until: datetime | None
    error: str | None

# repo is the owner/name form, the same one people see on GitHub.
@dataclass(frozen=True, slots=True)
class ScanFailure:
    repo: str
    error: str

# Only the answer about a single scan carries the failures, the scan list stays
# one row per scan and needs no second look at scan_repos.
@dataclass(frozen=True, slots=True)
class ScanDetail(Scan):
    failures: list[ScanFailure]
