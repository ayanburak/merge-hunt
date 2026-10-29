"""
file: test_parsing.py

Tests for github/parsing.py. The nodes are written by hand in the shape
PULL_REQUESTS_QUERY and COMMITS_QUERY return, so nothing here talks to GitHub.

description: Checks that GitHub's pull request and commit nodes become the right domain objects.
"""

from datetime import UTC, datetime

from merge_hunt.domain.models import Actor, CloseSource, Interaction
from merge_hunt.github.parsing import GHOST, parse_commit, parse_pull_request

# --- Helpers ---

def account(login: str, typename: str = "User") -> dict:
    return {"__typename": typename, "login": login}

# An open pull request by an outside user with no comments, each test only
# fills in what it is about.
def make_node(**fields) -> dict:
    node = {
        "number": 7,
        "createdAt": "2026-09-01T10:00:00Z",
        "mergedAt": None,
        "closedAt": None,
        "authorAssociation": "NONE",
        "author": account("alice"),
        "comments": {"nodes": []},
        "reviews": {"nodes": []},
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
    }
    node.update(fields)
    return node

def closed_event(actor: dict | None, closer: str | None = None) -> dict:
    return {"nodes": [{"actor": actor, "closer": {"__typename": closer} if closer else None}]}

# --- Fields ---

def test_reads_basic_fields_as_utc():
    pr = parse_pull_request(make_node(mergedAt="2026-09-03T08:30:00Z", closedAt="2026-09-03T08:30:00Z"))
    assert pr.number == 7
    assert pr.author == Actor("alice", False)
    assert pr.author_association == "NONE"
    assert pr.created_at == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert pr.merged_at == datetime(2026, 9, 3, 8, 30, tzinfo=UTC)
    assert pr.created_at.tzinfo is not None

def test_bot_type_marks_account_as_bot():
    pr = parse_pull_request(make_node(author=account("dependabot", "Bot")))
    assert pr.author == Actor("dependabot", True)

def test_missing_author_becomes_ghost():
    assert parse_pull_request(make_node(author=None)).author == GHOST

def test_reads_label_names():
    pr = parse_pull_request(make_node(labels={"nodes": [{"name": "rejected AI"}, {"name": "docs"}]}))
    assert pr.labels == ("rejected AI", "docs")

# --- Interactions ---

def test_comments_and_submitted_reviews_become_interactions():
    node = make_node(
        comments={"nodes": [{"author": account("maya"), "authorAssociation": "MEMBER", "createdAt": "2026-09-02T09:00:00Z"}]},
        reviews={"nodes": [{"author": account("omar"), "authorAssociation": "OWNER", "submittedAt": "2026-09-04T09:00:00Z"}]},
    )
    assert parse_pull_request(node).interactions == (
        Interaction(Actor("maya", False), "MEMBER", datetime(2026, 9, 2, 9, tzinfo=UTC)),
        Interaction(Actor("omar", False), "OWNER", datetime(2026, 9, 4, 9, tzinfo=UTC)),
    )

def test_pending_review_is_skipped():
    node = make_node(reviews={"nodes": [{"author": account("maya"), "authorAssociation": "MEMBER", "submittedAt": None}]})
    assert parse_pull_request(node).interactions == ()

def test_comment_from_deleted_account_keeps_ghost():
    node = make_node(comments={"nodes": [{"author": None, "authorAssociation": "NONE", "createdAt": "2026-09-02T09:00:00Z"}]})
    assert parse_pull_request(node).interactions[0].actor == GHOST

# --- Closing ---

def test_closed_by_person():
    pr = parse_pull_request(make_node(closedAt="2026-09-05T00:00:00Z", timelineItems=closed_event(account("maya"))))
    assert pr.closed_by == Actor("maya", False)
    assert pr.closed_via is None

def test_closed_by_commit_and_pull_request():
    for closer, source in (("Commit", CloseSource.COMMIT), ("PullRequest", CloseSource.PULL_REQUEST)):
        node = make_node(closedAt="2026-09-05T00:00:00Z", timelineItems=closed_event(account("maya"), closer))
        assert parse_pull_request(node).closed_via is source

# A project workflow close is not a sign that the change landed.
def test_unknown_closer_type_is_not_a_close_source():
    node = make_node(closedAt="2026-09-05T00:00:00Z", timelineItems=closed_event(account("maya"), "ProjectV2"))
    assert parse_pull_request(node).closed_via is None

def test_closed_event_without_actor_becomes_ghost():
    pr = parse_pull_request(make_node(closedAt="2026-09-05T00:00:00Z", timelineItems=closed_event(None)))
    assert pr.closed_by == GHOST

def test_closed_without_event_leaves_closer_empty():
    pr = parse_pull_request(make_node(closedAt="2026-09-05T00:00:00Z"))
    assert (pr.closed_by, pr.closed_via) == (None, None)

# The event is left over from a close that was reopened afterwards.
def test_open_pull_request_ignores_old_closed_event():
    pr = parse_pull_request(make_node(timelineItems=closed_event(account("maya"))))
    assert (pr.closed_by, pr.closed_via) == (None, None)

# --- Commits ---

# A commit by a linked user account, the shape one item of history.nodes has.
def commit_node(name: str = "alice", login: str | None = "alice") -> dict:
    return {
        "committedDate": "2026-10-20T11:36:16Z",
        "parents": {"totalCount": 1},
        "author": {"name": name, "user": {"login": login} if login else None},
    }

def test_commit_reads_author_and_time_as_utc():
    commit = parse_commit(commit_node())
    assert commit.author == Actor("alice", False)
    assert commit.committed_at == datetime(2026, 10, 20, 11, 36, 16, tzinfo=UTC)

def test_commit_bot_login_marks_bot():
    commit = parse_commit(commit_node("dependabot[bot]", "dependabot[bot]"))
    assert commit.author == Actor("dependabot[bot]", True)

def test_commit_without_account_becomes_ghost():
    assert parse_commit(commit_node("Jane Doe", None)).author == GHOST

def test_commit_without_account_but_bot_name_is_bot():
    commit = parse_commit(commit_node("renovate[bot]", None))
    assert commit.author == Actor("ghost", True)
