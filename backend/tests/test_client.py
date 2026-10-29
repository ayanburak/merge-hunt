"""
file: test_client.py

Tests for github/client.py. Requests never leave the machine, httpx's
MockTransport hands every request to a function in the test that decides the
answer. Waits are recorded by a fake sleep and take no time.

description: Checks paging, error handling and the wait and retry rules of GitHubClient.
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
from merge_hunt.github.queries import PAGE_SIZE
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
    dates = [SINCE + timedelta(days=170, minutes=-i) for i in range(PAGE_SIZE * MAX_PAGES)]
    pages = [page(dates[i:i + PAGE_SIZE], has_next=True) for i in range(0, len(dates), PAGE_SIZE)]
    handler, seen = scripted(*pages)
    history = run(handler, fetch)
    assert len(seen) == MAX_PAGES
    assert len(history.prs) == PAGE_SIZE * MAX_PAGES
    assert history.complete_since == dates[-1]

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
