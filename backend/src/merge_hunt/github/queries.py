"""
file: queries.py
importable: PULL_REQUESTS_QUERY, PAGE_SIZE, INTERACTION_LIMIT

GraphQL query texts sent to GitHub. PULL_REQUESTS_QUERY returns one page of a
repo's pull requests, newest first, with everything parsing.py needs to build
a PullRequest. It takes owner, name, pageSize, interactions and cursor as
variables, cursor is left empty for the first page.

description: The GraphQL queries the app sends to GitHub.
"""

# --- Limits ---

# Fifty pull requests with thirty comments and thirty reviews each stays far
# from GitHub's timeout and costs about two points of the hourly quota.
PAGE_SIZE = 50

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
