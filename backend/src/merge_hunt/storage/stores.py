"""
file: stores.py
importable: RepoStore, RepoNotFoundError

RepoStore reads and writes a repo, what discovery found out about it and its
raw enrichment data. It is built
with an async_sessionmaker and opens its own session for every call, so
callers never deal with sessions or transactions. Everything goes in and
comes out as the classes of domain/models.py, the Row classes stay inside
storage.

load_repo only gives back repos discovery saw, a repo saved by save_repo
alone has no stars or language to fill a Repo with.

save_repo or save_repos has to run before save_enrichment, a repo's owner
and name come from outside and the enrichment data alone can not create the
row. save_repos keeps the enrichment data of a repo found again, save_repo
keeps its discovery data.

description: Saves and loads repos and their pull request and commit data.
"""

# NOTE: save_enrichment does not merge with what is already there, it deletes
# the repo's pull requests, interactions and commits and writes the new ones,
# all in a single transaction. That way complete_since always describes one
# fetch, and a write that fails half way leaves the old data as it was.

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from merge_hunt.domain.models import (
    Actor,
    CloseSource,
    Commit,
    CommitHistory,
    Enrichment,
    Interaction,
    PullRequest,
    PullRequestHistory,
    Repo,
)
from merge_hunt.storage.tables import CommitRow, InteractionRow, PullRequestRow, RepoRow

# --- Errors ---

# Raised when enrichment data is saved for a repo save_repo never saw. That is
# a mistake in the calling code and not something to retry.
class RepoNotFoundError(LookupError):
    pass

# --- Domain to Rows ---

def _pull_request_row(repo_id: int, pr: PullRequest) -> PullRequestRow:
    return PullRequestRow(
        repo_id=repo_id,
        number=pr.number,
        author_login=pr.author.login,
        author_is_bot=pr.author.is_bot,
        author_association=pr.author_association,
        created_at=pr.created_at,
        merged_at=pr.merged_at,
        closed_at=pr.closed_at,
        closed_by_login=pr.closed_by.login if pr.closed_by else None,
        closed_by_is_bot=pr.closed_by.is_bot if pr.closed_by else None,
        closed_via=pr.closed_via,
        labels=list(pr.labels),
        interactions=[
            InteractionRow(actor_login=item.actor.login, actor_is_bot=item.actor.is_bot, association=item.association, at=item.at)
            for item in pr.interactions
        ],
    )

def _commit_row(repo_id: int, commit: Commit) -> CommitRow:
    return CommitRow(
        repo_id=repo_id,
        author_login=commit.author.login,
        author_is_bot=commit.author.is_bot,
        committed_at=commit.committed_at,
    )

# --- Rows to Domain ---

# closed_by_login and closed_by_is_bot are written together, so checking the
# login alone is enough to know whether there was a closer.
def _pull_request(row: PullRequestRow) -> PullRequest:
    return PullRequest(
        number=row.number,
        author=Actor(row.author_login, row.author_is_bot),
        author_association=row.author_association,
        created_at=row.created_at,
        merged_at=row.merged_at,
        closed_at=row.closed_at,
        closed_by=Actor(row.closed_by_login, row.closed_by_is_bot) if row.closed_by_login is not None else None,
        closed_via=CloseSource(row.closed_via) if row.closed_via is not None else None,
        interactions=tuple(Interaction(Actor(item.actor_login, item.actor_is_bot), item.association, item.at) for item in row.interactions),
        labels=tuple(row.labels),
    )

def _commit(row: CommitRow) -> Commit:
    return Commit(author=Actor(row.author_login, row.author_is_bot), committed_at=row.committed_at)

# Only called for a discovered row, where save_repos filled every column Repo
# needs. topics is a JSON list in the row and a tuple in Repo.
def _repo(row: RepoRow) -> Repo:
    return Repo(
        id=row.id,
        owner=row.owner,
        name=row.name,
        stars=row.stars,
        forks=row.forks,
        language=row.language,
        license=row.license,
        topics=tuple(row.topics or ()),
        open_issues=row.open_issues,
        pushed_at=row.pushed_at,
        latest_release_at=row.latest_release_at,
    )

# --- Store ---

