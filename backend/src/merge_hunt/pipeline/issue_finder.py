"""
file: issue_finder.py
importable: IssueFinder, IssueResult, IssueFetchError, IssueProgressListener, DEFAULT_ISSUE_LABELS

IssueFinder is the fourth stage of the scan. It is built with a GitHubClient,
an IssueStore and an IssueAnalyzer. find(repos, labels) walks the given repos
one by one, fetches their open issues carrying one of the labels and every
issue opened in the last RECENT_ISSUE_DAYS, and saves them. Unlike enrichment
there is no freshness check, a fetch takes a few seconds and issues get taken
quickly.

refresh(repo) does the same for one repo outside a scan, with the labels of its
last fetch. load(repo_id, options) reads what is saved, filters it and runs the
analyzer. It never goes to GitHub.

A failed repo does not stop the others in find. Only a rejected token stops the
whole run, GitHubAuthError goes up to the caller.

description: Fetches and saves the open issues of repos and gives them back filtered and analyzed.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from merge_hunt.domain.issues import AnalyzedIssue, FeedbackValue, IssueFilter, RepoIssues, is_shown
from merge_hunt.domain.models import RepoRef
from merge_hunt.github.client import GitHubAuthError, GitHubClient, GitHubQueryError, WaitListener
from merge_hunt.pipeline.issue_analysis import IssueAnalyzer
from merge_hunt.storage.stores import IssueStore

# --- Constants ---

# GitHub's two default labels for work open to newcomers. In fifteen repos they
# covered what looked for a beginner, and hacktoberfest is left out because it
# only means something one month a year.
DEFAULT_ISSUE_LABELS = ("good first issue", "help wanted")

# New issues count even without a label. In October 2026 only five of the 166
# issues opened in nine repos over half a year carried one of the labels above.
RECENT_ISSUE_DAYS = 180

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
        now = self._clock()
        labelled = await self._client.issues(repo.owner, repo.name, labels, on_wait)
        recent = await self._client.recent_issues(repo.owner, repo.name, now - timedelta(days=RECENT_ISSUE_DAYS), on_wait)
        unique = {issue.number: issue for issue in (*labelled, *recent)}
        await self._store.save(repo.id, unique.values(), labels, now)

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
            labels: Issues carrying one of these are fetched at any age.
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

    # A fetch replaces the saved list, so the same labels have to be used or
    # issues of a scan with its own labels would disappear.
    async def refresh(self, repo: RepoRef) -> None:
        """Fetches one repo's issues again, replacing the saved ones.

        Args:
            repo: The repo.

        Raises:
            IssueFetchError: GitHub could not answer or rejected the token.
        """
        last = await self._store.load(repo.id)
        labels = last.labels if last else DEFAULT_ISSUE_LABELS
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
            The issues that pass the filter, newest first. fetched_at is empty
            when the repo's issues were never fetched.
        """
        fetch = await self._store.load(repo_id)
        if fetch is None:
            return RepoIssues(None, (), ())
        now = self._clock()
        feedback = await self._store.feedback(repo_id)
        newest = sorted(fetch.issues, key=lambda issue: issue.created_at, reverse=True)
        shown = [issue for issue in newest if is_shown(issue, options, now, feedback.get(issue.number))]
        analyzed = [AnalyzedIssue(issue, await self._analyzer.analyze(issue), feedback.get(issue.number)) for issue in shown]
        return RepoIssues(fetch.fetched_at, fetch.labels, tuple(analyzed))

    async def give_feedback(self, repo_id: int, number: int, value: FeedbackValue) -> bool:
        """Records the user's feedback on an issue.

        Args:
            repo_id: GitHub's numeric id of the repo.
            number: The issue's number.
            value: The feedback.

        Returns:
            False when the repo has no saved issue with this number.
        """
        return await self._store.save_feedback(repo_id, number, value, self._clock())

    async def take_back_feedback(self, repo_id: int, number: int) -> None:
        """Removes the user's feedback on an issue."""
        await self._store.delete_feedback(repo_id, number)
