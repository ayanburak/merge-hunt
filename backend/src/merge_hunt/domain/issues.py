"""
file: issues.py
importable: IssueFilter, IssueAnalysis, AnalyzedIssue, RepoIssues, FeedbackValue, is_shown

What the app decides about an issue, on top of the raw Issue in models.py.
IssueFilter holds what the user wants to hide and is_shown applies it to one
issue. IssueAnalysis is what an analyzer found out, AnalyzedIssue puts it next
to the issue and the user's feedback and RepoIssues is the list the API gives
for a repo.

description: Filtering and analysis results for the issues of a repo.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from merge_hunt.domain.models import Issue

# --- Values ---

# What the user said about an issue. It is saved for the ranking that will learn
# from it later, today SKIPPED also hides the issue.
class FeedbackValue(StrEnum):
    INTERESTED = "INTERESTED"
    SKIPPED = "SKIPPED"

# --- Filter ---

# There is no age limit by default. In October 2026 only one of the thirty two
# free issues in fifteen repos was younger than a year, a limit would empty the
# list. Sorting by last update does more for the user here.
@dataclass(frozen=True, slots=True)
class IssueFilter:
    hide_assigned: bool = True
    hide_linked: bool = True
    max_age_days: int | None = None
    show_skipped: bool = False

def is_shown(issue: Issue, options: IssueFilter, now: datetime, feedback: FeedbackValue | None = None) -> bool:
    """Tells whether an issue passes the user's filter.

    Args:
        issue: The issue.
        options: What the user wants to hide.
        now: The moment the age is counted from.
        feedback: What the user said about this issue, if anything.

    Returns:
        False when the issue is assigned, has a linked pull request, is too
        old or was skipped and the filter hides that, True otherwise.
    """
    if options.hide_assigned and issue.assigned:
        return False
    if options.hide_linked and issue.linked_prs > 0:
        return False
    if not options.show_skipped and feedback is FeedbackValue.SKIPPED:
        return False
    return options.max_age_days is None or now - issue.created_at <= timedelta(days=options.max_age_days)

# --- Analysis ---

# claimed_at is the time of the latest comment that sounds like someone is
# taking the issue, the UI can tell a claim from last week from one from 2021.
# It is only a warning, the rule can be wrong and the issue stays in the list.
@dataclass(frozen=True, slots=True)
class IssueAnalysis:
    claimed_at: datetime | None = None

@dataclass(frozen=True, slots=True)
class AnalyzedIssue:
    issue: Issue
    analysis: IssueAnalysis
    feedback: FeedbackValue | None = None

# fetched_at is empty when this repo's issues were never fetched, the UI can
# tell that from a repo that simply has none. labels is what the fetch asked for.
@dataclass(frozen=True, slots=True)
class RepoIssues:
    fetched_at: datetime | None
    labels: tuple[str, ...]
    issues: tuple[AnalyzedIssue, ...]
