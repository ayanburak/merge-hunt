"""
file: repos.py
importable: router

GET /repos/{id} gives what discovery found out about a repo. GET
/repos/{id}/issues gives its saved issues, filtered by the query and analyzed,
and never goes to GitHub. POST /repos/{id}/issues/refresh fetches them again
with the labels of the last fetch, the UI reads them with the GET afterwards.
PUT and DELETE on /repos/{id}/issues/{number}/feedback record the user's Interested
or Skip and take it back. A repo the app never discovered gets 404 everywhere.

description: The API endpoints that give a repo, its issues and the feedback on them.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from merge_hunt.api.schemas import FeedbackWrite
from merge_hunt.domain.issues import IssueFilter, RepoIssues
from merge_hunt.domain.models import Repo, RepoRef
from merge_hunt.pipeline.issue_finder import IssueFetchError

router = APIRouter(prefix="/repos", tags=["repos"])

async def _known_repo(repo_id: int, request: Request) -> Repo:
    repo = await request.app.state.repos.load_repo(repo_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repo {repo_id} does not exist")
    return repo

@router.get("/{repo_id}")
async def get_repo(repo_id: int, request: Request) -> Repo:
    return await _known_repo(repo_id, request)

@router.get("/{repo_id}/issues")
async def repo_issues(
    repo_id: int,
    request: Request,
    hide_assigned: bool = True,
    hide_linked: bool = True,
    max_age_days: int | None = Query(None, ge=1),
    show_skipped: bool = False,
) -> RepoIssues:
    await _known_repo(repo_id, request)
    options = IssueFilter(hide_assigned, hide_linked, max_age_days, show_skipped)
    return await request.app.state.issue_finder.load(repo_id, options)

# 502 because the request itself was fine, GitHub is the one that failed.
@router.post("/{repo_id}/issues/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh_issues(repo_id: int, request: Request) -> None:
    repo = await _known_repo(repo_id, request)
    try:
        await request.app.state.issue_finder.refresh(RepoRef(repo.id, repo.owner, repo.name))
    except IssueFetchError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None

@router.put("/{repo_id}/issues/{number}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def give_feedback(repo_id: int, number: int, body: FeedbackWrite, request: Request) -> None:
    await _known_repo(repo_id, request)
    if not await request.app.state.issue_finder.give_feedback(repo_id, number, body.value):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Issue {number} is not saved for repo {repo_id}")

@router.delete("/{repo_id}/issues/{number}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def take_back_feedback(repo_id: int, number: int, request: Request) -> None:
    await _known_repo(repo_id, request)
    await request.app.state.issue_finder.take_back_feedback(repo_id, number)
