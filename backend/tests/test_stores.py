"""
file: test_stores.py

Tests for the storage package. Every test gets a fresh SQLite database that
lives only in memory, nothing is written to disk.

description: Checks that RepoStore saves and loads repos and enrichment data without losing or leaving anything.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.models import (
    Actor,
    CloseSource,
    Commit,
    CommitHistory,
    Interaction,
    PullRequest,
    PullRequestHistory,
    Repo,
)
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore
from merge_hunt.storage.tables import CommitRow, InteractionRow, PullRequestRow, RepoRow

# --- Helpers ---

MEMORY = "sqlite+aiosqlite://"
REPO_ID = 254517
AT = datetime(2026, 10, 29, 12, tzinfo=UTC)
SINCE = AT - timedelta(days=180)

ALICE = Actor("alice", False)
MAYA = Actor("maya", False)

# One of every shape the data can take: merged with a review, closed by a
# commit with labels, closed by a bot, open with a comment from a deleted
# account. The commits reach less far back than asked, like after the page
# limit, so the two dates coming back the same proves both are stored.
def sample_prs() -> PullRequestHistory:
    day = timedelta(days=1)
    return PullRequestHistory(
        prs=(
            PullRequest(
                4, ALICE, "NONE", AT - 2 * day, merged_at=AT - day, closed_at=AT - day, closed_by=MAYA,
                interactions=(Interaction(MAYA, "MEMBER", AT - 1.5 * day), Interaction(Actor("bot", True), "NONE", AT - 1.2 * day)),
            ),
            PullRequest(
                3, ALICE, "CONTRIBUTOR", AT - 10 * day, closed_at=AT - 9 * day, closed_by=MAYA, closed_via=CloseSource.COMMIT,
                labels=("rejected AI", "docs"),
            ),
            PullRequest(2, Actor("dependabot", True), "NONE", AT - 20 * day, closed_at=AT - 5 * day, closed_by=Actor("stale", True)),
            PullRequest(1, ALICE, "FIRST_TIME_CONTRIBUTOR", AT - 30 * day, interactions=(Interaction(Actor("ghost", False), "NONE", AT - 29 * day),)),
        ),
        complete_since=SINCE,
        requested_since=SINCE,
    )

def sample_commits() -> CommitHistory:
    return CommitHistory(
        commits=(
            Commit(MAYA, AT - timedelta(hours=3)),
            Commit(Actor("dependabot[bot]", True), AT - timedelta(days=4)),
            Commit(Actor("ghost", True), AT - timedelta(days=8)),
        ),
        complete_since=AT - timedelta(days=80),
        requested_since=AT - timedelta(days=90),
    )

# Runs body with a store on a fresh in-memory database and throws the
# database away afterwards. body also gets the engine, for the tests that
# look at the tables directly.
def run(body):
    async def main():
        engine = create_engine(MEMORY)
        try:
            await create_tables(engine)
            return await body(RepoStore(async_sessionmaker(engine)), engine)
        finally:
            await engine.dispose()
    return asyncio.run(main())

async def count(engine, row_class) -> int:
    async with engine.connect() as connection:
        return await connection.scalar(select(func.count()).select_from(row_class))

async def save_sample(store: RepoStore) -> None:
    await store.save_repo(REPO_ID, "pallets", "click")
    await store.save_enrichment(REPO_ID, sample_prs(), sample_commits(), AT)

# --- Tests ---

def test_enrichment_round_trip():
    async def body(store, _):
        await save_sample(store)
        return await store.load_enrichment(REPO_ID)

    loaded = run(body)
    assert loaded.enriched_at == AT
    assert loaded.prs == sample_prs()
    assert loaded.commits == sample_commits()

def test_times_come_back_utc_aware():
    async def body(store, _):
        await save_sample(store)
        return await store.load_enrichment(REPO_ID)

    loaded = run(body)
    pr = loaded.prs.prs[0]
    times = [loaded.enriched_at, loaded.prs.complete_since, loaded.commits.complete_since,
             pr.created_at, pr.merged_at, pr.interactions[0].at, loaded.commits.commits[0].committed_at]
    assert all(value.tzinfo is UTC for value in times)

# The second fetch has a single pull request without interactions, so any
# interaction left in the table would be a leftover of the first one.
def test_saving_again_replaces_old_data():
    newer = PullRequestHistory((PullRequest(9, ALICE, "NONE", AT),), SINCE, SINCE)

    async def body(store, engine):
        await save_sample(store)
        await store.save_enrichment(REPO_ID, newer, CommitHistory((), SINCE, SINCE), AT + timedelta(days=7))
        loaded = await store.load_enrichment(REPO_ID)
        return loaded, await count(engine, PullRequestRow), await count(engine, InteractionRow), await count(engine, CommitRow)

    loaded, prs, interactions, commits = run(body)
    assert loaded.prs == newer
    assert loaded.enriched_at == AT + timedelta(days=7)
    assert (prs, interactions, commits) == (1, 0, 0)

def test_save_repo_updates_name():
    async def body(store, engine):
        await save_sample(store)
        await store.save_repo(REPO_ID, "pallets-eco", "click-renamed")
        async with engine.connect() as connection:
            rows = (await connection.execute(select(RepoRow.id, RepoRow.owner, RepoRow.name))).all()
        return [tuple(row) for row in rows], await store.load_enrichment(REPO_ID)

    rows, loaded = run(body)
    assert rows == [(REPO_ID, "pallets-eco", "click-renamed")]
    assert loaded.prs == sample_prs()

def test_unenriched_repo_loads_none():
    async def body(store, _):
        await store.save_repo(REPO_ID, "pallets", "click")
        return await store.load_enrichment(REPO_ID), await store.load_enrichment(999)

    assert run(body) == (None, None)

# Two pull requests with the same number break the unique rule half way
# through the write, after the old rows were already deleted in the same
# transaction.
def test_failed_save_keeps_old_data():
    broken = PullRequestHistory((PullRequest(5, ALICE, "NONE", AT), PullRequest(5, ALICE, "NONE", AT)), SINCE, SINCE)

    async def body(store, _):
        await save_sample(store)
        with pytest.raises(IntegrityError):
            await store.save_enrichment(REPO_ID, broken, CommitHistory((), SINCE, SINCE), AT + timedelta(days=7))
        return await store.load_enrichment(REPO_ID)

    loaded = run(body)
    assert loaded.enriched_at == AT
    assert loaded.prs == sample_prs()
    assert loaded.commits == sample_commits()

def test_tables_created_twice_is_safe():
    async def body(store, engine):
        await save_sample(store)
        await create_tables(engine)
        return await store.load_enrichment(REPO_ID)

    assert run(body).prs == sample_prs()

# --- Discovery ---

def found_click(stars: int = 16000) -> Repo:
    return Repo(REPO_ID, "pallets", "click", stars, 1500, "Python", "BSD-3-Clause", ("cli",), 112, AT - timedelta(days=1), None)

async def repo_row(engine) -> tuple:
    async with engine.connect() as connection:
        row = (await connection.execute(select(RepoRow.owner, RepoRow.stars, RepoRow.topics, RepoRow.discovered_at, RepoRow.enriched_at))).one()
    return tuple(row)

def test_save_repos_adds_discovery_fields():
    async def body(store, engine):
        await store.save_repos([found_click()], AT)
        return await repo_row(engine)

    assert run(body) == ("pallets", 16000, ["cli"], AT, None)

# A repo found again by a later search keeps the pull requests and commits
# fetched for it, only the discovery fields change.
def test_found_again_keeps_enrichment():
    async def body(store, engine):
        await save_sample(store)
        await store.save_repos([found_click(stars=16500)], AT + timedelta(days=1))
        return await repo_row(engine), await store.load_enrichment(REPO_ID)

    row, loaded = run(body)
    assert row == ("pallets", 16500, ["cli"], AT + timedelta(days=1), AT)
    assert loaded.prs == sample_prs()

# save_repo is what Enricher calls, it must not wipe what discovery saved.
def test_save_repo_keeps_discovery_fields():
    async def body(store, engine):
        await store.save_repos([found_click()], AT)
        await store.save_repo(REPO_ID, "pallets-eco", "click")
        return await repo_row(engine)

    assert run(body) == ("pallets-eco", 16000, ["cli"], AT, None)

def test_load_repo_round_trip():
    async def body(store, _):
        await store.save_repos([found_click()], AT)
        return await store.load_repo(REPO_ID)

    assert run(body) == found_click()

# A row only Enricher wrote has no discovery fields, and an unknown id has no row.
def test_load_repo_none_unless_discovered():
    async def body(store, _):
        await store.save_repo(REPO_ID, "pallets", "click")
        return await store.load_repo(REPO_ID), await store.load_repo(1)

    assert run(body) == (None, None)