# The only door to the repo tables. No abstract class sits in front of it,
# moving to PostgreSQL would only change the URL and not this class.
class RepoStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # A repo seen again under a new name keeps its row, GitHub's id does not
    # change on a rename or a move to another owner.
    async def save_repo(self, id: int, owner: str, name: str) -> None:
        """Adds a repo, or updates its owner and name when it is already saved.

        Args:
            id: GitHub's numeric id of the repo.
            owner: The user or organization that owns the repo now.
            name: The repo name now.
        """
        async with self._sessions.begin() as session:
            row = await session.get(RepoRow, id)
            if row is None:
                session.add(RepoRow(id=id, owner=owner, name=name))
            else:
                row.owner = owner
                row.name = name

    # merge looks each repo up by its primary key and updates the row when it
    # is there, so a repo found again keeps its enrichment columns and its
    # pull requests. Repos come from one search, a thousand at most, so one
    # lookup per repo is fine.
    async def save_repos(self, repos: Iterable[Repo], at: datetime) -> None:
        """Adds the repos discovery found, or updates the ones already saved.

        All of them are written in one transaction.

        Args:
            repos: The repos as the search returned them.
            at: When they were found, timezone aware.
        """
        async with self._sessions.begin() as session:
            for repo in repos:
                await session.merge(
                    RepoRow(
                        id=repo.id,
                        owner=repo.owner,
                        name=repo.name,
                        stars=repo.stars,
                        forks=repo.forks,
                        language=repo.language,
                        license=repo.license,
                        topics=list(repo.topics),
                        open_issues=repo.open_issues,
                        pushed_at=repo.pushed_at,
                        latest_release_at=repo.latest_release_at,
                        discovered_at=at,
                    )
                )

    # discovered_at is what tells a discovered row apart, save_repos always
    # sets it and save_repo never does.
    async def load_repo(self, id: int) -> Repo | None:
        """Loads what discovery found out about a repo.

        Args:
            id: GitHub's numeric id of the repo.

        Returns:
            The repo, or None when it is unknown or was never discovered.
        """
        async with self._sessions() as session:
            row = await session.get(RepoRow, id)
            if row is None or row.discovered_at is None:
                return None
            return _repo(row)

    # Deleting the pull requests takes their interactions with them through
    # the database cascade, tables.py explains why that needs foreign_keys on.
    async def save_enrichment(self, repo_id: int, prs: PullRequestHistory, commits: CommitHistory, at: datetime) -> None:
        """Replaces a repo's pull requests and commits with a new fetch.

        Either all of it is written or, when something fails, nothing changes.

        Args:
            repo_id: GitHub's numeric id of a repo saved with save_repo.
            prs: The fetched pull requests.
            commits: The fetched commits.
            at: When the data was fetched, timezone aware.

        Raises:
            RepoNotFoundError: The repo was never saved with save_repo.
        """
        async with self._sessions.begin() as session:
            repo = await session.get(RepoRow, repo_id)
            if repo is None:
                raise RepoNotFoundError(f"Repo {repo_id} is not saved, save_repo has to come first")
            await session.execute(delete(PullRequestRow).where(PullRequestRow.repo_id == repo_id))
            await session.execute(delete(CommitRow).where(CommitRow.repo_id == repo_id))
            session.add_all(_pull_request_row(repo_id, pr) for pr in prs.prs)
            session.add_all(_commit_row(repo_id, commit) for commit in commits.commits)
            repo.enriched_at = at
            repo.prs_complete_since = prs.complete_since
            repo.commits_complete_since = commits.complete_since
            repo.prs_requested_since = prs.requested_since
            repo.commits_requested_since = commits.requested_since

    # Interactions come in one extra query for all pull requests together,
    # loading them one pull request at a time would mean hundreds of queries.
    async def load_enrichment(self, repo_id: int) -> Enrichment | None:
        """Loads the last saved fetch of a repo.

        Args:
            repo_id: GitHub's numeric id of the repo.

        Returns:
            The pull requests and commits in the order they were saved, or
            None when the repo is unknown or was never enriched.
        """
        async with self._sessions() as session:
            repo = await session.get(RepoRow, repo_id)
            if repo is None or repo.enriched_at is None:
                return None
            pr_rows = await session.scalars(
                select(PullRequestRow)
                .where(PullRequestRow.repo_id == repo_id)
                .order_by(PullRequestRow.id)
                .options(selectinload(PullRequestRow.interactions))
            )
            commit_rows = await session.scalars(select(CommitRow).where(CommitRow.repo_id == repo_id).order_by(CommitRow.id))
            return Enrichment(
                enriched_at=repo.enriched_at,
                prs=PullRequestHistory(
                    tuple(_pull_request(row) for row in pr_rows), repo.prs_complete_since, repo.prs_requested_since
                ),
                commits=CommitHistory(
                    tuple(_commit(row) for row in commit_rows), repo.commits_complete_since, repo.commits_requested_since
                ),
            )
