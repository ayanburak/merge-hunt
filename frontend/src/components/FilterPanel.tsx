/**
 * file: src/components/FilterPanel.tsx
 * importable: FilterPanel
 * description: The filter band above the table, one slider and one typed value for each measure.
 */

// How many repos a filter drops comes straight from failed_filters in the backend's answer, the
// filter rule itself is not repeated here. That is why the words below a slider catch up only
// after the hand stops, the value in the box moves at once.

import { useState } from "react";

import type { ProfileSettings, ScoredRepo } from "../api/client";
import { COLUMNS, rangeOf, topOf } from "./columns";
import type { Column } from "./columns";
import { LimitSlider } from "./LimitSlider";
import styles from "./FilterPanel.module.css";

type Props = {
  settings: ProfileSettings;
  repos: ScoredRepo[];
  onChange: (settings: ProfileSettings) => void;
};

const STEPS = 1000;

// A filter has one side, at the far end of its slider it lets everything through and that is
// what off means. A typed value may go past the end of the scale, only a rate has a real limit.
function Filter({ column, settings, repos, onChange }: Props & { column: Column }) {
  const [typing, setTyping] = useState<string | null>(null);
  const top = topOf(column, repos);
  const value = rangeOf(settings, column);
  const off = value === null;

  const set = (next: number | null) => {
    const clean = next === null ? null : Number(next.toFixed(3));
    onChange({ ...settings, [column.key]: { ...settings[column.key], [column.end]: clean } });
  };

  const onSlide = (position: number) => {
    const raw = (position / STEPS) ** (1 / column.curve) * top;
    const next = Math.round(raw / column.step) * column.step;
    set(next === (column.end === "low" ? 0 : top) ? null : next);
  };

  const onType = (text: string) => {
    const typed = text.replace(",", ".").replace(/[^\d.]/g, "");
    setTyping(typed);
    if (typed === "") return set(null);
    const next = Number(typed) / column.scale;
    set(column.scale === 100 ? Math.min(next, 1) : next);
  };

  const edge = column.end === "low" ? 0 : top;
  const position = Math.round((Math.min(off ? edge : value, top) / top) ** column.curve * STEPS);
  const dropped = repos.filter((item) => item.failed_filters.includes(column.key)).length;
  const effect = off ? "Off" : dropped ? `Drops ${dropped} ${dropped === 1 ? "repo" : "repos"}` : "Drops nothing";

  return (
    <div>
      <div className={styles.name}>{column.label}</div>
      <div className={styles.row}>
        {column.word}
        <input
          inputMode="decimal"
          placeholder="Off"
          aria-label={`${column.label} ${column.word.toLowerCase()}`}
          value={typing ?? (off ? "" : Number((value * column.scale).toFixed(1)))}
          onFocus={() => setTyping(off ? "" : String(Number((value * column.scale).toFixed(1))))}
          onBlur={() => setTyping(null)}
          onChange={(event) => onType(event.target.value)}
        />
        {column.unit}
      </div>
      <LimitSlider
        position={position}
        max={STEPS}
        label={column.label}
        off={off}
        fill={column.end === "low" ? "right" : "left"}
        onChange={onSlide}
      />
      <div className={styles.effect}>{effect}</div>
    </div>
  );
}

export function FilterPanel({ settings, repos, onChange }: Props) {
  const clear = () => {
    const off = { low: null, high: null };
    onChange({ ...settings, merge_rate: off, ignored_rate: off, response_days: off, commits: off });
  };

  return (
    <div className={styles.filters}>
      <div className={styles.intro}>
        <b>Filters</b>
        <p>Drag a slider or type a value. Repos that miss one drop below the line.</p>
        <button type="button" onClick={clear}>Turn all filters off</button>
      </div>
      {COLUMNS.map((column) => (
        <Filter key={column.key} column={column} settings={settings} repos={repos} onChange={onChange} />
      ))}
    </div>
  );
}
