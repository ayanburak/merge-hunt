"""
file: client.py
importable: GitHubClient, RateLimit, GitHubAuthError

GitHubClient is the only place in the app that talks to GitHub. It is built
with a TokenProvider and an httpx.AsyncClient, both passed in from outside.
The caller owns the httpx client and closes it, GitHubClient never does.
rate_limit() reports the remaining request quota and does not use any of it,
so it is also the cheapest way to check that a token works.

description: Async client for the GitHub API.
"""

# TODO: Only rate_limit() exists for now. Search, GraphQL queries and
# handling of the secondary rate limit come with the pipeline steps that
# need them.

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from merge_hunt.github.token import TokenProvider

# --- Constants ---

API_URL = "https://api.github.com"

# GitHub asks clients to pin the API version, without it a future default
# version could change response shapes under us.
API_VERSION = "2022-11-28"

# --- Errors ---

# A rejected token is its own error type because the fix is always the same,
# the user has to check the token, and the UI can say exactly that.
class GitHubAuthError(Exception):
    """GitHub rejected the token, it is wrong, expired or revoked."""

# --- Models ---

# Lives here and not in domain because quota is a GitHub detail, the business
# logic should never need to know about it.
@dataclass(frozen=True)
class RateLimit:
    limit: int
    remaining: int
    reset_at: datetime

# --- Client ---

# The token is fetched from the provider on every request instead of once in
# __init__. With a per user provider in the web version the same client can
# then serve different users.
class GitHubClient:
    def __init__(self, tokens: TokenProvider, http: httpx.AsyncClient) -> None:
        self._tokens = tokens
        self._http = http

    # Every request goes through here so headers and error handling live in a
    # single place.
    async def _get(self, path: str) -> dict:
        token = await self._tokens.get_token()
        response = await self._http.get(
            f"{API_URL}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        if response.status_code == 401:
            raise GitHubAuthError(
                "GitHub rejected the token. Check GITHUB_TOKEN in backend/.env, it may be wrong, expired or revoked."
            )
        response.raise_for_status()
        return response.json()

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
        rate = (await self._get("/rate_limit"))["rate"]
        return RateLimit(
            limit=rate["limit"],
            remaining=rate["remaining"],
            reset_at=datetime.fromtimestamp(rate["reset"], tz=UTC),
        )
