"""
file: client.py
importable: GitHubClient, RateLimit, RateLimitWait, WaitReason, GitHubAuthError, GitHubQueryError, HTTP_TIMEOUT

GitHubClient is the only place in the app that talks to GitHub. It is built
with a TokenProvider and an httpx.AsyncClient, both passed in from outside.
The caller owns the httpx client and closes it, GitHubClient never does.
It should be opened with timeout=HTTP_TIMEOUT, httpx's own default is too
short for a GraphQL page.
rate_limit() reports the remaining request quota and does not use any of it,
so it is also the cheapest way to check that a token works.

pull_requests(owner, name, since) fetches a repo's pull requests opened since
the given time, newest first, and returns them as a PullRequestHistory from
domain/models.py. When
the page limit stops it early, complete_since tells from which date on the
data is complete.

commits(owner, name, since) does the same for the commits on the default
branch and returns a CommitHistory. Merge commits are left out, bot commits
stay in and are only marked.

Requests go out one at a time. When GitHub says the quota is used up, asks to
slow down or fails for a moment, the client waits and tries again by itself.
Before each wait it calls the on_wait function given to pull_requests or
commits with a RateLimitWait, or logs a warning if there is none.

description: Async client for the GitHub API.
"""

# TODO: Search is not here yet, it comes with the discovery step.
#
# NOTE: A waiting request keeps the lock while it sleeps, so every other call
# on the same client waits too. That is on purpose, the quota is shared and a
# second request would only run into the same limit.
#
# XXX: The retry count is shared by all three kinds of waits within a single
# request. A request that first hits a server error and then the secondary
# limit has fewer tries left for the second one.

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import httpx

from merge_hunt.domain.models import Commit, CommitHistory, PullRequest, PullRequestHistory
from merge_hunt.github.parsing import parse_commit, parse_pull_request
from merge_hunt.github.queries import (
    COMMIT_PAGE_SIZE,
    COMMITS_QUERY,
    INTERACTION_LIMIT,
    LABEL_LIMIT,
    PULL_REQUEST_PAGE_SIZE,
    PULL_REQUESTS_QUERY,
)
from merge_hunt.github.token import TokenProvider

logger = logging.getLogger(__name__)

# --- Constants ---

API_URL = "https://api.github.com"

# GitHub asks clients to pin the API version, without it a future default
# version could change response shapes under us.
API_VERSION = "2022-11-28"

# Seconds. A page of fifty pull requests took about five seconds when it was
# measured, exactly httpx's default limit. GitHub itself gives up on a query
# after ten seconds, so thirty leaves plenty of room and never cuts a request
# GitHub would still have answered.
HTTP_TIMEOUT = 30.0

# Ten pages of fifty is five hundred pull requests and about twenty points,
# for commits it is a thousand commits and ten points. A repo with more in its
# window is bigger than the ones this app looks for.
MAX_PAGES = 10

# Three waits per request, then the error goes up to the caller. Retrying
# forever can get the token blocked by GitHub.
MAX_RETRIES = 3

_SERVER_ERRORS = frozenset({502, 503, 504})

# --- Errors ---

# A rejected token is its own error type because the fix is always the same,
# the user has to check the token, and the UI can say exactly that.
class GitHubAuthError(Exception):
    """GitHub rejected the token, it is wrong, expired or revoked."""

# GraphQL answers errors with status 200 and an errors list in the body, so
# raise_for_status does not catch them. Partial data that may come with the
# errors is never used.
class GitHubQueryError(Exception):
    """GitHub could not answer a GraphQL query, for example the repo does not exist or is private."""

# --- Models ---

# Lives here and not in domain because quota is a GitHub detail, the business
# logic should never need to know about it.
@dataclass(frozen=True)
class RateLimit:
    limit: int
    remaining: int
    reset_at: datetime

# A server error is not a quota limit, but for whoever shows the wait it means
# the same thing, GitHub is not answering now and the scan is waiting.
class WaitReason(StrEnum):
    RATE_LIMIT = "RATE_LIMIT"
    SECONDARY_LIMIT = "SECONDARY_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"

# Sent to on_wait right before the client sleeps. attempt starts at one.
@dataclass(frozen=True, slots=True)
class RateLimitWait:
    reason: WaitReason
    until: datetime
    attempt: int

WaitListener = Callable[[RateLimitWait], Awaitable[None]]

# --- Waiting ---

