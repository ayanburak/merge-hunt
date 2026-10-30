"""
file: test_scorer.py

Tests for pipeline/scoring.py. Scorer runs against a real RepoStore on an
in-memory SQLite database, the scoring itself is covered by test_scoring.py,
so these only check what Scorer adds: reading, skipping and sorting.

description: Checks that Scorer reads the saved repos, leaves out the ones it can not score and sorts the rest.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.models import Actor, Commit, CommitHistory, PullRequest, PullRequestHistory, Repo
from merge_hunt.domain.scoring import FilterProfile
from merge_hunt.pipeline.scoring import Scorer
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import RepoStore

# --- Helpers ---

MEMORY = "sqlite+aiosqlite://"
NOW = datetime(2026, 10, 1, tzinfo=UTC)
SINCE = NOW - timedelta(days=180)

AUTHOR = Actor("alice", False)
MAINTAINER = Actor("maya", False)

def repo(id: int) -> Repo:
    return Repo(id, "owner", f"repo{id}", 1000, 100, "Python", "MIT", (), 10, NOW, None)

# The first merged ones of twelve newcomer pull requests are merged and the rest rejected,
# all answered after two days, with thirty commits.
async def save(store: RepoStore, id: int, merged: int) -> None:
    opened = NOW - timedelta(days=60)
    closed = opened + timedelta(days=2)
    prs = tuple(
        PullRequest(n, AUTHOR, "NONE", opened, merged_at=closed if n < merged else None, closed_at=closed, closed_by=MAINTAINER)
        for n in range(12)
    )
    commits = tuple(Commit(MAINTAINER, NOW - timedelta(days=1)) for _ in range(30))
    await store.save_repos([repo(id)], NOW)
    await store.save_enrichment(id, PullRequestHistory(prs, SINCE, SINCE), CommitHistory(commits, SINCE, SINCE), NOW)

def run(body):
    async def main():
        engine = create_engine(MEMORY)
        try:
            await create_tables(engine)
            return await body(RepoStore(async_sessionmaker(engine)))
        finally:
            await engine.dispose()
    return asyncio.run(main())

# --- Tests ---

# Repo 2 merges less than half and fails, it goes last although it was asked
# for first. Repo 3 beats repo 1 on merge rate.
def test_passed_first_then_by_score():
    async def body(store):
        await save(store, 1, merged=9)
        await save(store, 2, merged=4)
        await save(store, 3, merged=12)
        return await Scorer(store, clock=lambda: NOW).score([2, 1, 3], FilterProfile())

    results = run(body)
    assert [item.repo.id for item in results] == [3, 1, 2]
    assert [item.passed for item in results] == [True, True, False]

def test_unknown_undiscovered_and_unenriched_repos_left_out():
    async def body(store):
        await save(store, 1, merged=12)
        await store.save_repo(2, "owner", "only-enriched-row")
        await store.save_repos([repo(3)], NOW)
        return await Scorer(store, clock=lambda: NOW).score([1, 2, 3, 4], FilterProfile())

    assert [item.repo.id for item in run(body)] == [1]

def test_clock_sets_the_window():
    async def body(store):
        await save(store, 1, merged=12)
        later = NOW + timedelta(days=365)
        return await Scorer(store, clock=lambda: later).score([1], FilterProfile())

    result = run(body)[0]
    assert result.metrics.accepted == 0 and result.commit_count == 0

# save keeps 180 days, so a 365 day window asks for more than there is.
def test_window_longer_than_the_saved_data_is_flagged():
    async def body(store):
        await save(store, 1, merged=9)
        scorer = Scorer(store, clock=lambda: NOW)
        wide = replace(FilterProfile(), metrics=MetricOptions(window_days=365))
        return (await scorer.score([1], FilterProfile()))[0], (await scorer.score([1], wide))[0]

    normal, wide = run(body)
    assert normal.window_covered is True
    assert wide.window_covered is False and wide.metrics.accepted == 9
