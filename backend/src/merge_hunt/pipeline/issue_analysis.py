"""
file: issue_analysis.py
importable: IssueAnalyzer, RuleIssueAnalyzer

The fifth stage of the scan line. An IssueAnalyzer looks at one issue and says
what it thinks about it as an IssueAnalysis. It runs when issues are read and
not when they are fetched, so a better analyzer works on saved data right away.
RuleIssueAnalyzer is the plain version, it looks for comments where someone
says they are taking the issue. main.py picks which one is used.

description: Decides what the text of an issue says about it being free to take.
"""

# NOTE: The pattern only knows English. In October 2026 it fired on about a
# quarter of the labelled issues in fifteen repos and was wrong on some of them,
# so its result is a warning and never hides an issue. A language model is
# meant to take this over.

import re
from abc import ABC, abstractmethod

from merge_hunt.domain.issues import IssueAnalysis
from merge_hunt.domain.models import Issue

# --- Rule ---

# Someone taking an issue says so in a line or two. A long comment that happens
# to hold these words is almost always a technical discussion.
CLAIM_MAX_LENGTH = 300

# The first branch needs a speaker close to a verb, like I'd like to work on
# this. The word boundary in front matters, without it manim and still match
# as im and ill.
_CLAIM = re.compile(
    r"\b(?:i['’]?m|i am|i['’]?ll|i will|i['’]?d like to|can i|could i|let me|may i)\b.{0,40}\b(?:work|take|pick|tackle|assign|fix|on (?:it|this)\b)"
    r"|\bassign (?:it |this )?(?:to )?me\b"
    r"|\bworking on (?:it|this)\b",
    re.IGNORECASE,
)

# --- Analyzers ---

# async because the model version will take seconds per issue and should not
# stop the server while it thinks.
class IssueAnalyzer(ABC):
    """Looks at one issue and reports what it found."""

    @abstractmethod
    async def analyze(self, issue: Issue) -> IssueAnalysis:
        """Analyzes an issue.

        Args:
            issue: The issue with its last comments.

        Returns:
            What the analyzer found out about it.
        """

# Comments are oldest first, so walking them backwards finds the latest claim.
# Bots are skipped, their templates are full of phrases like assign me.
class RuleIssueAnalyzer(IssueAnalyzer):
    async def analyze(self, issue: Issue) -> IssueAnalysis:
        for comment in reversed(issue.comments):
            if not comment.author.is_bot and len(comment.body) <= CLAIM_MAX_LENGTH and _CLAIM.search(comment.body):
                return IssueAnalysis(claimed_at=comment.at)
        return IssueAnalysis()
