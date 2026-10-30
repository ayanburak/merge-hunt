/**
 * file: src/components/ScanForm.test.ts
 * description: Checks what keeps a scan from starting and how long one is said to take.
 */

import { describe, expect, it } from "vitest";

import type { Scan, ScanRequest } from "../api/client";
import { estimate, problems } from "./ScanForm";

const OPTIONS = {
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
} satisfies Required<ScanRequest>;

// 867 seconds is the real scan of 30 October.
const LAST = {
  candidates: 100,
  started_at: "2026-10-30T11:33:50Z",
  finished_at: "2026-10-30T11:48:17Z",
} as Scan;

describe("problems", () => {
  it("accepts the defaults", () => {
    expect(problems(OPTIONS)).toEqual([]);
  });

  it("accepts equal ends of a range", () => {
    expect(problems({ ...OPTIONS, min_stars: 900, max_stars: 900 })).toEqual([]);
  });

  it("reports ranges in the wrong order", () => {
    expect(problems({ ...OPTIONS, min_stars: 6000 })).toEqual(["The lowest star count is above the highest."]);
    expect(problems({ ...OPTIONS, max_prs: 10 })).toEqual(["The lowest pull request count is above the highest."]);
  });

  it("reports a period of zero days", () => {
    expect(problems({ ...OPTIONS, window_days: 0 })).toEqual(["A period has to be at least 1 day."]);
    expect(problems({ ...OPTIONS, commit_days: 0 })).toEqual(["A period has to be at least 1 day."]);
  });

  it("reports everything at once", () => {
    expect(problems({ ...OPTIONS, min_stars: 6000, max_prs: 10, commit_days: 0 })).toHaveLength(3);
  });
});

describe("estimate", () => {
  it("quotes the last scan when the size is the same", () => {
    expect(estimate(100, LAST)).toBe("The last scan of 100 repos took 14m 27s.");
  });

  it("scales the last scan for another size", () => {
    expect(estimate(250, LAST)).toBe("Roughly 36 min, going by the last scan.");
  });

  it("never says less than a minute", () => {
    expect(estimate(1, { ...LAST, finished_at: "2026-10-30T11:33:51Z" })).toBe("Roughly 1 min, going by the last scan.");
  });

  it("says nothing without a finished scan to go by", () => {
    expect(estimate(100)).toBe("");
    expect(estimate(100, { ...LAST, finished_at: null })).toBe("");
    expect(estimate(100, { ...LAST, candidates: 0 })).toBe("");
  });
});
