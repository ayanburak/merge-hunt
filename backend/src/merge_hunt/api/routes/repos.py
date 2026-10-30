"""
file: repos.py
importable: router

GET /repos/{id}/issues gives a repo's saved issues, filtered by the query and
analyzed, and never goes to GitHub. POST /repos/{id}/issues/refresh fetches them
again, the UI reads them with the GET afterwards. A repo the app never
discovered gets 404 from both.

description: The API endpoints that give and refresh the issues of a repo.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from merge_hunt.domain.issues import IssueFilter, RepoIssues
from merge_hunt.domain.models import Repo, RepoRef
from merge_hunt.pipeline.issue_finder import DEFAULT_ISSUE_LABELS, IssueFetchError

router = APIRouter(prefix="/repos", tags=["repos"])

async def _known_repo(repo_id: int, request: Request) -> Repo:
    repo = await request.app.state.repos.load_repo(repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repo {repo_id} does not exist")
    return repo

@router.get("/{repo_id}/issues")
async def repo_issues(
    repo_id: int,
    request: Request,
    hide_assigned: bool = True,
    hide_linked: bool = True,
    max_age_days: int | None = Query(None, ge=1),
) -> RepoIssues:
    await _known_repo(repo_id, request)
    return await request.app.state.issue_finder.load(repo_id, IssueFilter(hide_assigned, hide_linked, max_age_days))

# 502 because the request itself was fine, GitHub is the one that failed.
@router.post("/{repo_id}/issues/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh_issues(
    repo_id: int,
    request: Request,
    labels: Annotated[list[str], Query(min_length=1)] = list(DEFAULT_ISSUE_LABELS),
) -> None:
    repo = await _known_repo(repo_id, request)
    try:
        await request.app.state.issue_finder.refresh(RepoRef(repo.id, repo.owner, repo.name), labels)
    except IssueFetchError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
