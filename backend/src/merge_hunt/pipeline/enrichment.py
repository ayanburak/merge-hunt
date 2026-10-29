"""
file: enrichment.py
importable: Enricher, EnrichResult, EnrichStatus, ProgressListener, DEFAULT_MAX_AGE

Enricher is the second stage of the scan. It is built with a GitHubClient
and a RepoStore, enrich(repos, options) walks the given repos one by one,
fetches their pull requests and commits from GitHub and saves them. A repo
fetched less than max_age ago is skipped as long as that fetch reached as far
back as the user's window, options.window_days for pull requests and
options.commit_days for commits.

Every repo ends up as an EnrichResult, fetched, skipped because it was fresh
or failed together with the reason. A failed repo does not stop the others.
Only a rejected token stops the whole run, GitHubAuthError goes up to the
caller because every repo after it would fail the same way.

description: Fetches the raw pull request and commit data of repos and saves it, unless a fresh enough copy is saved already.
"""

# NOTE: The freshness check loads a repo's whole saved data only to read three
# dates from it. With a few hundred pull requests per repo that is cheap next
# to a fetch, if it ever shows up in a scan's time RepoStore can get a method
# that reads just the repos row.

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import httpx

from merge_hunt.domain.metrics import MetricOptions
from merge_hunt.domain.models import Enrichment, RepoRef
from merge_hunt.github.client import GitHubClient, GitHubQueryError, WaitListener
from merge_hunt.storage.stores import RepoStore

# --- Constants ---

# A rate over a hundred and eighty days barely moves in a week, and fetching
# one repo can take close to a minute.
DEFAULT_MAX_AGE = timedelta(days=7)

# --- Results ---

# FRESH says why nothing was fetched, a saved copy was recent and reached far
# enough back.
class EnrichStatus(StrEnum):
    FETCHED = "FETCHED"
    FRESH = "FRESH"
    FAILED = "FAILED"

# error is the text of the exception that failed the repo, empty otherwise.
@dataclass(frozen=True, slots=True)
class EnrichResult:
    repo: RepoRef
    status: EnrichStatus
    error: str | None = None

# The scan job hangs a listener here that writes progress to the database.
ProgressListener = Callable[[EnrichResult], Awaitable[None]]

# --- Freshness ---

# requested_since is compared and not complete_since. A big repo whose fetch
# was cut by the page limit never reaches the window start, comparing
# complete_since would refetch it on every scan for the same result.
def _is_fresh(saved: Enrichment | None, now: datetime, max_age: timedelta, prs_since: datetime, commits_since: datetime) -> bool:
    return (
        saved is not None
        and now - saved.enriched_at < max_age
        and saved.prs.requested_since <= prs_since
        and saved.commits.requested_since <= commits_since
    )

# --- Enricher ---

# clock is passed in for the same reason GitHubClient takes sleep, tests can
# fix the time instead of depending on when they run.
class Enricher:
    def __init__(
        self,
        client: GitHubClient,
        store: RepoStore,
        max_age: timedelta = DEFAULT_MAX_AGE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._store = store
        self._max_age = max_age
        self._clock = clock

    # save_repo runs for fresh repos too, so a renamed repo gets its new name
    # even when nothing is fetched. httpx.HTTPError covers what is left after
    # the client's retries, a server that kept failing or a lost connection.
    async def _enrich_one(self, repo: RepoRef, options: MetricOptions, on_wait: WaitListener | None) -> EnrichResult:
        now = self._clock()
        prs_since = now - timedelta(days=options.window_days)
        commits_since = now - timedelta(days=options.commit_days)
        await self._store.save_repo(repo.id, repo.owner, repo.name)
        saved = await self._store.load_enrichment(repo.id)
        if _is_fresh(saved, now, self._max_age, prs_since, commits_since):
            return EnrichResult(repo, EnrichStatus.FRESH)
        try:
            prs = await self._client.pull_requests(repo.owner, repo.name, prs_since, on_wait)
            commits = await self._client.commits(repo.owner, repo.name, commits_since, on_wait)
        except (GitHubQueryError, httpx.HTTPError) as error:
            return EnrichResult(repo, EnrichStatus.FAILED, str(error))
        await self._store.save_enrichment(repo.id, prs, commits, now)
        return EnrichResult(repo, EnrichStatus.FETCHED)

    async def enrich(
        self,
        repos: Iterable[RepoRef],
        options: MetricOptions,
        on_progress: ProgressListener | None = None,
        on_wait: WaitListener | None = None,
    ) -> list[EnrichResult]:
        """Fetches and saves the raw data of each repo that has no fresh copy.

        Args:
            repos: The repos to enrich, handled one at a time in this order.
            options: The user's settings, window_days and commit_days decide
                how far back the data has to reach.
            on_progress: Called with each repo's result as soon as it is done.
            on_wait: Handed to the client, called before every wait caused by
                a limit or a server error.

        Returns:
            One result per repo, in the order the repos were given.

        Raises:
            GitHubAuthError: The token was rejected, the repos after the
                current one are not tried.
        """
        results = []
        for repo in repos:
            result = await self._enrich_one(repo, options, on_wait)
            results.append(result)
            if on_progress is not None:
                await on_progress(result)
        return results
