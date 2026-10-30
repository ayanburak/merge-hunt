"""
file: test_issues.py

Tests for domain/issues.py, pipeline/issue_analysis.py, pipeline/issue_finder.py
and IssueStore. The finder gets a fake client and the real IssueStore on an
in-memory SQLite database, so nothing reaches GitHub or the disk.

description: Checks the issue filter, the claim rule, saving issues and the rules of IssueFinder.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from merge_hunt.domain.issues import FeedbackValue, IssueFilter, is_shown
from merge_hunt.domain.models import Actor, Issue, IssueComment, RepoRef
from merge_hunt.github.client import GitHubAuthError, GitHubQueryError
from merge_hunt.pipeline.issue_analysis import CLAIM_MAX_LENGTH, RuleIssueAnalyzer
from merge_hunt.pipeline.issue_finder import IssueFetchError, IssueFinder
from merge_hunt.storage.database import create_engine, create_tables
from merge_hunt.storage.stores import IssueStore, RepoNotFoundError, RepoStore
from merge_hunt.storage.tables import IssueCommentRow, IssueFeedbackRow, IssueRow

# --- Helpers ---

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
ALICE = Actor("alice", False)
CLICK = RepoRef(1, "pallets", "click")
FLASK = RepoRef(2, "pallets", "flask")

def make_issue(number: int = 1, **fields) -> Issue:
    base = dict(
        number=number, title="Fix it", body="text", author=ALICE, created_at=NOW - timedelta(days=30), updated_at=NOW,
        labels=("help wanted",), assigned=False, linked_prs=0, comment_count=0,
    )
    return Issue(**(base | fields))

def comment(body: str, at: datetime = NOW, author: Actor = ALICE) -> IssueComment:
    return IssueComment(author, "NONE", at, body)

def claimed(*bodies: str):
    issue = make_issue(comments=tuple(comment(body) for body in bodies))
    return asyncio.run(RuleIssueAnalyzer().analyze(issue)).claimed_at is not None

# Hands out issues per repo name, an exception in the map is raised instead.
class FakeClient:
    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.calls: list = []

    async def issues(self, owner, name, labels, on_wait=None):
        self.calls.append((name, tuple(labels)))
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer

# Gives body a database with both repos saved, the store and the session maker.
def run(body):
    async def main():
        engine = create_engine("sqlite+aiosqlite://")
        try:
            await create_tables(engine)
            sessions = async_sessionmaker(engine)
            repos = RepoStore(sessions)
            await repos.save_repo(CLICK.id, CLICK.owner, CLICK.name)
            await repos.save_repo(FLASK.id, FLASK.owner, FLASK.name)
            return await body(IssueStore(sessions), sessions)
        finally:
            await engine.dispose()
    return asyncio.run(main())

def finder_for(store: IssueStore, answers: dict) -> IssueFinder:
    return IssueFinder(FakeClient(answers), store, RuleIssueAnalyzer(), clock=lambda: NOW)

# --- Filter ---

@pytest.mark.parametrize("issue, shown", [
    (make_issue(), True),
    (make_issue(assigned=True), False),
    (make_issue(linked_prs=1), False),
])
def test_default_filter_hides_taken_issues(issue, shown):
    assert is_shown(issue, IssueFilter(), NOW) is shown

def test_filter_can_show_taken_issues():
    options = IssueFilter(hide_assigned=False, hide_linked=False)
    assert is_shown(make_issue(assigned=True, linked_prs=2), options, NOW)

def test_no_age_limit_by_default():
    assert is_shown(make_issue(created_at=NOW - timedelta(days=4000)), IssueFilter(), NOW)

def test_age_limit_includes_its_last_day():
    options = IssueFilter(max_age_days=30)
    assert is_shown(make_issue(created_at=NOW - timedelta(days=30)), options, NOW)
    assert not is_shown(make_issue(created_at=NOW - timedelta(days=31)), options, NOW)

# --- Claim Rule ---

@pytest.mark.parametrize("text", [
    "I'd like to work on this, thanks!",
    "I’m going to start working on this issue",
    "I'm on it",
    "Can I take this one?",
    "Please assign this to me",
    "Is anyone working on this?",
])
def test_claim_is_found(text):
    assert claimed(text)

# The first two were real false matches, words that hold im and ill inside them.
@pytest.mark.parametrize("text", [
    "I think we can use manim, but we would have more work than creating images",
    "It still does not work on my machine",
    "This is a duplicate of the other report",
])
def test_ordinary_comment_is_no_claim(text):
    assert not claimed(text)

def test_long_comment_is_no_claim():
    assert not claimed("I'd like to work on this. " + "x" * CLAIM_MAX_LENGTH)

def test_bot_comment_is_no_claim():
    issue = make_issue(comments=(comment("I will work on this", author=Actor("helper", True)),))
    assert asyncio.run(RuleIssueAnalyzer().analyze(issue)).claimed_at is None

def test_latest_claim_wins():
    old, new = NOW - timedelta(days=90), NOW - timedelta(days=2)
    issue = make_issue(comments=(comment("I'll fix it", old), comment("I can't, sorry"), comment("Let me take it", new)))
    assert asyncio.run(RuleIssueAnalyzer().analyze(issue)).claimed_at == new

# --- IssueStore ---

def test_store_gives_back_what_was_saved_in_order():
    saved = (make_issue(5, comments=(comment("one"), comment("two")), comment_count=7), make_issue(3))

    async def body(store, _):
        await store.save(CLICK.id, saved, ("easy",), NOW)
        return await store.load(CLICK.id)

    fetch = run(body)
    assert fetch.fetched_at == NOW and fetch.issues == saved and fetch.labels == ("easy",)

def test_store_replaces_the_last_fetch_and_its_comments():
    async def body(store, sessions):
        await store.save(CLICK.id, [make_issue(5, comments=(comment("one"),))], (), NOW)
        await store.save(CLICK.id, [make_issue(6)], (), NOW + timedelta(hours=1))
        async with sessions() as session:
            counts = await session.scalar(select(func.count()).select_from(IssueRow)), await session.scalar(select(func.count()).select_from(IssueCommentRow))
        return await store.load(CLICK.id), counts

    fetch, counts = run(body)
    assert [issue.number for issue in fetch.issues] == [6] and fetch.fetched_at == NOW + timedelta(hours=1)
    assert counts == (1, 0)

def test_store_tells_empty_from_never_fetched():
    async def body(store, _):
        await store.save(CLICK.id, [], (), NOW)
        return await store.load(CLICK.id), await store.load(FLASK.id)

    empty, never = run(body)
    assert empty.issues == () and never is None

def test_store_refuses_unknown_repo():
    async def body(store, _):
        with pytest.raises(RepoNotFoundError):
            await store.save(99, [], (), NOW)

    run(body)

# --- IssueFinder ---

def test_find_saves_each_repo_and_reports_it():
    async def body(store, _):
        finder = finder_for(store, {"click": (make_issue(1),), "flask": ()})
        seen = []

        async def on_progress(result):
            seen.append(result)

        await finder.find([CLICK, FLASK], ("easy",), on_progress)
        return seen, await store.load(CLICK.id), await store.load(FLASK.id), finder._client.calls

    seen, click, flask, calls = run(body)
    assert [(item.repo, item.error) for item in seen] == [(CLICK, None), (FLASK, None)]
    assert len(click.issues) == 1 and flask.issues == ()
    assert calls == [("click", ("easy",)), ("flask", ("easy",))]

@pytest.mark.parametrize("error", [GitHubQueryError("gone"), httpx.ConnectError("lost")])
def test_failed_repo_does_not_stop_the_rest(error):
    async def body(store, _):
        finder = finder_for(store, {"click": error, "flask": (make_issue(1),)})
        seen = []

        async def on_progress(result):
            seen.append(result)

        await finder.find([CLICK, FLASK], ("easy",), on_progress)
        return seen, await store.load(CLICK.id), await store.load(FLASK.id)

    seen, click, flask = run(body)
    assert seen[0].error.startswith("Issues could not be fetched") and seen[1].error is None
    assert click is None and len(flask.issues) == 1

def test_rejected_token_stops_find():
    async def body(store, _):
        finder = finder_for(store, {"click": GitHubAuthError("bad"), "flask": ()})
        with pytest.raises(GitHubAuthError):
            await finder.find([CLICK, FLASK], ("easy",))
        return finder._client.calls

    assert len(run(body)) == 1

@pytest.mark.parametrize("error", [GitHubAuthError("bad"), GitHubQueryError("gone"), httpx.ConnectError("lost")])
def test_refresh_wraps_every_fetch_error(error):
    async def body(store, _):
        with pytest.raises(IssueFetchError):
            await finder_for(store, {"click": error}).refresh(CLICK)

    run(body)

def test_load_filters_analyzes_and_keeps_order():
    issues = (make_issue(1, comments=(comment("I'll take it"),)), make_issue(2, assigned=True), make_issue(3))

    async def body(store, _):
        await store.save(CLICK.id, issues, ("easy",), NOW)
        return await finder_for(store, {}).load(CLICK.id, IssueFilter())

    answer = run(body)
    assert answer.fetched_at == NOW and answer.labels == ("easy",)
    assert [item.issue.number for item in answer.issues] == [1, 3]
    assert answer.issues[0].analysis.claimed_at == NOW and answer.issues[1].analysis.claimed_at is None

def test_load_of_never_fetched_repo_has_no_time():
    async def body(store, _):
        return await finder_for(store, {}).load(CLICK.id, IssueFilter())

    answer = run(body)
    assert answer.fetched_at is None and answer.issues == ()

def test_refresh_keeps_the_labels_of_the_last_fetch():
    async def body(store, _):
        finder = finder_for(store, {"click": ()})
        await finder.find([CLICK], ("easy", "docs"))
        await finder.refresh(CLICK)
        return finder._client.calls

    calls = run(body)
    assert calls[0] == calls[1] == ("click", ("easy", "docs"))

def test_refresh_of_never_fetched_repo_uses_the_defaults():
    async def body(store, _):
        finder = finder_for(store, {"click": ()})
        await finder.refresh(CLICK)
        return finder._client.calls

    assert run(body) == [("click", ("good first issue", "help wanted"))]

# --- Feedback ---

def test_skipped_issue_is_hidden_until_asked_for():
    async def body(store, _):
        await store.save(CLICK.id, [make_issue(1), make_issue(2)], (), NOW)
        finder = finder_for(store, {})
        assert await finder.give_feedback(CLICK.id, 1, FeedbackValue.SKIPPED)
        assert await finder.give_feedback(CLICK.id, 2, FeedbackValue.INTERESTED)
        return await finder.load(CLICK.id, IssueFilter()), await finder.load(CLICK.id, IssueFilter(show_skipped=True))

    hidden, shown = run(body)
    assert [(item.issue.number, item.feedback) for item in hidden.issues] == [(2, FeedbackValue.INTERESTED)]
    assert [item.feedback for item in shown.issues] == [FeedbackValue.SKIPPED, FeedbackValue.INTERESTED]

def test_feedback_on_unknown_issue_is_refused():
    async def body(store, _):
        return await finder_for(store, {}).give_feedback(CLICK.id, 7, FeedbackValue.SKIPPED)

    assert run(body) is False

# The whole point of the copy, the issue row is gone and the text must not be.
def test_feedback_keeps_a_copy_after_the_issue_is_gone():
    async def body(store, sessions):
        await store.save(CLICK.id, [make_issue(1, title="Fix docs", comments=(comment("one"),))], (), NOW)
        await store.save_feedback(CLICK.id, 1, FeedbackValue.INTERESTED, NOW)
        await store.save(CLICK.id, [], (), NOW)
        async with sessions() as session:
            return await session.scalar(select(IssueFeedbackRow)), await store.feedback(CLICK.id)

    row, feedback = run(body)
    assert row.issue["title"] == "Fix docs" and row.issue["comments"][0]["body"] == "one"
    assert row.issue["created_at"].startswith("2026-09") and feedback == {1: FeedbackValue.INTERESTED}

def test_second_feedback_replaces_the_first_and_can_be_taken_back():
    async def body(store, sessions):
        await store.save(CLICK.id, [make_issue(1)], (), NOW)
        await store.save_feedback(CLICK.id, 1, FeedbackValue.INTERESTED, NOW)
        await store.save_feedback(CLICK.id, 1, FeedbackValue.SKIPPED, NOW)
        replaced = await store.feedback(CLICK.id)
        await store.delete_feedback(CLICK.id, 1)
        await store.delete_feedback(CLICK.id, 1)
        return replaced, await store.feedback(CLICK.id)

    replaced, after = run(body)
    assert replaced == {1: FeedbackValue.SKIPPED} and after == {}

# --- Free Counts ---

def test_free_counts_skip_taken_issues_and_never_fetched_repos():
    issues = [make_issue(1), make_issue(2, assigned=True), make_issue(3, linked_prs=1), make_issue(4)]

    async def body(store, _):
        await store.save(CLICK.id, issues, (), NOW)
        await store.save_feedback(CLICK.id, 4, FeedbackValue.SKIPPED, NOW)
        return await store.free_counts([CLICK.id, FLASK.id])

    assert run(body) == {CLICK.id: 1}

def test_free_counts_tell_zero_from_never_fetched():
    async def body(store, _):
        await store.save(CLICK.id, [make_issue(1, assigned=True)], (), NOW)
        return await store.free_counts([CLICK.id, FLASK.id])

    assert run(body) == {CLICK.id: 0}
