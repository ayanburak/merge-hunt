"""
file: scoring.py
importable: Scorer

Scorer is the third stage of the scan. It is built with a RepoStore and an
IssueFinder, score(repo_ids, profile) reads each repo's saved data, scores it with
domain/scoring.py and returns the repos best first. It never talks to GitHub,
so when the user changes a filter or a weight the same repos can be scored
again right away without a new scan.

A repo that was never discovered or never enriched is left out of the result,
there is nothing to score it on. Repos that failed a filter stay in, after the
ones that passed, so the UI can say why they dropped. The issue count only goes
along for the UI, it has no part in the score or the filters.

description: Scores the saved repos against the user's profile and sorts them.
"""

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime

from merge_hunt.domain.scoring import FilterProfile, ScoredRepo, covers_window, score_repo
from merge_hunt.pipeline.issue_finder import IssueFinder
from merge_hunt.storage.stores import RepoStore

# clock is passed in like in Enricher, so tests can fix the day the windows
# are counted back from.
class Scorer:
    def __init__(self, store: RepoStore, issues: IssueFinder, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._store = store
        self._issues = issues
        self._clock = clock

    # The clock is read once, so every repo in one call is measured against
    # the same moment. sort is stable, two repos with the same score keep the
    # order they were given in.
    async def score(self, repo_ids: Iterable[int], profile: FilterProfile) -> list[ScoredRepo]:
        """Scores the given repos and sorts them.

        Args:
            repo_ids: GitHub's numeric ids of the repos to score.
            profile: The user's filters, weights and metric settings.

        Returns:
            The repos that passed every filter first, then the failed ones,
            each group from the highest score down.
        """
        now = self._clock()
        repo_ids = list(repo_ids)
        counts = await self._issues.free_counts(repo_ids)
        results = []
        for repo_id in repo_ids:
            repo = await self._store.load_repo(repo_id)
            enrichment = await self._store.load_enrichment(repo_id)
            if repo is None or enrichment is None:
                continue
            covered = covers_window(enrichment, profile.metrics, now)
            scored = score_repo(repo, enrichment.prs.prs, enrichment.commits.commits, profile, now, covered)
            results.append(replace(scored, free_issues=counts.get(repo_id)))
        results.sort(key=lambda item: (not item.passed, -item.score))
        return results
