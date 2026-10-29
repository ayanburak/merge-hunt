"""
file: test_discovery.py

Tests for pipeline/discovery.py. search_query is checked on its own, then
Discoverer runs against FakeClient and the real RepoStore on an in-memory
SQLite database.

description: Checks the search text built from the user's options and what Discoverer saves and returns.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.models import Repo, RepoRef
from merge_hunt.pipeline.discovery import MAX_CANDIDATES, Discoverer, DiscoveryOptions, search_query
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore
from merge_hunt.storage.tables import RepoRow

# --- Helpers ---

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)

def found(id: int, name: str) -> Repo:
    return Repo(id, "pallets", name, 300, 20, "Python", "MIT", (), 4, NOW - timedelta(days=1), None)

# Stands in for GitHubClient and keeps every search it was asked to run.
class FakeClient:
    def __init__(self, repos: list[Repo]) -> None:
        self.repos = repos
        self.searches: list[tuple[str, int]] = []

    async def search_repos(self, query, limit, on_wait=None) -> list[Repo]:
        self.searches.append((query, limit))
        return self.repos[:limit]

def run(body, repos: list[Repo]):
    async def main():
        engine = create_engine("sqlite+aiosqlite://")
        try:
            await create_tables(engine)
            fake = FakeClient(repos)
            discoverer = Discoverer(fake, RepoStore(async_sessionmaker(engine)), clock=lambda: NOW)
            return await body(discoverer, fake, engine)
        finally:
            await engine.dispose()
    return asyncio.run(main())

# --- Search Text ---

def test_default_query():
    assert search_query(DiscoveryOptions(), NOW) == (
        "stars:200..5000 pushed:>=2026-07-31 archived:false fork:false good-first-issues:>=1 sort:updated-desc"
    )

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
        refs = await discoverer.discover(DiscoveryOptions(max_candidates=2))
        async with engine.connect() as connection:
            saved = (await connection.execute(select(RepoRow.id, RepoRow.discovered_at).order_by(RepoRow.id))).all()
        return refs, fake.searches, [tuple(row) for row in saved]

    refs, searches, saved = run(body, [found(1, "click"), found(2, "flask"), found(3, "jinja")])
    assert refs == [RepoRef(1, "pallets", "click"), RepoRef(2, "pallets", "flask")]
    assert searches == [(search_query(DiscoveryOptions(max_candidates=2), NOW), 2)]
    assert saved == [(1, NOW), (2, NOW)]

def test_too_many_candidates_is_refused_before_searching():
    async def body(discoverer, fake, _):
        with pytest.raises(ValueError):
            await discoverer.discover(DiscoveryOptions(max_candidates=MAX_CANDIDATES + 1))
        return fake.searches

    assert run(body, []) == []
