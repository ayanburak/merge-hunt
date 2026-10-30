"""
file: queries.py
importable: PULL_REQUESTS_QUERY, COMMITS_QUERY, SEARCH_QUERY, PULL_REQUEST_COUNT_QUERY, ISSUES_QUERY, PULL_REQUEST_PAGE_SIZE, COMMIT_PAGE_SIZE,
    SEARCH_PAGE_SIZE, ISSUE_PAGE_SIZE, INTERACTION_LIMIT, LABEL_LIMIT, TOPIC_LIMIT, ISSUE_COMMENT_LIMIT

GraphQL query texts sent to GitHub. PULL_REQUESTS_QUERY returns one page of a
repo's pull requests, newest first, with everything parsing.py needs to build
a PullRequest. It takes owner, name, pageSize, interactions, labels and
cursor as variables, cursor is left empty for the first page.

COMMITS_QUERY returns one page of the commits on a repo's default branch since
a given time, newest first. It takes owner, name, since, pageSize and cursor.
defaultBranchRef comes back empty for a repo that has no commits at all.

SEARCH_QUERY returns one page of GitHub's repo search with everything
parsing.py needs to build a Repo. It takes query, the search text with its
qualifiers, pageSize, topics and cursor.

PULL_REQUEST_COUNT_QUERY only asks for the number of results of an issue
search, it takes query and the search text should hold repo and is:pr.

ISSUES_QUERY returns one page of a repo's open issues, newest first by the order
field. It takes owner, name, issueLabels, order, pageSize, labels, comments and
cursor. Empty issueLabels means every issue, otherwise an issue has to carry at
least one of them. order is UPDATED_AT or CREATED_AT.

description: The GraphQL queries the app sends to GitHub.
"""

# --- Limits ---

# Fifty pull requests with thirty comments and thirty reviews each stays far
# from GitHub's timeout and costs about two points of the hourly quota.
PULL_REQUEST_PAGE_SIZE = 50

# A commit node is small, so GitHub's own maximum is used. A page took one to
# three seconds and one point of the quota when this was measured.
COMMIT_PAGE_SIZE = 100

# With every field of a Repo, a page of fifty took 9.5 seconds when it was
# measured, right under GitHub's ten second limit, and a hundred failed with
# 502. Twenty five took four seconds. Without topics, the open issue count and
# the latest release fifty took three seconds, those nested fields are the slow
# part.
SEARCH_PAGE_SIZE = 25

# Comments arrive oldest first and the first maintainer response is almost
# always among the first few. A pull request where a maintainer only writes
# after thirty other comments is missed and may count as ignored.
INTERACTION_LIMIT = 30

# Only label names are asked for, metrics.py looks for spam labels like
# rejected AI among them. Ten leaves room for the usual topic labels next to it.
LABEL_LIMIT = 10

# Topics are kept for the user's topic filter, ten covers what most repos set.
TOPIC_LIMIT = 10

# With bodies and five comments each, a page of eighteen issues took two seconds
# and two points of the quota when it was measured.
ISSUE_PAGE_SIZE = 50

# The newest comments are the ones that tell whether someone took the issue.
ISSUE_COMMENT_LIMIT = 5

# --- Queries ---

# Only the last closed event is asked for, a pull request can be closed,
# reopened and closed again and only the last close decides the outcome.
# Bots come without the [bot] suffix in GraphQL, so __typename is asked for
# every account and parsing.py uses it to spot them.
PULL_REQUESTS_QUERY = """
query ($owner: String!, $name: String!, $pageSize: Int!, $interactions: Int!, $labels: Int!, $cursor: String) {
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
        labels(first: $labels) {
          nodes {
            name
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

# type REPOSITORY makes every node a Repository, the inline fragment is still
# needed because search nodes are a union type in GraphQL. Only open issues are
# counted, pull requests live in their own connection and do not end up here.
SEARCH_QUERY = """
query ($query: String!, $pageSize: Int!, $topics: Int!, $cursor: String) {
  search(query: $query, type: REPOSITORY, first: $pageSize, after: $cursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on Repository {
        databaseId
        name
        owner {
          login
        }
        stargazerCount
        forkCount
        pushedAt
        primaryLanguage {
          name
        }
        licenseInfo {
          spdxId
        }
        repositoryTopics(first: $topics) {
          nodes {
            topic {
              name
            }
          }
        }
        issues(states: OPEN) {
          totalCount
        }
        latestRelease {
          publishedAt
        }
      }
    }
  }
}
"""

# first: 0 asks for no results at all, only the count. One call took about
# half a second when it was measured in October 2026.
PULL_REQUEST_COUNT_QUERY = """
query ($query: String!) {
  search(query: $query, type: ISSUE, first: 0) {
    issueCount
  }
}
"""

# GitHub reads the labels as an or, an issue with any of them comes back. first
# is required on assignees and on the pull requests, only totalCount is used.
# includeClosedPrs is off so a rejected attempt does not make an issue look
# taken. bodyText is the plain text, nothing later needs the markdown.
ISSUES_QUERY = """
query ($owner: String!, $name: String!, $issueLabels: [String!], $order: IssueOrderField!, $pageSize: Int!, $labels: Int!, $comments: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(
      states: OPEN
      labels: $issueLabels
      first: $pageSize
      after: $cursor
      orderBy: {field: $order, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        title
        bodyText
        createdAt
        updatedAt
        author {
          __typename
          login
        }
        labels(first: $labels) {
          nodes {
            name
          }
        }
        assignees(first: 1) {
          totalCount
        }
        closedByPullRequestsReferences(first: 1, includeClosedPrs: false) {
          totalCount
        }
        comments(last: $comments) {
          totalCount
          nodes {
            author {
              __typename
              login
            }
            authorAssociation
            createdAt
            bodyText
          }
        }
      }
    }
  }
}
"""
