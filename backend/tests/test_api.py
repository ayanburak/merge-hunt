"""
file: test_api.py

Tests for main.py and the scan endpoints. The app's real startup runs against
a SQLite file in pytest's temporary folder, then ScanJob is swapped for a fake
so nothing reaches GitHub. Requests go through httpx's ASGITransport, the app
is called directly without a server or a network.

description: Checks the app's startup and shutdown and the answers of POST /scans and GET /scans/{id}.
"""

import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.config import Settings
from merge_hunt.domain.scans import ScanStatus
from merge_hunt.jobs.scans import ScanAlreadyRunningError
from merge_hunt.main import create_app
from merge_hunt.pipeline.discovery import SearchSort
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import ScanStore

# --- Helpers ---

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)

# Opens the scan like ScanJob but never runs it, and keeps what it was given.
class FakeJob:
    def __init__(self, scans: ScanStore, busy: bool = False) -> None:
        self.scans = scans
        self.busy = busy
        self.started: list = []

    async def start(self, discovery, metrics) -> int:
        if self.busy:
            raise ScanAlreadyRunningError("A scan is already running, only one can run at a time")
        self.started.append((discovery, metrics))
        return await self.scans.start({}, NOW)

    async def close(self) -> None:
        pass

def database_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"

# Runs the app's startup, swaps in the fake job, calls body with a client and
# runs the shutdown afterwards.
def run(tmp_path, body, busy: bool = False):
    async def main():
        app = create_app(Settings(github_token=SecretStr("test-token"), database_url=database_url(tmp_path)))
        async with app.router.lifespan_context(app):
            fake = FakeJob(app.state.scans, busy)
            app.state.scan_job = fake
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await body(client, fake)
    return asyncio.run(main())

# --- Startup ---

# A scan left RUNNING in the file, like after a crash, is INTERRUPTED once the
# app has started.
def test_startup_marks_unfinished_scans_interrupted(tmp_path):
    async def leave_running():
        engine = create_engine(database_url(tmp_path))
        await create_tables(engine)
        scan_id = await ScanStore(async_sessionmaker(engine)).start({}, NOW)
        await engine.dispose()
        return scan_id

    scan_id = asyncio.run(leave_running())

    async def body(client, _):
        return (await client.get(f"/scans/{scan_id}")).json()

    assert run(tmp_path, body)["status"] == ScanStatus.INTERRUPTED

# --- POST /scans ---

def test_start_scan_returns_id_and_passes_options(tmp_path):
    async def body(client, fake):
        response = await client.post("/scans", json={"language": "Python", "sort": "updated-desc", "window_days": 120})
        scan = (await client.get(f"/scans/{response.json()['id']}")).json()
        return response, scan, fake.started

    response, scan, started = run(tmp_path, body)
    assert response.status_code == 202
    assert scan["status"] == ScanStatus.RUNNING and scan["stage"] == "DISCOVERY"
    discovery, metrics = started[0]
    assert discovery.language == "Python" and discovery.sort is SearchSort.UPDATED_DESC
    assert discovery.min_prs == 30 and metrics.window_days == 120 and metrics.commit_days == 90

def test_start_scan_while_running_is_conflict(tmp_path):
    async def body(client, _):
        return await client.post("/scans", json={})

    assert run(tmp_path, body, busy=True).status_code == 409

def test_start_scan_checks_ranges(tmp_path):
    async def body(client, _):
        stars = await client.post("/scans", json={"min_stars": 900, "max_stars": 100})
        prs = await client.post("/scans", json={"min_prs": 600})
        candidates = await client.post("/scans", json={"max_candidates": 1001})
        sort = await client.post("/scans", json={"sort": "forks"})
        return [item.status_code for item in (stars, prs, candidates, sort)]

    assert run(tmp_path, body) == [422, 422, 422, 422]

# --- GET /scans/{id} ---

def test_unknown_scan_is_not_found(tmp_path):
    async def body(client, _):
        return await client.get("/scans/99")

    assert run(tmp_path, body).status_code == 404
