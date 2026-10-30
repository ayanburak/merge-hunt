/**
 * file: src/components/RepoTable.tsx
 * importable: RepoTable, RepoSort, SortKey
 * description: The repos, those passing on the light sheet and those failing below the line.
 */

// The rows are placed by hand with transforms and not by normal flow, that is what lets a repo
// glide across the line when a filter changes. The row heights below have to match the css.
//
// Sorting is the only thing decided here. Who passes and every score come from the backend.

import { useState } from "react";

import type { ProfileSettings, ScoredRepo } from "../api/client";
import { ago, number } from "../format";
import { COLUMNS, FILTER_LABELS } from "./columns";
import styles from "./RepoTable.module.css";

export type SortKey = "score" | "name" | "merge_rate" | "ignored_rate" | "response_days" | "commits";

const ROW = 62;
const OPEN = 160;
const GAP = 76;

const fullName = ({ repo }: ScoredRepo) => `${repo.owner}/${repo.name}`;

// An empty value always sorts to the bottom, a rate nobody can measure is not a good rate.
const SORTERS: Record<SortKey, (a: ScoredRepo, b: ScoredRepo) => number> = {
  score: (a, b) => b.score - a.score,
  name: (a, b) => fullName(a).localeCompare(fullName(b)),
  merge_rate: (a, b) => (b.metrics.merge_rate ?? -1) - (a.metrics.merge_rate ?? -1),
  ignored_rate: (a, b) => (a.metrics.ignored_rate ?? 9) - (b.metrics.ignored_rate ?? 9),
  response_days: (a, b) => (a.metrics.median_response_days ?? 9e9) - (b.metrics.median_response_days ?? 9e9),
  commits: (a, b) => b.commit_count - a.commit_count,
};

function reason(item: ScoredRepo, key: string, settings: ProfileSettings): string {
  const column = COLUMNS.find((entry) => entry.key === key);
  if (!column) return "Too few pull requests to trust its rates, and this profile drops such repos.";
  const value = column.value(item) as number;
  const { low, high } = settings[column.key];
  return low != null && value < low
    ? `${column.label} is ${column.show(value)}, below the ${column.show(low)} you asked for.`
    : `${column.label} is ${column.show(value)}, above the ${column.show(high as number)} you asked for.`;
}

function Detail({ item, settings }: { item: ScoredRepo; settings: ProfileSettings }) {
  const { repo, metrics } = item;

  return (
    <div className={styles.detail}>
      <div className={styles.why}>
        {item.failed_filters.length > 0 ? (
          item.failed_filters.map((key) => <p key={key}>{reason(item, key, settings)}</p>)
        ) : (
          <>
            <p>Passes every filter.</p>
            <p>{metrics.accepted} accepted, {metrics.rejected} rejected, {metrics.ignored} of {metrics.evaluable} left waiting past {settings.metrics.ignored_after_days} days.</p>
          </>
        )}
        {item.insufficient && <p>Fewer than {settings.metrics.min_sample} pull requests behind at least one rate, so that rate stays empty.</p>}
        {!item.window_covered && <p>Stored data does not reach back the full {settings.metrics.window_days} days, so these numbers rest on less time. A new scan fetches the rest.</p>}
      </div>
      <div>
        <h4>Topics</h4>
        <div className={styles.topics}>
          {repo.topics.length > 0 ? repo.topics.slice(0, 8).map((topic) => <span key={topic}>{topic}</span>) : <span>None</span>}
        </div>
        <a className={styles.github} href={`https://github.com/${fullName(item)}`} target="_blank" rel="noreferrer">Open on GitHub</a>
      </div>
      <div>
        <div className={styles.spec}><span>Open issues</span><b>{number(repo.open_issues)}</b></div>
        <div className={styles.spec}><span>Forks</span><b>{number(repo.forks)}</b></div>
        <div className={styles.spec}><span>Latest release</span><b>{repo.latest_release_at ? ago(repo.latest_release_at) : "None"}</b></div>
      </div>
    </div>
  );
}

type RowProps = {
  item: ScoredRepo;
  settings: ProfileSettings;
  under: boolean;
  open: boolean;
  y: number;
  delay: number;
  onToggle: () => void;
};

