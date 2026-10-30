"""
file: test_client.py

Tests for github/client.py. Requests never leave the machine, httpx's
MockTransport hands every request to a function in the test that decides the
answer. Waits are recorded by a fake sleep and take no time.

description: Checks paging of pull requests, commits, issues and repo search, error handling and the wait and retry rules of GitHubClient.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from merge_hunt.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubQueryError,
    MAX_PAGES,
    RateLimitWait,
    WaitReason,
)
from merge_hunt.github.queries import COMMIT_PAGE_SIZE, ISSUE_PAGE_SIZE, PULL_REQUEST_PAGE_SIZE, SEARCH_PAGE_SIZE
from merge_hunt.github.token import StaticTokenProvider

# --- Helpers ---

SINCE = datetime(2026, 4, 1, tzinfo=UTC)

# Keeps every wait the client asked for instead of sleeping.
class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

def node(created_at: datetime) -> dict:
    return {
        "number": 1,
        "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        "mergedAt": None,
        "closedAt": None,
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "alice"},
        "comments": {"nodes": []},
        "reviews": {"nodes": []},
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
    }

def page(dates: list[datetime], has_next: bool, cursor: str = "next") -> httpx.Response:
    data = {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": [node(day) for day in dates],
    }}}
    return httpx.Response(200, json={"data": data})

def ok_page() -> httpx.Response:
    return page([SINCE + timedelta(days=10)], has_next=False)

# Answers with the given responses in order and keeps every request body.
def scripted(*responses):
    queue = list(responses)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return handler, seen

def run(handler, call, sleep: FakeSleep | None = None):
    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = GitHubClient(StaticTokenProvider(SecretStr("test-token")), http, sleep or FakeSleep())
            return await call(client)
    return asyncio.run(main())

def fetch(client: GitHubClient, on_wait=None):
    return client.pull_requests("owner", "repo", SINCE, on_wait)

# --- Paging ---

def test_stops_at_first_page_older_than_since():
    handler, seen = scripted(
        page([SINCE + timedelta(days=50), SINCE + timedelta(days=40)], has_next=True, cursor="c1"),
        page([SINCE + timedelta(days=5), SINCE - timedelta(days=5)], has_next=True, cursor="c2"),
    )
    history = run(handler, fetch)
    assert len(seen) == 2
    assert seen[0]["variables"]["cursor"] is None
    assert seen[1]["variables"]["cursor"] == "c1"
    assert len(history.prs) == 3
    assert history.complete_since == SINCE

def test_short_history_is_complete():
    handler, seen = scripted(page([SINCE + timedelta(days=3)], has_next=False))
    history = run(handler, fetch)
    assert len(seen) == 1
    assert history.complete_since == SINCE

# Every page is full and still inside the window, so only the page limit stops it.
def test_page_limit_marks_where_data_is_complete():
    dates = [SINCE + timedelta(days=170, minutes=-i) for i in range(PULL_REQUEST_PAGE_SIZE * MAX_PAGES)]
    pages = [page(dates[i:i + PULL_REQUEST_PAGE_SIZE], has_next=True) for i in range(0, len(dates), PULL_REQUEST_PAGE_SIZE)]
    handler, seen = scripted(*pages)
    history = run(handler, fetch)
    assert len(seen) == MAX_PAGES
    assert len(history.prs) == PULL_REQUEST_PAGE_SIZE * MAX_PAGES
    assert history.complete_since == dates[-1]
    assert history.requested_since == SINCE

# --- Commits ---

def commit_node(committed_at: datetime, parents: int = 1) -> dict:
    return {
        "committedDate": committed_at.isoformat().replace("+00:00", "Z"),
        "parents": {"totalCount": parents},
        "author": {"name": "alice", "user": {"login": "alice"}},
    }

# nodes is a list of (date, parent count) pairs, newest first like GitHub.
def commit_page(nodes: list[tuple[datetime, int]], has_next: bool, cursor: str = "next") -> httpx.Response:
    history = {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": [commit_node(day, parents) for day, parents in nodes],
    }
    data = {"repository": {"defaultBranchRef": {"target": {"history": history}}}}
    return httpx.Response(200, json={"data": data})

def fetch_commits(client: GitHubClient):
    return client.commits("owner", "repo", SINCE)

def test_commits_skip_merge_commits():
    handler, _ = scripted(commit_page([(SINCE + timedelta(days=9), 2), (SINCE + timedelta(days=8), 1)], has_next=False))
    history = run(handler, fetch_commits)
    assert [commit.committed_at for commit in history.commits] == [SINCE + timedelta(days=8)]

def test_commits_follow_pages_to_the_end():
    handler, seen = scripted(
        commit_page([(SINCE + timedelta(days=20), 1)], has_next=True, cursor="c1"),
        commit_page([(SINCE + timedelta(days=2), 1)], has_next=False),
    )
    history = run(handler, fetch_commits)
    assert len(seen) == 2
    assert seen[0]["variables"]["since"] == SINCE.isoformat()
    assert seen[0]["variables"]["pageSize"] == COMMIT_PAGE_SIZE
    assert seen[0]["variables"]["cursor"] is None
    assert seen[1]["variables"]["cursor"] == "c1"
    assert len(history.commits) == 2
    assert history.complete_since == SINCE

# The last commit of the last allowed page is a merge commit. It is not kept,
# but the data still reaches back to it.
def test_commits_page_limit_counts_skipped_commits():
    dates = [SINCE + timedelta(days=80, minutes=-i) for i in range(COMMIT_PAGE_SIZE * MAX_PAGES)]
    nodes = [(day, 2 if day == dates[-1] else 1) for day in dates]
    pages = [commit_page(nodes[i:i + COMMIT_PAGE_SIZE], has_next=True) for i in range(0, len(nodes), COMMIT_PAGE_SIZE)]
    handler, seen = scripted(*pages)
    history = run(handler, fetch_commits)
    assert len(seen) == MAX_PAGES
    assert len(history.commits) == COMMIT_PAGE_SIZE * MAX_PAGES - 1
    assert history.complete_since == dates[-1]
    assert history.requested_since == SINCE

def test_repo_without_default_branch_has_no_commits():
    handler, _ = scripted(httpx.Response(200, json={"data": {"repository": {"defaultBranchRef": None}}}))
    history = run(handler, fetch_commits)
    assert history.commits == ()
    assert history.complete_since == SINCE

# --- Search ---

def repo_node(number: int) -> dict:
    return {
        "databaseId": number,
        "name": f"repo{number}",
        "owner": {"login": "owner"},
        "stargazerCount": 300,
        "forkCount": 20,
        "pushedAt": "2026-10-20T00:00:00Z",
        "primaryLanguage": None,
        "licenseInfo": None,
        "repositoryTopics": {"nodes": []},
        "issues": {"totalCount": 4},
        "latestRelease": None,
    }

def search_page(numbers: range, has_next: bool, cursor: str = "next") -> httpx.Response:
    data = {"search": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": [repo_node(number) for number in numbers],
    }}
    return httpx.Response(200, json={"data": data})

# Thirty repos are asked for, the second page only asks for the five missing.
def test_search_stops_at_limit():
    handler, seen = scripted(search_page(range(25), has_next=True, cursor="s1"), search_page(range(25, 30), has_next=True))
    repos = run(handler, lambda client: client.search_repos("language:Python", 30))
    assert [item["variables"]["pageSize"] for item in seen] == [SEARCH_PAGE_SIZE, 5]
    assert seen[1]["variables"]["cursor"] == "s1"
    assert seen[0]["variables"]["query"] == "language:Python"
    assert [repo.id for repo in repos] == list(range(30))

def test_search_stops_when_results_run_out():
    handler, seen = scripted(search_page(range(3), has_next=False))
    repos = run(handler, lambda client: client.search_repos("language:Python", 100))
    assert len(seen) == 1
    assert len(repos) == 3

def test_count_pull_requests_asks_for_the_day():
    handler, seen = scripted(httpx.Response(200, json={"data": {"search": {"issueCount": 42}}}))
    count = run(handler, lambda client: client.count_pull_requests("pallets", "click", SINCE + timedelta(hours=13)))
    assert count == 42
    assert seen[0]["variables"]["query"] == "repo:pallets/click is:pr created:>=2026-04-01"

# --- Errors ---

def test_graphql_errors_raise_query_error():
    handler, _ = scripted(httpx.Response(200, json={"data": {"repository": None}, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}]}))
    with pytest.raises(GitHubQueryError, match="Could not resolve"):
        run(handler, fetch)

def test_rejected_token_raises_auth_error():
    handler, _ = scripted(httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(GitHubAuthError):
        run(handler, fetch)

def test_plain_forbidden_is_not_retried():
    handler, seen = scripted(httpx.Response(403, json={"message": "Resource not accessible"}))
    with pytest.raises(httpx.HTTPStatusError):
        run(handler, fetch)
    assert len(seen) == 1

# --- Waiting ---

def test_used_up_quota_waits_until_reset():
    reset = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=20)
    used_up = httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset.timestamp()))})
    handler, _ = scripted(used_up, ok_page())
    sleep, waits = FakeSleep(), []

    async def on_wait(wait: RateLimitWait) -> None:
        waits.append(wait)

    run(handler, lambda client: fetch(client, on_wait), sleep)
    assert waits == [RateLimitWait(WaitReason.RATE_LIMIT, reset, 1)]
    assert 1190 < sleep.calls[0] <= 1201

def test_rate_limited_error_in_body_waits_too():
    reset = datetime.now(UTC) + timedelta(minutes=5)
    used_up = httpx.Response(
        200,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(reset.timestamp()))},
        json={"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
    )
    handler, seen = scripted(used_up, ok_page())
    run(handler, fetch)
    assert len(seen) == 2

def test_secondary_limit_uses_retry_after():
    handler, _ = scripted(httpx.Response(429, headers={"retry-after": "30"}), ok_page())
    sleep = FakeSleep()
    run(handler, fetch, sleep)
    assert 29 < sleep.calls[0] <= 31

def test_secondary_limit_backs_off_then_gives_up():
    limited = httpx.Response(403, json={"message": "You have exceeded a secondary rate limit."})
    handler, seen = scripted(*[limited] * 4)
    sleep = FakeSleep()
    with pytest.raises(httpx.HTTPStatusError):
        run(handler, fetch, sleep)
    assert len(seen) == 4
    assert [round(s) for s in sleep.calls] == [61, 121, 241]

def test_server_errors_retry_with_short_waits():
    handler, seen = scripted(httpx.Response(502), httpx.Response(504), ok_page())
    sleep = FakeSleep()
    history = run(handler, fetch, sleep)
    assert len(seen) == 3
    assert [round(s) for s in sleep.calls] == [3, 5]
    assert len(history.prs) == 1

def test_timeout_is_retried():
    handler, seen = scripted(httpx.ReadTimeout("slow"), ok_page())
    run(handler, fetch)
    assert len(seen) == 2

def test_wait_is_logged_without_listener(caplog):
    handler, _ = scripted(httpx.Response(503), ok_page())
    with caplog.at_level(logging.WARNING, logger="merge_hunt.github.client"):
        run(handler, fetch)
    assert "SERVER_ERROR" in caplog.text

# --- Order ---

# Two calls are started together, the handler counts how many requests are in
# flight at the same moment.
def test_requests_go_out_one_at_a_time():
    active = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ok_page()

    async def both(client: GitHubClient):
        await asyncio.gather(fetch(client), fetch(client))

    run(handler, both)
    assert peak == 1

# --- Issues ---

def issue_page(number: int, has_next: bool, cursor: str = "next") -> httpx.Response:
    item = {
        "number": number,
        "title": "Fix it",
        "bodyText": "",
        "createdAt": "2026-01-01T10:00:00Z",
        "updatedAt": "2026-02-01T10:00:00Z",
        "author": None,
        "labels": {"nodes": []},
        "assignees": {"totalCount": 0},
        "closedByPullRequestsReferences": {"totalCount": 0},
        "comments": {"totalCount": 0, "nodes": []},
    }
    data = {"repository": {"issues": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": [item]}}}
    return httpx.Response(200, json={"data": data})

def test_issues_follow_pages_and_send_labels():
    handler, seen = scripted(issue_page(1, True, "c1"), issue_page(2, False))
    issues = run(handler, lambda client: client.issues("owner", "repo", ("help wanted", "easy")))
    assert [issue.number for issue in issues] == [1, 2]
    assert seen[0]["variables"]["issueLabels"] == ["help wanted", "easy"]
    assert seen[0]["variables"]["pageSize"] == ISSUE_PAGE_SIZE
    assert seen[0]["variables"]["cursor"] is None and seen[1]["variables"]["cursor"] == "c1"

def test_issues_stop_at_page_limit():
    handler, seen = scripted(*[issue_page(number, True) for number in range(MAX_PAGES + 1)])
    assert len(run(handler, lambda client: client.issues("owner", "repo", ("easy",)))) == MAX_PAGES
    assert len(seen) == MAX_PAGES
