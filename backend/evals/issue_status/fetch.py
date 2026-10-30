"""
file: fetch.py

Saves a snapshot of the recent unassigned issues of a few repos as a JSON file,
ready to be labelled by hand. Run from the backend folder:

    uv run python evals/issue_status/fetch.py evals/issue_status/dev.json owner/name owner/name

description: Takes the snapshot of real issues the issue rules are measured on.
"""

# Logins stay out, the rules only need what a comment says and who it speaks
# for. Texts are cut to what the rules read so the file stays small.

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

import httpx

from merge_hunt.config import Settings
from merge_hunt.github.client import HTTP_TIMEOUT, GitHubClient
from merge_hunt.github.token import StaticTokenProvider

PER_REPO = 30
BODY_LIMIT = 1500
COMMENT_LIMIT = 800

async def main(path: str, repos: list[str]) -> None:
    now = datetime.now(UTC)
    items = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        client = GitHubClient(StaticTokenProvider(Settings().github_token), http)
        for repo in repos:
            owner, name = repo.split("/")
            issues = await client.recent_issues(owner, name, now - timedelta(days=180))
            for issue in [item for item in issues if not item.assigned][:PER_REPO]:
                items.append({
                    "repo": repo,
                    "number": issue.number,
                    "verdict": None,
                    "title": issue.title,
                    "body": issue.body[:BODY_LIMIT],
                    "labels": list(issue.labels),
                    "created_at": issue.created_at.isoformat(),
                    "updated_at": issue.updated_at.isoformat(),
                    "linked_prs": issue.linked_prs,
                    "comment_count": issue.comment_count,
                    "comments": [
                        {"is_bot": item.author.is_bot, "association": item.association, "at": item.at.isoformat(), "body": item.body[:COMMENT_LIMIT]}
                        for item in issue.comments
                    ],
                })
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"fetched_at": now.isoformat(), "issues": items}, file, indent=1, ensure_ascii=False)
    print(f"{len(items)} issues saved to {path}")

asyncio.run(main(sys.argv[1], sys.argv[2:]))
