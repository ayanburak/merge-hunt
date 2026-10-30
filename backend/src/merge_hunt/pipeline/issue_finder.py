"""
file: issue_finder.py
importable: IssueFinder, IssueResult, IssueFetchError, IssueProgressListener, DEFAULT_ISSUE_LABELS

IssueFinder is the fourth stage of the scan. It is built with a GitHubClient,
an IssueStore and an IssueAnalyzer. find(repos, labels) walks the given repos
one by one, fetches their open issues carrying one of the labels and saves
them. Unlike enrichment there is no freshness check, a fetch takes about a
second and issues get taken quickly.

refresh(repo, labels) does the same for one repo outside a scan. load(repo_id,
options) reads what is saved, filters it and runs the analyzer. It never goes
to GitHub.

A failed repo does not stop the others in find. Only a rejected token stops the
whole run, GitHubAuthError goes up to the caller.

description: Fetches and saves the open issues of repos and gives them back filtered and analyzed.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from merge_hunt.domain.issues import AnalyzedIssue, IssueFilter, RepoIssues, is_shown
from merge_hunt.domain.models import RepoRef
from merge_hunt.github.client import GitHubAuthError, GitHubClient, GitHubQueryError, WaitListener
from merge_hunt.pipeline.issue_analysis import IssueAnalyzer
from merge_hunt.storage.stores import IssueStore

# --- Constants ---

# GitHub's two default labels for work open to newcomers. In fifteen repos they
# covered what looked for a beginner, and hacktoberfest is left out because it
# only means something one month a year.
DEFAULT_ISSUE_LABELS = ("good first issue", "help wanted")

# --- Results ---

# Raised by refresh, the API turns it into 502. It keeps the API from knowing
# which errors the GitHub client can raise.
class IssueFetchError(RuntimeError):
    pass

# error is the text of the exception that failed the repo, empty otherwise.
@dataclass(frozen=True, slots=True)
class IssueResult:
    repo: RepoRef
    error: str | None = None

IssueProgressListener = Callable[[IssueResult], Awaitable[None]]

# --- Finder ---

class IssueFinder:
    def __init__(
        self,
        client: GitHubClient,
        store: IssueStore,
        analyzer: IssueAnalyzer,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._store = store
        self._analyzer = analyzer
        self._clock = clock

    async def _fetch(self, repo: RepoRef, labels: Sequence[str], on_wait: WaitListener | None) -> None:
        issues = await self._client.issues(repo.owner, repo.name, labels, on_wait)
        await self._store.save(repo.id, issues, self._clock())

    async def find(
        self,
        repos: Iterable[RepoRef],
        labels: Sequence[str],
        on_progress: IssueProgressListener | None = None,
        on_wait: WaitListener | None = None,
    ) -> None:
        """Fetches and saves the issues of each repo.

        Args:
            repos: The repos, handled one at a time in this order.
            labels: Issues carrying one of these are fetched.
            on_progress: Called with each repo's result as soon as it is done.
            on_wait: Handed to the client, called before every wait caused by
                a limit or a server error.

        Raises:
            GitHubAuthError: The token was rejected, the repos after the
                current one are not tried.
        """
        for repo in repos:
            try:
                await self._fetch(repo, labels, on_wait)
            except (GitHubQueryError, httpx.HTTPError) as error:
                result = IssueResult(repo, f"Issues could not be fetched: {error}")
            else:
                result = IssueResult(repo)
            if on_progress is not None:
                await on_progress(result)

    async def refresh(self, repo: RepoRef, labels: Sequence[str]) -> None:
        """Fetches one repo's issues again, replacing the saved ones.

        Args:
            repo: The repo.
            labels: Issues carrying one of these are fetched.

        Raises:
            IssueFetchError: GitHub could not answer or rejected the token.
        """
        try:
            await self._fetch(repo, labels, None)
        except (GitHubAuthError, GitHubQueryError, httpx.HTTPError) as error:
            raise IssueFetchError(str(error)) from error

    # The filter runs first so the analyzer only sees what the user will see.
    # That starts to matter when the analyzer is a slow model.
    async def load(self, repo_id: int, options: IssueFilter) -> RepoIssues:
        """Gives a repo's saved issues, filtered and analyzed.

        Args:
            repo_id: GitHub's numeric id of the repo.
            options: What the user wants to hide.

        Returns:
            The issues that pass the filter, last updated first. fetched_at is
            empty when the repo's issues were never fetched.
        """
        fetch = await self._store.load(repo_id)
        if fetch is None:
            return RepoIssues(None, ())
        now = self._clock()
        shown = [issue for issue in fetch.issues if is_shown(issue, options, now)]
        return RepoIssues(fetch.fetched_at, tuple([AnalyzedIssue(issue, await self._analyzer.analyze(issue)) for issue in shown]))
