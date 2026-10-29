"""
file: test_scan_job.py

Tests for jobs/scans.py and ScanStore. ScanJob gets a fake Discoverer and a
fake Enricher, the stages have their own tests, and the real ScanStore on an
in-memory SQLite database, so what is checked is what ends up in the scans
tables.

description: Checks that ScanJob runs both stages, records every step of a scan and allows one scan at a time.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.models import RepoRef
from merge_hunt.domain.scans import ScanStage, ScanStatus
from merge_hunt.github.client import GitHubAuthError, RateLimitWait, WaitReason
from merge_hunt.jobs.scans import ScanAlreadyRunningError, ScanJob
from merge_hunt.pipeline.discovery import DiscoveryOptions, DiscoveryResult
from merge_hunt.pipeline.enrichment import EnrichResult, EnrichStatus
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore, ScanStore
from merge_hunt.storage.tables import ScanRepoRow

# --- Helpers ---

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
CLICK = RepoRef(1, "pallets", "click")
FLASK = RepoRef(2, "pallets", "flask")

# Saves the repos like the real Discoverer, scan_repos points at them, and
# reports one wait so the test can see it recorded.
class FakeDiscoverer:
    def __init__(self, repos: RepoStore) -> None:
        self.repos = repos
        self.calls: list[datetime] = []

    async def discover(self, options, prs_since, on_wait=None) -> DiscoveryResult:
        self.calls.append(prs_since)
        for ref in (CLICK, FLASK):
            await self.repos.save_repo(ref.id, ref.owner, ref.name)
        await self.repos.save_repo(3, "pallets", "jinja")
        await on_wait(RateLimitWait(WaitReason.RATE_LIMIT, NOW + timedelta(minutes=5), 1))
        return DiscoveryResult(3, (CLICK, FLASK))

# The first repo is fetched and the second fails. waiting is what the scan
# showed while the first repo was being handled, the wait from discovery
# should still be there then.
class FakeEnricher:
    def __init__(self, scans: ScanStore, error: Exception | None = None) -> None:
        self.scans = scans
        self.error = error
        self.waiting = None

    async def enrich(self, repos, options, on_progress=None, on_wait=None):
        if self.error:
            raise self.error
        self.waiting = (await self.scans.load(1)).waiting_until
        await on_progress(EnrichResult(repos[0], EnrichStatus.FETCHED))
        await on_progress(EnrichResult(repos[1], EnrichStatus.FAILED, "not found"))

def run(body, error: Exception | None = None):
    async def main():
        engine = create_engine("sqlite+aiosqlite://")
        try:
            await create_tables(engine)
            sessions = async_sessionmaker(engine)
            scans = ScanStore(sessions)
            enricher = FakeEnricher(scans, error)
            job = ScanJob(FakeDiscoverer(RepoStore(sessions)), enricher, scans, clock=lambda: NOW)
            return await body(job, scans, enricher, engine)
        finally:
            await engine.dispose()
    return asyncio.run(main())

# --- ScanJob ---

def test_scan_records_every_step():
    async def body(job, scans, enricher, engine):
        scan_id = await job.start(DiscoveryOptions(language="Python"), MetricOptions(window_days=100))
        await job._task
        async with engine.connect() as connection:
            rows = (await connection.execute(select(ScanRepoRow.repo_id, ScanRepoRow.status, ScanRepoRow.error))).all()
        return await scans.load(scan_id), enricher.waiting, job._discoverer.calls, [tuple(row) for row in rows]

    scan, waiting, calls, rows = run(body)
    assert (scan.status, scan.stage) == (ScanStatus.DONE, ScanStage.ENRICHMENT)
    assert (scan.candidates, scan.skipped, scan.done, scan.failed) == (3, 1, 1, 1)
    assert scan.finished_at == NOW and scan.waiting_until is None
    assert scan.options["discovery"]["language"] == "Python" and scan.options["window_days"] == 100
    assert waiting == NOW + timedelta(minutes=5)
    assert calls == [NOW - timedelta(days=100)]
    assert sorted(rows) == [(1, "FETCHED", None), (2, "FAILED", "not found")]

def test_error_that_stops_scan_marks_it_failed():
    async def body(job, scans, *_):
        scan_id = await job.start(DiscoveryOptions(), MetricOptions())
        await job._task
        return await scans.load(scan_id)

    scan = run(body, GitHubAuthError("GitHub rejected the token"))
    assert scan.status is ScanStatus.FAILED
    assert scan.error == "GitHub rejected the token"
    assert scan.finished_at == NOW

def test_second_scan_refused_while_first_runs():
    async def body(job, *_):
        await job.start(DiscoveryOptions(), MetricOptions())
        with pytest.raises(ScanAlreadyRunningError):
            await job.start(DiscoveryOptions(), MetricOptions())
        await job._task
        second = await job.start(DiscoveryOptions(), MetricOptions())
        await job._task
        return second

    assert run(body) == 2

# Shutting down leaves the scan RUNNING, the next start of the app marks it.
def test_close_cancels_and_leaves_scan_running():
    async def body(job, scans, *_):
        scan_id = await job.start(DiscoveryOptions(), MetricOptions())
        await job.close()
        return job.running, await scans.load(scan_id)

    running, scan = run(body)
    assert not running
    assert scan.status is ScanStatus.RUNNING

# --- ScanStore ---

def test_interrupt_running_only_touches_running_scans():
    async def body(job, scans, *_):
        finished = await scans.start({}, NOW)
        await scans.finish(finished, ScanStatus.DONE, NOW)
        running = await scans.start({}, NOW)
        await scans.set_waiting(running, NOW + timedelta(hours=1))
        count = await scans.interrupt_running(NOW + timedelta(days=1))
        return count, await scans.load(finished), await scans.load(running)

    count, finished, running = run(body)
    assert count == 1
    assert finished.status is ScanStatus.DONE and finished.finished_at == NOW
    assert running.status is ScanStatus.INTERRUPTED
    assert running.finished_at == NOW + timedelta(days=1) and running.waiting_until is None

def test_unknown_scan_loads_none():
    async def body(job, scans, *_):
        return await scans.load(99)

    assert run(body) is None