function Row({ item, settings, under, open, y, delay, onToggle }: RowProps) {
  const { repo } = item;
  const license = repo.license === "NOASSERTION" ? "Other license" : repo.license ?? "No license";

  return (
    <article
      className={styles.row}
      data-under={under ? "" : undefined}
      data-open={open ? "" : undefined}
      style={{ "--y": `${y}px`, "--delay": `${delay}ms` } as React.CSSProperties}
    >
      <button type="button" className={styles.main} aria-expanded={open} onClick={onToggle}>
        <span className={styles.name}>
          <span className={styles.title}><span>{repo.owner}/</span>{repo.name}</span>
          <span className={styles.facts}>
            {item.insufficient && <span className={styles.flag}>Too little data</span>}
            {!item.window_covered && <span className={styles.flag} data-warn="">Short history</span>}
            <span>{number(repo.stars)} stars</span>
            <span>{license}</span>
            <span>pushed {ago(repo.pushed_at)}</span>
          </span>
        </span>
        <span className={styles.score}>{item.score.toFixed(2).replace(/^0/, "")}</span>
        {COLUMNS.map((column) => {
          const value = column.value(item);
          return (
            <span
              key={column.key}
              className={styles.cell}
              data-failed={item.failed_filters.includes(column.key) ? "" : undefined}
              data-none={value === null ? "" : undefined}
            >
              <b>{value === null ? "–" : column.show(value)}</b>
              <small>{column.sample(item)}</small>
            </span>
          );
        })}
      </button>
      <div className={styles.more}>
        <div className={styles.moreInner}>{open && <Detail item={item} settings={settings} />}</div>
      </div>
    </article>
  );
}

type Props = {
  repos: ScoredRepo[];
  settings: ProfileSettings;
  sort: SortKey;
  sieved: boolean;
  staggered: boolean;
};

export function RepoTable({ repos, settings, sort, sieved, staggered }: Props) {
  const [openId, setOpenId] = useState<number | null>(null);

  const order = [...repos].sort(SORTERS[sort]);
  const passing = order.filter((item) => !sieved || item.failed_filters.length === 0);
  const failing = order.filter((item) => sieved && item.failed_filters.length > 0);

  let y = 0;
  const place = (item: ScoredRepo, under: boolean, index: number) => {
    const open = openId === item.repo.id;
    const row = (
      <Row
        key={item.repo.id}
        item={item}
        settings={settings}
        under={under}
        open={open}
        y={y}
        delay={staggered ? Math.min(index, 40) * 14 : 0}
        onToggle={() => setOpenId(open ? null : item.repo.id)}
      />
    );
    y += ROW + (open ? OPEN : 0);
    return row;
  };

  const above = passing.map((item, index) => place(item, false, index));
  const edge = y;
  if (failing.length > 0) y += GAP;
  const below = failing.map((item, index) => place(item, true, index));

  const counts = new Map<string, number>();
  for (const item of failing) {
    for (const key of item.failed_filters) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const reasons = [...counts].sort((a, b) => b[1] - a[1]).map(([key, count]) => `${FILTER_LABELS[key]} ${count}`).join(", ");

  // React would reorder the rows in the page when the order changes, which drops the running
  // transition. A row is found by its id instead, so each one always stays where it is.
  const rows = [...above, ...below].sort((a, b) => Number(a.key) - Number(b.key));

  return (
    <div className={styles.rows} style={{ "--edge": `${edge}px`, height: y + 2 } as React.CSSProperties}>
      <div className={styles.ground} />
      <div className={styles.line} style={{ opacity: failing.length > 0 ? 1 : 0 }}>
        <strong>{failing.length} fell through</strong>
        <span>{reasons}</span>
      </div>
      {rows}
    </div>
  );
}

const SORT_LABELS: [SortKey, string][] = [["name", "Repo"], ["score", "Score"], ...COLUMNS.map((column): [SortKey, string] => [column.key, column.label])];

export function RepoSort({ sort, onChange }: { sort: SortKey; onChange: (sort: SortKey) => void }) {
  return (
    <div className={styles.columns}>
      {SORT_LABELS.map(([key, label]) => (
        <button key={key} type="button" className={styles.label} onClick={() => onChange(key)}>
          {label} {sort === key && <em>↓</em>}
        </button>
      ))}
    </div>
  );
}
