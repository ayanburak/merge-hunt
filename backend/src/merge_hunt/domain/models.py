"""
file: models.py
importable: Actor, Interaction, PullRequest, CloseSource, Commit, PullRequestHistory, CommitHistory, Enrichment, RepoRef, Repo,
    Issue, IssueComment, IssueFetch

Raw pull request and commit data, kept the way GitHub reported it. Nothing in here is
computed, the enrichment step fills these from GitHub responses and the
functions in metrics.py read them. Who did what is always kept next to when it
happened, so a change in the user's settings, like a new name in the bot list,
can be applied to old data without fetching it again.

Every class is frozen, an object can not be changed after it is built, and
lists inside are tuples for the same reason. All datetimes are expected to be
timezone aware and in UTC.

PullRequestHistory and CommitHistory are what one fetch brings back, the data
together with the date it was asked from and the date from which it is
complete. Enrichment is both of them as the database gives them back, with
the time they were fetched. RepoRef points at one repo on GitHub, it is what
the enrichment step is given to work on. Repo is what discovery found out
about a repo from GitHub's search, its size, language and activity.

Issue is an open issue as GitHub reported it, with its last comments. Whether
it is really free to take is decided later, IssueFetch is the list one fetch
saved together with the time it was fetched.

description: Raw pull request, commit and issue data that everything else is calculated from.
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
# labels only holds label names, metrics.py drops pull requests carrying one of
# the labels the user excluded, spam closes for example.
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
    labels: tuple[str, ...] = ()

# One commit on the default branch. Merge commits never get here, they only
# record that a pull request was joined and carry no work of their own. Bot
# commits are kept and only marked, so a name the user adds to the bot list
# later still drops them from old data.
@dataclass(frozen=True, slots=True)
class Commit:
    author: Actor
    committed_at: datetime

# --- Fetched Data ---

# id is GitHub's numeric repo id. It stays the same when a repo is renamed or
# moved, owner and name do not.
@dataclass(frozen=True, slots=True)
class RepoRef:
    id: int
    owner: str
    name: str

# Comes from GitHub's search and is saved as it was found, the scoring step
# compares it with the user's ranges. language and license are empty when
# GitHub knows none, license holds the SPDX id like MIT, and NOASSERTION when
# GitHub could not tell which license it is. open_issues counts issues only,
# not pull requests.
@dataclass(frozen=True, slots=True)
class Repo:
    id: int
    owner: str
    name: str
    stars: int
    forks: int
    language: str | None
    license: str | None
    topics: tuple[str, ...]
    open_issues: int
    pushed_at: datetime
    latest_release_at: datetime | None

# requested_since is where the fetch was asked to start. complete_since is
# later than that only when the page limit stopped the fetch early, and the
# enrichment step needs both to tell a cut fetch from a too narrow one.
# prs only holds pull requests opened at or after complete_since.
@dataclass(frozen=True, slots=True)
class PullRequestHistory:
    prs: tuple[PullRequest, ...]
    complete_since: datetime
    requested_since: datetime

# Same as PullRequestHistory, commits only holds commits made at or after
# complete_since.
@dataclass(frozen=True, slots=True)
class CommitHistory:
    commits: tuple[Commit, ...]
    complete_since: datetime
    requested_since: datetime

# Pull requests and commits are always fetched and saved together, so a single
# enriched_at covers both. Each keeps its own complete_since because their
# windows differ.
@dataclass(frozen=True, slots=True)
class Enrichment:
    enriched_at: datetime
    prs: PullRequestHistory
    commits: CommitHistory

# --- Issues ---

# One of the last comments on an issue. The text is kept next to who wrote it,
# deciding what a comment means is the analyzer's work and can change later
# without fetching anything again.
@dataclass(frozen=True, slots=True)
class IssueComment:
    author: Actor
    association: str
    at: datetime
    body: str

# body and the comments hold plain text, GitHub's rendering of the markdown.
# linked_prs counts the open pull requests GitHub knows will close this issue,
# a pull request that only mentions it is not in there. comments is the last
# few, oldest first, and comment_count all of them.
@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str
    author: Actor
    created_at: datetime
    updated_at: datetime
    labels: tuple[str, ...]
    assigned: bool
    linked_prs: int
    comment_count: int
    comments: tuple[IssueComment, ...] = ()

# issues keeps the order GitHub sent, last updated first.
@dataclass(frozen=True, slots=True)
class IssueFetch:
    fetched_at: datetime
    issues: tuple[Issue, ...]
