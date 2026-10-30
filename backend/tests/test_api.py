"""
file: test_api.py

Tests for main.py and the scan and profile endpoints. The app's real startup runs against
a SQLite file in pytest's temporary folder, then ScanJob is swapped for a fake
so nothing reaches GitHub. Requests go through httpx's ASGITransport, the app
is called directly without a server or a network.

description: Checks the app's startup and shutdown and the answers of the scan and profile endpoints.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.config import Settings
from merge_hunt.api.schemas import ProfileSettings
from merge_hunt.domain.models import Actor, Commit, CommitHistory, PullRequest, PullRequestHistory, Repo, RepoRef
from merge_hunt.domain.scans import ScanStatus
from merge_hunt.domain.scoring import FilterProfile
from merge_hunt.jobs.scans import ScanAlreadyRunningError
from merge_hunt.main import create_app
from merge_hunt.pipeline.discovery import SearchSort
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore, ScanStore

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

# --- GET /scans ---

def test_list_scans_newest_first_and_limited(tmp_path):
    async def body(client, _):
        for _ in range(3):
            await client.post("/scans", json={})
        all_scans = (await client.get("/scans")).json()
        one = (await client.get("/scans", params={"limit": 1})).json()
        too_many = await client.get("/scans", params={"limit": 101})
        return all_scans, one, too_many

    all_scans, one, too_many = run(tmp_path, body)
    assert [scan["id"] for scan in all_scans] == [3, 2, 1]
    assert [scan["id"] for scan in one] == [3]
    assert too_many.status_code == 422

# --- POST /scans/{id}/results ---

MAINTAINER = Actor("maya", False)
SINCE = datetime.now(UTC) - timedelta(days=200)

# Puts a scan with two enriched repos and one discovered but not enriched in
# the file, repo 1 merges twelve newcomer pull requests and repo 2 only four.
async def save_results(tmp_path) -> int:
    engine = create_engine(database_url(tmp_path))
    await create_tables(engine)
    sessions = async_sessionmaker(engine)
    repos, scans = RepoStore(sessions), ScanStore(sessions)
    now = datetime.now(UTC)
    opened = now - timedelta(days=60)
    closed = opened + timedelta(days=2)
    for id, merged in ((1, 12), (2, 4), (3, 0)):
        await repos.save_repos([Repo(id, "owner", f"repo{id}", 1000, 100, "Python", "MIT", (), 10, now, None)], now)
        if id == 3:
            continue
        prs = tuple(
            PullRequest(n, Actor("alice", False), "NONE", opened, merged_at=closed if n < merged else None,
                        closed_at=closed, closed_by=MAINTAINER)
            for n in range(12)
        )
        commits = tuple(Commit(MAINTAINER, now - timedelta(days=1)) for _ in range(30))
        await repos.save_enrichment(id, PullRequestHistory(prs, SINCE, SINCE), CommitHistory(commits, SINCE, SINCE), now)
    scan_id = await scans.start({}, now)
    await scans.set_discovered(scan_id, 3, [RepoRef(id, "owner", f"repo{id}") for id in (1, 2, 3)])
    await engine.dispose()
    return scan_id

def test_results_are_sorted_and_say_why_a_repo_failed(tmp_path):
    scan_id = asyncio.run(save_results(tmp_path))

    async def body(client, _):
        return await client.post(f"/scans/{scan_id}/results")

    response = run(tmp_path, body)
    assert response.status_code == 200
    results = response.json()
    assert [item["repo"]["name"] for item in results] == ["repo1", "repo2"]
    assert results[0]["failed_filters"] == [] and results[1]["failed_filters"] == ["merge_rate"]
    assert results[0]["metrics"]["accepted"] == 12 and results[0]["commit_count"] == 30
    assert results[0]["window_covered"] is True

def test_results_use_the_settings_in_the_body(tmp_path):
    scan_id = asyncio.run(save_results(tmp_path))

    async def body(client, _):
        loose = await client.post(f"/scans/{scan_id}/results", json={"merge_rate": {"low": 0.1}})
        wide = await client.post(f"/scans/{scan_id}/results", json={"metrics": {"window_days": 365}})
        return loose.json(), wide.json()

    loose, wide = run(tmp_path, body)
    assert loose[1]["failed_filters"] == []
    assert wide[0]["window_covered"] is False

def test_results_of_unknown_scan_is_not_found(tmp_path):
    async def body(client, _):
        return await client.post("/scans/99/results")

    assert run(tmp_path, body).status_code == 404

# --- Profiles ---

def test_default_profile_matches_the_domain_defaults(tmp_path):
    async def body(client, _):
        return await client.get("/profiles/default")

    settings = ProfileSettings.model_validate(run(tmp_path, body).json())
    assert settings.filter_profile() == FilterProfile()

def test_profile_round_trip(tmp_path):
    async def body(client, _):
        created = await client.post("/profiles", json={"name": " strict ", "settings": {"merge_rate": {"low": 0.8}}})
        id = created.json()["id"]
        read = await client.get(f"/profiles/{id}")
        changed = await client.put(f"/profiles/{id}", json={"name": "loose", "settings": {"commit_target": 30}})
        listed = await client.get("/profiles")
        gone = await client.delete(f"/profiles/{id}")
        return created, read, changed, listed, gone, await client.get(f"/profiles/{id}"), await client.delete(f"/profiles/{id}")

    created, read, changed, listed, gone, missing, missing_delete = run(tmp_path, body)
    assert created.status_code == 201 and created.json()["name"] == "strict"
    assert read.json()["settings"]["merge_rate"] == {"low": 0.8, "high": None}
    assert read.json()["settings"]["ignored_rate"]["high"] == 0.4
    assert changed.json()["name"] == "loose" and changed.json()["settings"]["commit_target"] == 30
    assert changed.json()["settings"]["merge_rate"]["low"] == 0.5
    assert [item["name"] for item in listed.json()] == ["loose"]
    assert gone.status_code == 204
    assert missing.status_code == 404 and missing_delete.status_code == 404

def test_profile_name_is_unique(tmp_path):
    async def body(client, _):
        first = await client.post("/profiles", json={"name": "a"})
        await client.post("/profiles", json={"name": "b"})
        same = await client.post("/profiles", json={"name": "a"})
        renamed = await client.put(f"/profiles/{first.json()['id']}", json={"name": "b"})
        kept = await client.put(f"/profiles/{first.json()['id']}", json={"name": "a"})
        return same.status_code, renamed.status_code, kept.status_code

    assert run(tmp_path, body) == (409, 409, 200)

def test_update_of_unknown_profile_is_not_found(tmp_path):
    async def body(client, _):
        return await client.put("/profiles/99", json={"name": "a"})

    assert run(tmp_path, body).status_code == 404

def test_profile_settings_are_checked(tmp_path):
    async def body(client, _):
        bad = [
            {"name": "   "},
            {"name": "x" * 101},
            {"name": "a", "settings": {"merge_rate": {"low": 0.9, "high": 0.1}}},
            {"name": "a", "settings": {"ignored_rate": {"high": 1.5}}},
            {"name": "a", "settings": {"commits": {"low": -1}}},
            {"name": "a", "settings": {"weights": {"commits": -0.1}}},
            {"name": "a", "settings": {"commit_target": -1}},
            {"name": "a", "settings": {"metrics": {"min_sample": 0}}},
            {"name": "a", "settings": {"metrics": {"window_days": 0}}},
            {"name": "a", "settings": {"metrics": {"ignored_after_days": -1}}},
        ]
        return [(await client.post("/profiles", json=item)).status_code for item in bad]

    assert set(run(tmp_path, body)) == {422}

# A row saved before a field existed must still load, with that field's default.
def test_saved_profile_fills_in_missing_fields(tmp_path):
    async def body(client, _):
        created = await client.post("/profiles", json={"name": "old"})
        store = client._transport.app.state.profiles
        await store.update(created.json()["id"], "old", {"commit_target": 5}, NOW)
        return (await client.get(f"/profiles/{created.json()['id']}")).json()["settings"]

    settings = run(tmp_path, body)
    assert settings["commit_target"] == 5 and settings["merge_rate"]["low"] == 0.5
