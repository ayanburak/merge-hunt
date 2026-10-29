"""
file: test_enrichment.py

Tests for pipeline/enrichment.py. GitHub is replaced by FakeClient, which
hands back fixed data and records what it was asked for. The store is the
real RepoStore on an in-memory SQLite database, so a test also proves that
what Enricher saved can be read back.

description: Checks when Enricher fetches, when it skips a fresh repo and how it handles errors.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.models import Actor, CommitHistory, PullRequest, PullRequestHistory, RepoRef
from merge_hunt.github.client import GitHubAuthError, GitHubQueryError
from merge_hunt.pipeline.enrichment import Enricher, EnrichResult, EnrichStatus
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore
from merge_hunt.storage.tables import RepoRow

# --- Helpers ---

START = datetime(2026, 10, 29, 12, tzinfo=UTC)
OPTIONS = MetricOptions()
CLICK = RepoRef(254517, "pallets", "click")
FLASK = RepoRef(596892, "pallets", "flask")

# Stands in for GitHubClient. errors maps a repo name to the exception its
# pull request fetch raises. cut_days moves complete_since that many days past
# the requested start, the way the page limit does.
class FakeClient:
    def __init__(self, errors: dict[str, Exception] | None = None, cut_days: int = 0) -> None:
        self.errors = errors or {}
        self.cut = timedelta(days=cut_days)
        self.calls: list[tuple[str, str, datetime]] = []

    async def pull_requests(self, owner, name, since, on_wait=None) -> PullRequestHistory:
        self.calls.append(("pull_requests", name, since))
        if name in self.errors:
            raise self.errors[name]
        pr = PullRequest(1, Actor("alice", False), "NONE", since + timedelta(days=100))
        return PullRequestHistory((pr,), since + self.cut, since)

    async def commits(self, owner, name, since, on_wait=None) -> CommitHistory:
        self.calls.append(("commits", name, since))
        return CommitHistory((), since, since)

# now can be moved forward by a test between two runs.
class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

# Runs body with an Enricher on a fresh in-memory database. body gets the
# enricher, its fake client, the clock and the store.
def run(body, client: FakeClient | None = None):
    async def main():
        engine = create_engine("sqlite+aiosqlite://")
        try:
            await create_tables(engine)
            store = RepoStore(async_sessionmaker(engine))
            fake = client or FakeClient()
            clock = Clock()
            return await body(Enricher(fake, store, clock=clock), fake, clock, store, engine)
        finally:
            await engine.dispose()
    return asyncio.run(main())

def statuses(results: list[EnrichResult]) -> list[EnrichStatus]:
    return [result.status for result in results]

# --- Fetching ---

def test_new_repo_is_fetched_over_the_users_windows():
    async def body(enricher, fake, clock, store, _):
        results = await enricher.enrich([CLICK], OPTIONS)
        return results, fake.calls, await store.load_enrichment(CLICK.id)

    results, calls, saved = run(body)
    assert statuses(results) == [EnrichStatus.FETCHED]
    assert calls == [
        ("pull_requests", "click", START - timedelta(days=OPTIONS.window_days)),
        ("commits", "click", START - timedelta(days=OPTIONS.commit_days)),
    ]
    assert saved.enriched_at == START
    assert len(saved.prs.prs) == 1

def test_fresh_repo_is_skipped():
    async def body(enricher, fake, clock, *_):
        await enricher.enrich([CLICK], OPTIONS)
        clock.now += timedelta(days=6)
        return await enricher.enrich([CLICK], OPTIONS), len(fake.calls)

    results, calls = run(body)
    assert statuses(results) == [EnrichStatus.FRESH]
    assert calls == 2

def test_old_repo_is_fetched_again():
    async def body(enricher, fake, clock, *_):
        await enricher.enrich([CLICK], OPTIONS)
        clock.now += timedelta(days=7)
        return await enricher.enrich([CLICK], OPTIONS)

    assert statuses(run(body)) == [EnrichStatus.FETCHED]

def test_wider_window_fetches_fresh_repo_again():
    async def body(enricher, *_):
        await enricher.enrich([CLICK], OPTIONS)
        return await enricher.enrich([CLICK], MetricOptions(window_days=365))

    assert statuses(run(body)) == [EnrichStatus.FETCHED]

# The saved pull requests only reach back 170 of the 180 days, still nothing
# would be gained by asking again.
def test_fetch_cut_by_page_limit_still_counts_as_fresh():
    async def body(enricher, *_):
        await enricher.enrich([CLICK], OPTIONS)
        return await enricher.enrich([CLICK], OPTIONS)

    assert statuses(run(body, FakeClient(cut_days=10))) == [EnrichStatus.FRESH]

def test_fresh_repo_still_gets_its_new_name():
    async def body(enricher, fake, clock, store, engine):
        await enricher.enrich([CLICK], OPTIONS)
        await enricher.enrich([RepoRef(CLICK.id, "pallets-eco", "click")], OPTIONS)
        async with engine.connect() as connection:
            return (await connection.execute(select(RepoRow.owner))).scalar_one()

    assert run(body) == "pallets-eco"

# --- Errors and Progress ---

@pytest.mark.parametrize("error", [
    GitHubQueryError("Could not resolve to a Repository"),
    httpx.ConnectError("connection lost"),
])
def test_failed_repo_does_not_stop_the_others(error):
    async def body(enricher, *_):
        seen = []

        async def on_progress(result):
            seen.append(result)

        results = await enricher.enrich([CLICK, FLASK], OPTIONS, on_progress)
        return results, seen

    results, seen = run(body, FakeClient(errors={"click": error}))
    assert statuses(results) == [EnrichStatus.FAILED, EnrichStatus.FETCHED]
    assert results[0].error == str(error)
    assert seen == results

def test_rejected_token_stops_the_run():
    async def body(enricher, fake, *_):
        with pytest.raises(GitHubAuthError):
            await enricher.enrich([CLICK, FLASK], OPTIONS)
        return fake.calls

    calls = run(body, FakeClient(errors={"click": GitHubAuthError("bad token")}))
    assert [name for _, name, _ in calls] == ["click"]
