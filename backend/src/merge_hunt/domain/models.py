"""
file: models.py
importable: Actor, Interaction, PullRequest, CloseSource, Commit

Raw pull request and commit data, kept the way GitHub reported it. Nothing in here is
computed, the enrichment step fills these from GitHub responses and the
functions in metrics.py read them. Who did what is always kept next to when it
happened, so a change in the user's settings, like a new name in the bot list,
can be applied to old data without fetching it again.

Every class is frozen, an object can not be changed after it is built, and
lists inside are tuples for the same reason. All datetimes are expected to be
timezone aware and in UTC.

description: Raw pull request and commit data that the metrics are calculated from.
"""

# NOTE: Associations are kept as plain strings and not as an enum. GitHub has
# added new values before, MANNEQUIN for imported accounts is one, and an enum
# would make parsing fail on the first value it does not know. metrics.py only
# treats the values it knows as external, anything else counts as internal.

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# --- Values ---

# Only covers a pull request closed by something other than a person. When
# someone clicks close, closed_via stays empty and closed_by says who it was.
class CloseSource(StrEnum):
    COMMIT = "COMMIT"
    PULL_REQUEST = "PULL_REQUEST"

# --- Data ---

# Anyone who did something on a pull request, its author, whoever closed it or
# whoever commented. login sits next to is_bot because some bots run on normal
# accounts and can only be caught by name, through the user's own bot list.
@dataclass(frozen=True, slots=True)
class Actor:
    login: str
    is_bot: bool

# One comment or one review. association is how this person relates to the
# repo, same values as PullRequest.author_association, OWNER, MEMBER and so on.
@dataclass(frozen=True, slots=True)
class Interaction:
    actor: Actor
    association: str
    at: datetime

# A merged pull request also has closed_at set, GitHub closes it while merging.
# Who merged it is left out on purpose, a merge is always a maintainer's call.
@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    author: Actor
    author_association: str
    created_at: datetime
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    closed_by: Actor | None = None
    closed_via: CloseSource | None = None
    interactions: tuple[Interaction, ...] = ()

# One commit on the default branch. Merge commits never get here, they only
# record that a pull request was joined and carry no work of their own. Bot
# commits are kept and only marked, so a name the user adds to the bot list
# later still drops them from old data.
@dataclass(frozen=True, slots=True)
class Commit:
    author: Actor
    committed_at: datetime
