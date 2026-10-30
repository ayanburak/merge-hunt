/**
 * file: src/components/columns.ts
 * importable: COLUMNS, Column, ColumnKey, FILTER_LABELS
 * description: The four measures shown as filters and as table columns.
 */

import type { ProfileSettings, ScoredRepo } from "../api/client";

export type ColumnKey = "merge_rate" | "ignored_rate" | "response_days" | "commits";

// end is the side of the backend's Range this filter sets, the other side stays as it is.
// The stored value is in the backend's own unit, scale turns it into the one on screen.
export type Column = {
  key: ColumnKey;
  label: string;
  end: "low" | "high";
  word: string;
  unit: string;
  scale: number;
  step: number;
  curve: number;
  show: (value: number) => string;
  value: (item: ScoredRepo) => number | null;
  sample: (item: ScoredRepo) => string;
};

const percent = (value: number) => `${Math.round(value * 100)}%`;
const days = (value: number) => (value < 9.95 ? `${value.toFixed(1)} d` : `${Math.round(value)} d`);

export const COLUMNS: Column[] = [
  {
    key: "merge_rate", label: "Merge rate", end: "low", word: "At least", unit: "%", scale: 100, step: .01, curve: 1, show: percent,
    value: (item) => item.metrics.merge_rate,
    sample: ({ metrics }) => `${metrics.accepted}/${metrics.accepted + metrics.rejected}`,
  },
  {
    key: "ignored_rate", label: "Ignored", end: "high", word: "At most", unit: "%", scale: 100, step: .01, curve: 1, show: percent,
    value: (item) => item.metrics.ignored_rate,
    sample: ({ metrics }) => `${metrics.ignored}/${metrics.evaluable}`,
  },
  {
    key: "response_days", label: "First response", end: "high", word: "At most", unit: "days", scale: 1, step: .5, curve: .5, show: days,
    value: (item) => item.metrics.median_response_days,
    sample: ({ metrics }) => `${metrics.responded} PRs`,
  },
  {
    key: "commits", label: "Commits", end: "low", word: "At least", unit: "", scale: 1, step: 1, curve: .5,
    show: (value) => String(Math.round(value)),
    value: (item) => item.commit_count,
    sample: () => "",
  },
];

export const FILTER_LABELS: Record<string, string> = {
  commits: "commits",
  merge_rate: "merge rate",
  ignored_rate: "ignored",
  response_days: "first response",
  insufficient_data: "too little data",
};

// The top of each slider. The rates end at one by nature, the two others at what makes sense
// for the repos on screen.
export function topOf(column: Column, repos: ScoredRepo[]): number {
  if (column.key === "response_days") return 30;
  if (column.key === "commits") return Math.max(100, Math.ceil(Math.max(...repos.map((item) => item.commit_count)) / 50) * 50);
  return 1;
}

export function rangeOf(settings: ProfileSettings, column: Column): number | null {
  return settings[column.key][column.end] ?? null;
}
