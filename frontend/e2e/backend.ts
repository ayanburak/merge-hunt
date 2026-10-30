/**
 * file: e2e/backend.ts
 * importable: FakeBackend, scanned, scored
 *
 * Answers every request the page sends to the backend, so a test needs neither Python nor a
 * database. A test sets the scans and repos it wants and reads requests to see what was asked.
 *
 * description: A stand-in for the backend inside the browser, typed by the real schema.
 */

// The answers use the types of the real schema, a field that moves in the backend breaks here
// at compile time. A request with no answer here gets a 500 and never leaves the browser, a
// forgotten route must not reach a real backend that happens to be running.

import type { Page, Route } from "@playwright/test";

import type { AnalyzedIssue, ProfileSettings, Repo, RepoIssues, ScanDetail, ScanRequest, ScoredRepo } from "../src/api/client";

const API = "http://localhost:8000";

const HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "*",
  "access-control-allow-headers": "*",
};

const SCAN_DEFAULTS: ScanRequest = {
  language: null,
  min_stars: 200,
  max_stars: 5000,
  pushed_within_days: 90,
  topic: null,
  license: null,
  min_good_first_issues: 1,
  min_help_wanted_issues: 0,
  max_candidates: 100,
  sort: "stars-desc",
  min_prs: 30,
  max_prs: 500,
  window_days: 180,
  commit_days: 90,
  issue_labels: ["good first issue", "help wanted"],
};

const PROFILE: ProfileSettings = {
  metrics: {
    external_until_merges: 1,
    extra_bots: [],
    excluded_labels: ["spam", "rejected AI", "invalid"],
    ignored_after_days: 30,
    window_days: 180,
    min_sample: 10,
    commit_days: 90,
  },
  commits: { low: 10, high: null },
  merge_rate: { low: 0.5, high: null },
  ignored_rate: { low: null, high: 0.4 },
  response_days: { low: null, high: null },
  weights: { merge_rate: 0.4, ignored_rate: 0.25, response_days: 0.2, commits: 0.15 },
  commit_target: 60,
  hide_insufficient: false,
};

export function scanned(changes: Partial<ScanDetail> = {}): ScanDetail {
  return {
    id: 1,
    status: "DONE",
    stage: "ISSUES",
    started_at: "2026-10-30T14:18:51Z",
    finished_at: "2026-10-30T14:21:29Z",
    options: { discovery: { language: "Python", min_stars: 200, max_stars: 5000 }, window_days: 180, commit_days: 90 },
    candidates: 25,
    skipped: 16,
    done: 9,
    failed: 0,
    waiting_until: null,
    error: null,
    failures: [],
    ...changes,
  };
}

export function scored(name: string, id: number, changes: Partial<ScoredRepo> = {}): ScoredRepo {
  const repo: Repo = {
    id,
    owner: "acme",
    name,
    stars: 1200,
    forks: 80,
    language: "Python",
    license: "MIT",
    topics: [],
    open_issues: 40,
    pushed_at: "2026-10-29T10:00:00Z",
    latest_release_at: "2026-10-20T10:00:00Z",
  };
  return {
    repo,
    metrics: {
      accepted: 40,
      rejected: 10,
      merge_rate: 0.8,
      ignored: 3,
      evaluable: 50,
      ignored_rate: 0.06,
      responded: 45,
      median_response_days: 1.5,
    },
    commit_count: 60,
    score: 0.8,
    failed_filters: [],
    insufficient: false,
    window_covered: true,
    free_issues: 1,
    ...changes,
  };
}

function issueOf(number: number, title: string): AnalyzedIssue {
  return {
    issue: {
      number,
      title,
      body: "Something is wrong.",
      author: { login: "someone", is_bot: false },
      created_at: "2026-10-01T10:00:00Z",
      updated_at: "2026-10-20T10:00:00Z",
      labels: ["good first issue"],
      assigned: false,
      linked_prs: 0,
      comment_count: 0,
      comments: [],
    },
    analysis: { claimed_at: null },
    feedback: null,
  };
}

export class FakeBackend {
  scans: ScanDetail[] = [];
  repos: ScoredRepo[] = [];

  // Every request the page sent, method first and in order, like GET /scans.
  readonly requests: string[] = [];

  count(request: string): number {
    return this.requests.filter((item) => item === request).length;
  }

  async attach(page: Page) {
    await page.route(`${API}/**`, (route) => this.answer(route));
  }

  private answer(route: Route) {
    const request = route.request();
    const method = request.method();
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers: HEADERS });

    const path = new URL(request.url()).pathname;
    this.requests.push(`${method} ${path}`);
    const [status, body] = this.reply(method, path);
    return route.fulfill({ status, headers: HEADERS, json: body });
  }

  private reply(method: string, path: string): [number, unknown] {
    const parts = path.split("/").slice(1);
    const scan = this.scans.find((item) => item.id === Number(parts[1]));
    const repo = this.repos.find((item) => item.repo.id === Number(parts[1]));

    if (method === "GET" && path === "/scans") return [200, this.scans];
    if (method === "POST" && path === "/scans") return this.start();
    if (path === "/scans/defaults") return [200, SCAN_DEFAULTS];
    if (path === "/profiles/default") return [200, PROFILE];
    if (path === "/profiles") return [200, []];

    if (parts[0] === "scans") {
      if (!scan) return [404, { detail: `Scan ${parts[1]} does not exist` }];
      return parts[2] === "results" ? [200, this.repos] : [200, scan];
    }
    if (parts[0] === "repos") {
      if (!repo) return [404, { detail: `Repo ${parts[1]} does not exist` }];
      if (parts[2] === "issues") {
        const issues: RepoIssues = { fetched_at: "2026-10-30T14:21:00Z", labels: ["good first issue"], issues: [issueOf(12, "Fix the broken link")] };
        return [200, issues];
      }
      return [200, repo.repo];
    }
    return [500, { detail: `The fake backend has no answer for ${method} ${path}` }];
  }

  private start(): [number, unknown] {
    const scan = scanned({ id: this.scans.length + 1, status: "RUNNING", stage: "ENRICHMENT", finished_at: null, done: 2, candidates: 10, skipped: 4 });
    this.scans.unshift(scan);
    return [202, { id: scan.id }];
  }
}
