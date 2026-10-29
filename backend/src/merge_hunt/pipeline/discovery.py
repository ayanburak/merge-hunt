"""
file: discovery.py
importable: Discoverer, DiscoveryOptions, DiscoveryResult, SearchSort, search_query, MAX_CANDIDATES

Discoverer is the first stage of the scan. It is built with a GitHubClient
and a RepoStore, discover(options, prs_since) turns the user's search settings
into one GitHub repo search and saves every repo it finds. Then it counts each
repo's pull requests opened since prs_since and keeps the ones between
options.min_prs and options.max_prs, those come back as RepoRef objects ready
to be handed to Enricher. Linking the two stages is the scan job's work,
Discoverer never starts an enrichment itself.

At most options.max_candidates repos are searched, in the order options.sort
asks for. Archived repos and forks are always left out.

description: Finds candidate repos with GitHub's search and saves what the search tells about them.
"""

# NOTE: The pull request count is what keeps a scan short. In October 2026 the
# first thirty Python candidates had a median of 506 pull requests in 180 days
# sorted by last update and 160 sorted by stars, and fetching takes about
# four and a half seconds per fifty. Above 500 a repo is bigger than what the
# project looks for, below 30 its metrics almost always lack data. One count
# took half a second.
#
# NOTE: The search text is built here and not in the github package, because
# it comes straight from DiscoveryOptions. GitHubClient only runs a ready text
# and does not know what the user asked for.

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from merge_hunt.domain.models import RepoRef
from merge_hunt.github.client import GitHubClient, WaitListener
from merge_hunt.storage.stores import RepoStore

# --- Options ---

# GitHub returns at most a thousand results for a single search.
MAX_CANDIDATES = 1000

# The values are GitHub's own sort qualifiers. Stars down is the default, last
# update first picked the busiest and most expensive repos.
class SearchSort(StrEnum):
    STARS_DESC = "stars-desc"
    STARS_ASC = "stars-asc"
    UPDATED_DESC = "updated-desc"

# The defaults are what the UI starts with, like MetricOptions. An empty
# language, topic or license means no filter on it. A zero issue count adds no
# qualifier, GitHub can not join the two issue conditions with an or, so a
# repo has to meet both when both are set.
@dataclass(frozen=True, slots=True)
class DiscoveryOptions:
    language: str | None = None
    min_stars: int = 200
    max_stars: int = 5000
    pushed_within_days: int = 90
    topic: str | None = None
    license: str | None = None
    min_good_first_issues: int = 1
    min_help_wanted_issues: int = 0
    max_candidates: int = 100
    sort: SearchSort = SearchSort.STARS_DESC
    min_prs: int = 30
    max_prs: int = 500

# found counts every repo the search returned, repos only the ones whose pull
# request count was in range. The scan job shows the difference as skipped.
@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    found: int
    repos: tuple[RepoRef, ...]

# --- Search Text ---

# Languages, topics and licenses with a space in them, like Jupyter Notebook,
# need quotes or GitHub reads the second word as a search term.
def _value(text: str) -> str:
    return f'"{text}"' if " " in text else text

# Public so the tests and the scan job can show exactly what was searched.
# The pushed date is cut to a day, GitHub's qualifier takes a date and the time
# of day would change nothing.
def search_query(options: DiscoveryOptions, now: datetime) -> str:
    """Builds GitHub's search text for the given options.

    Args:
        options: The user's search settings.
        now: The moment pushed_within_days is counted back from.

    Returns:
        The search text with its qualifiers, for example
        stars:200..5000 pushed:>=2026-07-31 archived:false fork:false
        good-first-issues:>=1 sort:stars-desc.
    """
    pushed = (now - timedelta(days=options.pushed_within_days)).date().isoformat()
    parts = [f"stars:{options.min_stars}..{options.max_stars}", f"pushed:>={pushed}", "archived:false", "fork:false"]
    if options.language:
        parts.append(f"language:{_value(options.language)}")
    if options.topic:
        parts.append(f"topic:{_value(options.topic)}")
    if options.license:
        parts.append(f"license:{_value(options.license)}")
    if options.min_good_first_issues > 0:
        parts.append(f"good-first-issues:>={options.min_good_first_issues}")
    if options.min_help_wanted_issues > 0:
        parts.append(f"help-wanted-issues:>={options.min_help_wanted_issues}")
    parts.append(f"sort:{options.sort}")
    return " ".join(parts)

# --- Discoverer ---

# clock is passed in like in Enricher, so tests can fix the pushed date.
class Discoverer:
    def __init__(
        self,
        client: GitHubClient,
        store: RepoStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._store = store
        self._clock = clock

    # The limit is checked before any request, a value from the UI above it
    # would otherwise quietly return fewer repos than asked for. Every repo
    # found is saved, also the skipped ones, so what the search saw stays
    # visible. The counts go one by one like every other request.
    async def discover(
        self,
        options: DiscoveryOptions,
        prs_since: datetime,
        on_wait: WaitListener | None = None,
    ) -> DiscoveryResult:
        """Searches GitHub for candidate repos, saves them and picks the ones to enrich.

        Args:
            options: The user's search settings.
            prs_since: Start of the window the pull requests are counted in,
                the scan job passes the start of the metrics window.
            on_wait: Handed to the client, called before every wait caused by
                a limit or a server error.

        Returns:
            How many repos the search found and the ones whose pull request
            count is within options.min_prs and options.max_prs, in the
            search's order.

        Raises:
            ValueError: options.max_candidates is above MAX_CANDIDATES.
            GitHubAuthError: The token was rejected.
            GitHubQueryError: GitHub could not answer the search.
            httpx.HTTPStatusError: GitHub kept failing after every retry.
        """
        if options.max_candidates > MAX_CANDIDATES:
            raise ValueError(f"max_candidates is {options.max_candidates}, GitHub's search returns at most {MAX_CANDIDATES}")
        now = self._clock()
        repos = await self._client.search_repos(search_query(options, now), options.max_candidates, on_wait)
        await self._store.save_repos(repos, now)
        kept = []
        for repo in repos:
            count = await self._client.count_pull_requests(repo.owner, repo.name, prs_since, on_wait)
            if options.min_prs <= count <= options.max_prs:
                kept.append(RepoRef(repo.id, repo.owner, repo.name))
        return DiscoveryResult(len(repos), tuple(kept))
