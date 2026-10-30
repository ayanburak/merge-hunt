"""
file: evaluate.py

Runs RuleIssueAnalyzer over a labelled snapshot and prints how well the list of
shown issues matches the verdicts. Run from the backend folder:

    uv run python evals/issue_status/evaluate.py evals/issue_status/dev.json
    uv run python evals/issue_status/evaluate.py evals/issue_status/dev.json --errors

Precision is the share of shown issues that are really free. Recall is the
share of free issues that are shown.

description: Scores the issue rules against the issues labelled by hand.
"""

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime

from merge_hunt.domain.models import Actor, Issue, IssueComment
from merge_hunt.pipeline.issue_analysis import RuleIssueAnalyzer

# Logins are not in the snapshot and no rule reads them.
def to_issue(item: dict) -> Issue:
    comments = tuple(
        IssueComment(Actor("someone", part["is_bot"]), part["association"], datetime.fromisoformat(part["at"]), part["body"])
        for part in item["comments"]
    )
    return Issue(
        number=item["number"],
        title=item["title"],
        body=item["body"],
        author=Actor("someone", False),
        created_at=datetime.fromisoformat(item["created_at"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
        labels=tuple(item["labels"]),
        assigned=False,
        linked_prs=item["linked_prs"],
        comment_count=item["comment_count"],
        comments=comments,
    )

async def main(path: str, show_errors: bool) -> None:
    with open(path, encoding="utf-8") as file:
        snapshot = json.load(file)
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    analyzer = RuleIssueAnalyzer(lambda: fetched_at)

    shown = Counter()
    errors = []
    for item in snapshot["issues"]:
        analysis = await analyzer.analyze(to_issue(item))
        kept = item["linked_prs"] == 0 and analysis.hide is None
        shown[item["verdict"], kept] += 1
        if kept != (item["verdict"] == "FREE"):
            reason = "linked" if item["linked_prs"] else analysis.hide
            errors.append(f"{item['verdict']:<10} {'shown' if kept else f'hidden {reason}':<18} {item['repo']}#{item['number']} {item['title'][:70]}")

    total = sum(count for (_, kept), count in shown.items() if kept)
    free = shown["FREE", True]
    print(f"{len(snapshot['issues'])} issues, {total} shown, {free} of them free")
    print(f"Precision {free / total:.0%}, recall {free / (free + shown['FREE', False]):.0%}")
    print(f"Shown by mistake: {shown['TAKEN', True]} taken, {shown['NOT_A_TASK', True]} not a task")
    if show_errors:
        print("\n".join(errors))

asyncio.run(main(sys.argv[1], "--errors" in sys.argv))
