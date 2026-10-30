"""
file: scans.py
importable: ScanJob, ScanAlreadyRunningError

ScanJob runs a scan in the background, discovery first, then enrichment and
then issue finding, and writes every step to the database through ScanStore.
It is built once in main.py with a Discoverer, an Enricher, an IssueFinder and
a ScanStore. start(discovery, metrics, issue_labels) opens the scan, starts the
work as an asyncio task and returns the scan id right away, the UI then follows
the scan with ScanStore.load.

Only one scan runs at a time. The client sends every request one after the
other anyway, so a second scan would only slow the first one down, start
raises ScanAlreadyRunningError instead. Scoring is not part of a scan, it runs
when the results are read, so a changed profile needs no new scan.

description: Runs the stages of a scan in the background and records its progress.
"""

# NOTE: Any exception that stops the scan marks it FAILED with the error text,
# not only GitHubAuthError. A background task that dies quietly would leave the
# scan RUNNING forever and the UI waiting. Cancelling the task on shutdown is
# not caught, the scan stays RUNNING and the next start marks it INTERRUPTED.

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.scans import ScanStatus
from merge_hunt.github.client import RateLimitWait
from merge_hunt.pipeline.discovery import Discoverer, DiscoveryOptions
from merge_hunt.pipeline.enrichment import EnrichResult, Enricher, EnrichStatus
from merge_hunt.pipeline.issue_finder import DEFAULT_ISSUE_LABELS, IssueFinder, IssueResult
from merge_hunt.storage.stores import ScanStore

logger = logging.getLogger(__name__)

# --- Errors ---

# The API turns this into 409, the user tried to start a second scan.
class ScanAlreadyRunningError(RuntimeError):
    pass

# --- Job ---

# The task is kept on the object, asyncio only holds a weak reference to a
# running task and one nobody points at can be collected half way.
class ScanJob:
    def __init__(
        self,
        discoverer: Discoverer,
        enricher: Enricher,
        finder: IssueFinder,
        scans: ScanStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._discoverer = discoverer
        self._enricher = enricher
        self._finder = finder
        self._scans = scans
        self._clock = clock
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # Only the two metric settings enrichment uses are saved, the rest of
    # MetricOptions plays no part in a scan.
    async def start(
        self,
        discovery: DiscoveryOptions,
        metrics: MetricOptions,
        issue_labels: Sequence[str] = DEFAULT_ISSUE_LABELS,
    ) -> int:
        """Opens a scan and starts it in the background.

        Args:
            discovery: The user's search settings.
            metrics: The user's metric settings, window_days and commit_days
                decide how far back pull requests and commits are fetched.
            issue_labels: Issues carrying one of these labels are fetched.

        Returns:
            The id of the new scan.

        Raises:
            ScanAlreadyRunningError: Another scan has not finished yet.
        """
        if self.running:
            raise ScanAlreadyRunningError("A scan is already running, only one can run at a time")
        options = {
            "discovery": asdict(discovery),
            "window_days": metrics.window_days,
            "commit_days": metrics.commit_days,
            "issue_labels": list(issue_labels),
        }
        scan_id = await self._scans.start(options, self._clock())
        self._task = asyncio.create_task(self.run(scan_id, discovery, metrics, issue_labels))
        return scan_id

    # Public so the tests can await a scan directly instead of racing a task.
    # The pull requests are counted over the same window enrichment fetches.
    # A repo that failed enrichment gets no scores, so its issues are skipped.
    async def run(
        self,
        scan_id: int,
        discovery: DiscoveryOptions,
        metrics: MetricOptions,
        issue_labels: Sequence[str] = DEFAULT_ISSUE_LABELS,
    ) -> None:
        """Runs one scan to the end and records how it went."""

        async def on_wait(wait: RateLimitWait) -> None:
            await self._scans.set_waiting(scan_id, wait.until)

        async def on_progress(result: EnrichResult) -> None:
            await self._scans.record_repo(scan_id, result.repo.id, result.status, result.error)

        async def on_issues(result: IssueResult) -> None:
            await self._scans.record_issues(scan_id, result.repo.id, result.error)

        try:
            prs_since = self._clock() - timedelta(days=metrics.window_days)
            found = await self._discoverer.discover(discovery, prs_since, on_wait)
            await self._scans.set_discovered(scan_id, found.found, found.repos)
            enriched = await self._enricher.enrich(found.repos, metrics, on_progress, on_wait)
            await self._scans.start_issues(scan_id)
            ready = [result.repo for result in enriched if result.status != EnrichStatus.FAILED]
            await self._finder.find(ready, issue_labels, on_issues, on_wait)
        except Exception as error:
            logger.exception("Scan %d failed", scan_id)
            await self._scans.finish(scan_id, ScanStatus.FAILED, self._clock(), str(error))
        else:
            await self._scans.finish(scan_id, ScanStatus.DONE, self._clock())

    # Called when the app shuts down. The scan is left RUNNING on purpose, the
    # next start marks it INTERRUPTED like after a crash.
    async def close(self) -> None:
        """Stops a running scan and waits until it has stopped."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
