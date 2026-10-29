"""
file: parsing.py
importable: parse_pull_request, parse_commit, GHOST

parse_pull_request turns one pull request node from PULL_REQUESTS_QUERY into
a PullRequest, parse_commit turns one commit node from COMMITS_QUERY into a
Commit. It does no network work and no filtering, the window and the
bot rules are applied later by the caller and by metrics.py.

description: Converts GitHub's GraphQL answers into domain objects.
"""

# NOTE: GitHub can also close a pull request from a project workflow, the
# closer is then a ProjectV2 and not a commit or a pull request. That close is
# not treated as accepted, closed_via stays empty and the closer account
# decides the outcome like any other close.

from datetime import datetime

from merge_hunt.domain.models import Actor, CloseSource, Commit, Interaction, PullRequest

# --- Accounts ---

# GraphQL can return an empty account where the person deleted their profile.
# It is filled with GitHub's own ghost account so closed_by only stays empty
# when there was no closed event at all. That happens too, GitHub hides the
# closed event of some accounts, most likely the ones it flagged as spam.
GHOST = Actor(login="ghost", is_bot=False)

_CLOSERS = {
    "Commit": CloseSource.COMMIT,
    "PullRequest": CloseSource.PULL_REQUEST,
}

# GitHub marks bot accounts with the Bot type, the login alone does not tell.
def _actor(node: dict | None) -> Actor:
    if node is None:
        return GHOST
    return Actor(login=node["login"], is_bot=node["__typename"] == "Bot")

# GitHub sends times as ISO text ending in Z, fromisoformat reads that as UTC
# from Python 3.11 on, so the result is timezone aware like models.py expects.
def _time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)

# --- Pull Requests ---

# The closed event is read only for a pull request that is closed now. An open
# one may still carry an event from an earlier close that was reopened since.
def _closing(node: dict) -> tuple[Actor | None, CloseSource | None]:
    events = node["timelineItems"]["nodes"]
    if node["closedAt"] is None or not events:
        return None, None
    event = events[-1]
    closer = event.get("closer")
    via = _CLOSERS.get(closer["__typename"]) if closer else None
    return _actor(event.get("actor")), via

# A review with no submittedAt is still pending, the author has never seen it
# so it can not be a response.
def parse_pull_request(node: dict) -> PullRequest:
    """Builds a PullRequest from one node of PULL_REQUESTS_QUERY.

    Args:
        node: A single item of pullRequests.nodes, as GitHub returned it.

    Returns:
        The pull request with its comments and submitted reviews as
        interactions and its label names.
    """
    comments = [
        Interaction(_actor(item["author"]), item["authorAssociation"], _time(item["createdAt"]))
        for item in node["comments"]["nodes"]
    ]
    reviews = [
        Interaction(_actor(item["author"]), item["authorAssociation"], _time(item["submittedAt"]))
        for item in node["reviews"]["nodes"]
        if item["submittedAt"] is not None
    ]
    closed_by, closed_via = _closing(node)
    return PullRequest(
        number=node["number"],
        author=_actor(node["author"]),
        author_association=node["authorAssociation"],
        created_at=_time(node["createdAt"]),
        merged_at=_time(node["mergedAt"]),
        closed_at=_time(node["closedAt"]),
        closed_by=closed_by,
        closed_via=closed_via,
        interactions=tuple(comments + reviews),
        labels=tuple(item["name"] for item in node["labels"]["nodes"]),
    )

# --- Commits ---

# On a commit GitHub returns even a bot as a User, dependabot comes back as
# dependabot[bot] with the User type. So unlike pull requests the [bot] at the
# end of the name decides. When the commit email is not linked to any account
# user is empty, then git's own author name is the only hint left.
def _commit_author(node: dict | None) -> Actor:
    if node is None:
        return GHOST
    user = node["user"]
    if user is None:
        return Actor(login=GHOST.login, is_bot=(node["name"] or "").endswith("[bot]"))
    return Actor(login=user["login"], is_bot=user["login"].endswith("[bot]"))

def parse_commit(node: dict) -> Commit:
    """Builds a Commit from one node of COMMITS_QUERY.

    It does not look at parents, skipping merge commits is up to the caller.

    Args:
        node: A single item of history.nodes, as GitHub returned it.

    Returns:
        The commit with its author and the time it landed on the branch.
    """
    return Commit(author=_commit_author(node["author"]), committed_at=_time(node["committedDate"]))
