"""
file: queries.py
importable: PULL_REQUESTS_QUERY, COMMITS_QUERY, PULL_REQUEST_PAGE_SIZE, COMMIT_PAGE_SIZE, INTERACTION_LIMIT

GraphQL query texts sent to GitHub. PULL_REQUESTS_QUERY returns one page of a
repo's pull requests, newest first, with everything parsing.py needs to build
a PullRequest. It takes owner, name, pageSize, interactions and cursor as
variables, cursor is left empty for the first page.

COMMITS_QUERY returns one page of the commits on a repo's default branch since
a given time, newest first. It takes owner, name, since, pageSize and cursor.
defaultBranchRef comes back empty for a repo that has no commits at all.

description: The GraphQL queries the app sends to GitHub.
"""

# --- Limits ---

# Fifty pull requests with thirty comments and thirty reviews each stays far
# from GitHub's timeout and costs about two points of the hourly quota.
PULL_REQUEST_PAGE_SIZE = 50

# A commit node is small, so GitHub's own maximum is used. A page took one to
# three seconds and one point of the quota when this was measured.
COMMIT_PAGE_SIZE = 100

# Comments arrive oldest first and the first maintainer response is almost
# always among the first few. A pull request where a maintainer only writes
# after thirty other comments is missed and may count as ignored.
INTERACTION_LIMIT = 30

# --- Queries ---

# Only the last closed event is asked for, a pull request can be closed,
# reopened and closed again and only the last close decides the outcome.
# Bots come without the [bot] suffix in GraphQL, so __typename is asked for
# every account and parsing.py uses it to spot them.
PULL_REQUESTS_QUERY = """
query ($owner: String!, $name: String!, $pageSize: Int!, $interactions: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: $pageSize, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        createdAt
        mergedAt
        closedAt
        authorAssociation
        author {
          __typename
          login
        }
        comments(first: $interactions) {
          nodes {
            author {
              __typename
              login
            }
            authorAssociation
            createdAt
          }
        }
        reviews(first: $interactions) {
          nodes {
            author {
              __typename
              login
            }
            authorAssociation
            submittedAt
          }
        }
        timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
          nodes {
            ... on ClosedEvent {
              actor {
                __typename
                login
              }
              closer {
                __typename
              }
            }
          }
        }
      }
    }
  }
}
"""

# GitHub applies since on its side and compares it with the committed date, so
# every node that comes back is already inside the window. parents is asked
# for so client.py can drop merge commits. The git author name is asked for
# next to the account because the account is empty when the commit email is
# not linked to anyone, and parsing.py then looks at the name for [bot].
COMMITS_QUERY = """
query ($owner: String!, $name: String!, $since: GitTimestamp!, $pageSize: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: $pageSize, after: $cursor, since: $since) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              committedDate
              parents {
                totalCount
              }
              author {
                name
                user {
                  login
                }
              }
            }
          }
        }
      }
    }
  }
}
"""