# GraphQL can run out of quota with status 200 and a RATE_LIMITED error in the
# body instead of a 403, so the body is looked at too.
def _says_rate_limited(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    errors = body.get("errors") if isinstance(body, dict) else None
    return any(error.get("type") == "RATE_LIMITED" for error in errors or ())

# Decides whether a response is worth waiting for and until when. The rules
# follow GitHub's own advice. When the quota is gone the reset header says
# when it comes back. For the secondary limit retry-after is used when GitHub
# sends it, otherwise one, two and then four minutes. A 403 without any sign
# of a limit is a real permission error and is not retried.
def _wait_for(response: httpx.Response, attempt: int, now: datetime) -> RateLimitWait | None:
    headers = response.headers
    status = response.status_code
    if headers.get("x-ratelimit-remaining") == "0" and (status in (403, 429) or _says_rate_limited(response)):
        until = datetime.fromtimestamp(int(headers["x-ratelimit-reset"]), tz=UTC)
        return RateLimitWait(WaitReason.RATE_LIMIT, until, attempt)
    if status == 429 or (status == 403 and ("retry-after" in headers or "secondary rate limit" in response.text.lower())):
        seconds = int(headers.get("retry-after", 60 * 2 ** (attempt - 1)))
        return RateLimitWait(WaitReason.SECONDARY_LIMIT, now + timedelta(seconds=seconds), attempt)
    if status in _SERVER_ERRORS:
        return RateLimitWait(WaitReason.SERVER_ERROR, now + timedelta(seconds=2**attempt), attempt)
    return None

# --- Client ---

# The token is fetched from the provider on every request instead of once in
# __init__. With a per user provider in the web version the same client can
# then serve different users. sleep is passed in so tests can record the waits
# without actually waiting.
class GitHubClient:
    def __init__(
        self,
        tokens: TokenProvider,
        http: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self._lock = asyncio.Lock()

    # Every request goes through here so headers, waiting and error handling
    # live in a single place. A dropped connection or a timeout on our side is
    # retried like a server error, it is the same kind of short hiccup.
    async def _request(self, method: str, path: str, on_wait: WaitListener | None = None, **kwargs) -> httpx.Response:
        async with self._lock:
            attempt = 0
            while True:
                attempt += 1
                now = datetime.now(UTC)
                token = await self._tokens.get_token()
                try:
                    response = await self._http.request(
                        method,
                        f"{API_URL}{path}",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": API_VERSION,
                        },
                        **kwargs,
                    )
                except httpx.TransportError:
                    if attempt > MAX_RETRIES:
                        raise
                    wait = RateLimitWait(WaitReason.SERVER_ERROR, now + timedelta(seconds=2**attempt), attempt)
                else:
                    wait = _wait_for(response, attempt, now)
                    if wait is None or attempt > MAX_RETRIES:
                        break
                await self._announce(wait, on_wait)
                # One extra second because the reset header is rounded down to
                # whole seconds and waking up early would only fail again.
                await self._sleep(max(0.0, (wait.until - datetime.now(UTC)).total_seconds()) + 1)

        if response.status_code == 401:
            raise GitHubAuthError(
                "GitHub rejected the token. Check GITHUB_TOKEN in backend/.env, it may be wrong, expired or revoked."
            )
        response.raise_for_status()
        return response

    # Whoever started the call decides where the news of a wait goes. Without a
    # listener a warning is logged, so a long wait never looks like a freeze.
    async def _announce(self, wait: RateLimitWait, on_wait: WaitListener | None) -> None:
        if on_wait is not None:
            await on_wait(wait)
        else:
            logger.warning(
                "GitHub is not answering, reason %s, attempt %d, waiting until %s",
                wait.reason,
                wait.attempt,
                wait.until.isoformat(timespec="seconds"),
            )

    # GitHub's GraphQL errors are turned into GitHubQueryError here, so callers
    # only ever see the data part of a successful answer.
    async def _graphql(self, query: str, variables: dict, on_wait: WaitListener | None) -> dict:
        response = await self._request("POST", "/graphql", on_wait, json={"query": query, "variables": variables})
        body = response.json()
        if body.get("errors"):
            messages = "; ".join(error.get("message", "unknown error") for error in body["errors"])
            raise GitHubQueryError(f"GitHub could not answer the query: {messages}")
        return body["data"]

    # Reads the core REST quota. Search and GraphQL have quotas of their own,
    # those are left out until something uses them.
    async def rate_limit(self) -> RateLimit:
        """Returns the current REST API quota for the token.

        Returns:
            How many requests the token gets per hour, how many are left and when the count resets.

        Raises:
            GitHubAuthError: The token was rejected.
            httpx.HTTPStatusError: GitHub answered with any other error.
        """
        rate = (await self._request("GET", "/rate_limit")).json()["rate"]
        return RateLimit(
            limit=rate["limit"],
            remaining=rate["remaining"],
            reset_at=datetime.fromtimestamp(rate["reset"], tz=UTC),
        )

    # Pages come newest first, so the first page whose oldest pull request is
    # older than since is the last one needed. If MAX_PAGES runs out before
    # that, the data only reaches back to the oldest pull request fetched and
    # complete_since says so.
    async def pull_requests(
        self,
        owner: str,
        name: str,
        since: datetime,
        on_wait: WaitListener | None = None,
    ) -> PullRequestHistory:
        """Fetches the pull requests of a repo opened since the given time.

        Args:
            owner: The user or organization that owns the repo.
            name: The repo name.
            since: Start of the window, timezone aware.
            on_wait: Called before every wait caused by a limit or a server
                error. When missing, waits are logged as warnings.

        Returns:
            The pull requests opened at or after complete_since, newest first.
            complete_since equals since unless the page limit was reached.

        Raises:
            GitHubAuthError: The token was rejected.
            GitHubQueryError: GitHub could not answer, for example the repo does not exist.
            httpx.HTTPStatusError: GitHub kept failing after every retry.
        """
        prs: list[PullRequest] = []
        cursor = None
        for _ in range(MAX_PAGES):
            variables = {
                "owner": owner,
                "name": name,
                "pageSize": PULL_REQUEST_PAGE_SIZE,
                "interactions": INTERACTION_LIMIT,
                "labels": LABEL_LIMIT,
                "cursor": cursor,
            }
            page = (await self._graphql(PULL_REQUESTS_QUERY, variables, on_wait))["repository"]["pullRequests"]
            batch = [parse_pull_request(node) for node in page["nodes"]]
            prs.extend(pr for pr in batch if pr.created_at >= since)
            passed_window = bool(batch) and batch[-1].created_at < since
            if passed_window or not page["pageInfo"]["hasNextPage"]:
                return PullRequestHistory(tuple(prs), since, since)
            cursor = page["pageInfo"]["endCursor"]
        return PullRequestHistory(tuple(prs), prs[-1].created_at if prs else since, since)

    # GitHub already cuts the history at since, so paging only stops at the
    # last page or at MAX_PAGES. Merge commits are dropped here and not in
    # parsing.py, but the last commit fetched still sets complete_since when
    # the limit is hit, merge or not. Otherwise a page ending in merge commits
    # would claim the data goes back less far than it really does.
    async def commits(
        self,
        owner: str,
        name: str,
        since: datetime,
        on_wait: WaitListener | None = None,
    ) -> CommitHistory:
        """Fetches the commits on a repo's default branch since the given time.

        Args:
            owner: The user or organization that owns the repo.
            name: The repo name.
            since: Start of the window, timezone aware.
            on_wait: Called before every wait caused by a limit or a server
                error. When missing, waits are logged as warnings.

        Returns:
            The commits made at or after complete_since, newest first, without
            merge commits. Empty when the repo has no default branch.
            complete_since equals since unless the page limit was reached.

        Raises:
            GitHubAuthError: The token was rejected.
            GitHubQueryError: GitHub could not answer, for example the repo does not exist.
            httpx.HTTPStatusError: GitHub kept failing after every retry.
        """
        commits: list[Commit] = []
        cursor = None
        oldest = since
        for _ in range(MAX_PAGES):
            variables = {
                "owner": owner,
                "name": name,
                "since": since.isoformat(),
                "pageSize": COMMIT_PAGE_SIZE,
                "cursor": cursor,
            }
            branch = (await self._graphql(COMMITS_QUERY, variables, on_wait))["repository"]["defaultBranchRef"]
            if branch is None:
                return CommitHistory((), since, since)
            page = branch["target"]["history"]
            for node in page["nodes"]:
                commit = parse_commit(node)
                oldest = commit.committed_at
                if node["parents"]["totalCount"] < 2:
                    commits.append(commit)
            if not page["pageInfo"]["hasNextPage"]:
                return CommitHistory(tuple(commits), since, since)
            cursor = page["pageInfo"]["endCursor"]
        return CommitHistory(tuple(commits), oldest, since)
