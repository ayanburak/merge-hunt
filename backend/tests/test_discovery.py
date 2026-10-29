"""
file: test_discovery.py

Tests for pipeline/discovery.py. search_query is checked on its own, then
Discoverer runs against FakeClient and the real RepoStore on an in-memory
SQLite database.

description: Checks the search text built from the user's options and what Discoverer saves, counts and returns.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.models import Repo, RepoRef
from merge_hunt.pipeline.discovery import MAX_CANDIDATES, Discoverer, DiscoveryOptions, DiscoveryResult, SearchSort, search_query
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore
from merge_hunt.storage.tables import RepoRow

# --- Helpers ---

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
PRS_SINCE = NOW - timedelta(days=180)

def found(id: int, name: str) -> Repo:
    return Repo(id, "pallets", name, 300, 20, "Python", "MIT", (), 4, NOW - timedelta(days=1), None)

# Stands in for GitHubClient and keeps every search and count it was asked
# for. A repo missing from counts has a hundred pull requests, inside the
# default range.
class FakeClient:
    def __init__(self, repos: list[Repo], counts: dict[str, int]) -> None:
        self.repos = repos
        self.counts = counts
        self.searches: list[tuple[str, int]] = []
        self.counted: list[tuple[str, datetime]] = []

    async def search_repos(self, query, limit, on_wait=None) -> list[Repo]:
        self.searches.append((query, limit))
        return self.repos[:limit]

    async def count_pull_requests(self, owner, name, since, on_wait=None) -> int:
        self.counted.append((name, since))
        return self.counts.get(name, 100)

def run(body, repos: list[Repo], counts: dict[str, int] | None = None):
    async def main():
        engine = create_engine("sqlite+aiosqlite://")
        try:
            await create_tables(engine)
            fake = FakeClient(repos, counts or {})
            discoverer = Discoverer(fake, RepoStore(async_sessionmaker(engine)), clock=lambda: NOW)
            return await body(discoverer, fake, engine)
        finally:
            await engine.dispose()
    return asyncio.run(main())

# --- Search Text ---

def test_default_query():
    assert search_query(DiscoveryOptions(), NOW) == (
        "stars:200..5000 pushed:>=2026-07-31 archived:false fork:false good-first-issues:>=1 sort:stars-desc"
    )

def test_query_follows_sort():
    assert search_query(DiscoveryOptions(sort=SearchSort.UPDATED_DESC), NOW).endswith(" sort:updated-desc")

def test_query_with_every_filter():
    options = DiscoveryOptions(
        language="Jupyter Notebook", topic="cli", license="mit", min_good_first_issues=0, min_help_wanted_issues=2,
    )
    query = search_query(options, NOW)
    assert 'language:"Jupyter Notebook"' in query
    assert "topic:cli" in query and "license:mit" in query
    assert "help-wanted-issues:>=2" in query
    assert "good-first-issues" not in query

# --- Discoverer ---

def test_discover_saves_and_returns_refs():
    async def body(discoverer, fake, engine):
        result = await discoverer.discover(DiscoveryOptions(max_candidates=2), PRS_SINCE)
        async with engine.connect() as connection:
            saved = (await connection.execute(select(RepoRow.id, RepoRow.discovered_at).order_by(RepoRow.id))).all()
        return result, fake.searches, [tuple(row) for row in saved]

    result, searches, saved = run(body, [found(1, "click"), found(2, "flask"), found(3, "jinja")])
    assert result == DiscoveryResult(2, (RepoRef(1, "pallets", "click"), RepoRef(2, "pallets", "flask")))
    assert searches == [(search_query(DiscoveryOptions(max_candidates=2), NOW), 2)]
    assert saved == [(1, NOW), (2, NOW)]

# Both ends of the range are kept. The skipped repos are still saved, the
# search found them.
def test_discover_keeps_repos_within_pull_request_range():
    async def body(discoverer, fake, engine):
        result = await discoverer.discover(DiscoveryOptions(), PRS_SINCE)
        async with engine.connect() as connection:
            saved = (await connection.execute(select(RepoRow.id))).scalars().all()
        return result, fake.counted, saved

    repos = [found(1, "few"), found(2, "low"), found(3, "high"), found(4, "many")]
    result, counted, saved = run(body, repos, {"few": 29, "low": 30, "high": 500, "many": 501})
    assert result.found == 4
    assert [ref.name for ref in result.repos] == ["low", "high"]
    assert counted == [(repo.name, PRS_SINCE) for repo in repos]
    assert sorted(saved) == [1, 2, 3, 4]

def test_too_many_candidates_is_refused_before_searching():
    async def body(discoverer, fake, _):
        with pytest.raises(ValueError):
            await discoverer.discover(DiscoveryOptions(max_candidates=MAX_CANDIDATES + 1), PRS_SINCE)
        return fake.searches

    assert run(body, []) == []
