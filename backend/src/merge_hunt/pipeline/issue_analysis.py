"""
file: issue_analysis.py
importable: IssueAnalyzer, RuleIssueAnalyzer

The fifth stage of the scan line. An IssueAnalyzer looks at one issue and says
what it thinks about it as an IssueAnalysis. It runs when issues are read and
not when they are fetched, so a better analyzer works on saved data right away.
RuleIssueAnalyzer is the plain version, it reads the labels, the title and the
last comments. main.py picks which one is used.

description: Decides what the text of an issue says about it being free to take.
"""

# NOTE: The patterns only know English and they were written against
# evals/issue_status/dev.json, evaluate.py in that folder gives their score. They
# must never be tuned on holdout.json. A language model is meant to take the
# cases they miss, a discussion written as a plain sentence for one.

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from merge_hunt.domain.issues import HideReason, IssueAnalysis
from merge_hunt.domain.models import Issue, IssueComment

# --- Rule ---

# Someone taking an issue says so at the start of the comment, whatever follows
# it can be a long plan. The same words deep in a comment are usually a
# technical discussion.
CLAIM_HEAD = 250

# A claim with nothing after it for this long usually means the person left, so
# the issue stays in the list and only carries the warning.
CLAIM_HIDE_DAYS = 90

MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

# The first branch needs a speaker close to a verb, like I'd like to work on
# this. The word boundary in front matters, without it manim and still match
# as im and ill.
_CLAIM = re.compile(
    r"\b(?:i['’]?m|i am|i['’]?ll|i will|i['’]?d like to|i['’]?d love to|can i|could i|let me|may i|i plan to|i can)\b.{0,40}\b(?:work|take|pick|tackle|assign|fix|contribute|on (?:it|this)\b)"
    r"|\bassign (?:it |this )?(?:to )?me\b"
    r"|\bworking on (?:it|this)\b"
    r"|\b(?:happy|glad) to (?:pick|take|work|send|open|submit|help with a pr)\b"
    r"|\bpicked (?:it|this) up\b"
    r"|\bstill available\b"
    r"|\bi['’]?ve (?:investigated|reproduced)\b"
    r"|\b(?:from|is|am) implementing\b"
    r"|\bi have an? .{0,30}\bfix\b"
    r"|\b(?:opened|raised a fix in) #\d"
    r"|\bpr(?: \d+| #\d+)? (?:is up|with the fix)\b",
    re.IGNORECASE | re.DOTALL,
)

# A maintainer telling someone by name to go on means the issue has an owner.
_APPROVAL = re.compile(r"@\S+.{0,40}\b(?:go ahead|feel free to (?:make|open|send|submit)|please do)\b", re.IGNORECASE | re.DOTALL)

_RELEASE = re.compile(r"\b(?:stepping back|unassign(?:ing)? myself|can(?:no|['’])t continue|no longer working on)\b", re.IGNORECASE)

_SOLVED = re.compile(
    r"\b(?:has|have) been (?:fixed|resolved)\b"
    r"|\b(?:already|appears to be|seems to be) (?:fixed|resolved|implemented)\b"
    r"|\bno longer (?:applicable|relevant|reproduces|needed)\b"
    r"|\bcan be closed\b"
    r"|\bclose this issue\b"
    r"|\b(?:fixed|closed) by #?\w",
    re.IGNORECASE,
)

# Compared after folding case and turning - and _ into spaces.
_NOT_READY_LABELS = ("needs discussion", "discussion", "stale", "blocked", "on hold", "wontfix", "wont fix", "duplicate", "invalid", "question")

_NOT_A_TASK = re.compile(
    r"\?\s*$"
    r"|^\W*(?:question|security|rfc|discussion|meta)\b"
    r"|\b(?:survey|tracking issue|roadmap)\b"
    r"|\beveryone is welcome\b",
    re.IGNORECASE,
)

def _is_not_ready(label: str) -> bool:
    folded = label.lower().replace("-", " ").replace("_", " ").replace("'", "")
    return any(name in folded for name in _NOT_READY_LABELS)

def _is_claim(comment: IssueComment) -> bool:
    head = comment.body[:CLAIM_HEAD]
    return _CLAIM.search(head) is not None or (comment.association in MAINTAINERS and _APPROVAL.search(head) is not None)

# Comments are oldest first, so walking them backwards finds the latest claim.
# Someone stepping back cancels whatever came before. Bots are skipped, their
# templates are full of phrases like assign me.
def _latest_claim(issue: Issue) -> datetime | None:
    for comment in reversed(issue.comments):
        if comment.author.is_bot:
            continue
        if _RELEASE.search(comment.body):
            return None
        if _is_claim(comment):
            return comment.at
    return None

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

# The clock is passed in so the evaluation can read a snapshot from the day it
# was taken. The checks go from the surest to the weakest, the reason shown is
# the first that fits.
class RuleIssueAnalyzer(IssueAnalyzer):
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    async def analyze(self, issue: Issue) -> IssueAnalysis:
        claimed_at = _latest_claim(issue)
        return IssueAnalysis(claimed_at, self._hide_reason(issue, claimed_at))

    def _hide_reason(self, issue: Issue, claimed_at: datetime | None) -> HideReason | None:
        if any(_is_not_ready(label) for label in issue.labels):
            return HideReason.NOT_READY
        if _NOT_A_TASK.search(issue.title):
            return HideReason.NOT_A_TASK
        if issue.comments and _SOLVED.search(issue.comments[-1].body):
            return HideReason.SOLVED
        if claimed_at is not None and self._clock() - claimed_at <= timedelta(days=CLAIM_HIDE_DAYS):
            return HideReason.CLAIMED
        return None
