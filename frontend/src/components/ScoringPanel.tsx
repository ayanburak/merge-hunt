/**
 * file: src/components/ScoringPanel.tsx
 * importable: ScoringPanel
 * description: The settings behind the numbers, how metrics are counted and what the score weighs.
 */

import { useState } from "react";

import type { ProfileSettings } from "../api/client";
import { LimitSlider } from "./LimitSlider";
import { NumberBox } from "./NumberBox";
import styles from "./ScoringPanel.module.css";

type Metrics = ProfileSettings["metrics"];
type Weights = ProfileSettings["weights"];

type Props = {
  open: boolean;
  settings: ProfileSettings;
  onChange: (settings: ProfileSettings) => void;
  onReset: () => void;
};

const NUMBERS: [keyof Metrics | "commit_target", string][] = [
  ["window_days", "Pull requests from the last, days"],
  ["commit_days", "Commits from the last, days"],
  ["ignored_after_days", "Ignored after waiting, days"],
  ["min_sample", "Fewest pull requests for a rate"],
  ["external_until_merges", "Outsider until this many merges"],
  ["commit_target", "Commits for full marks"],
];

const WEIGHTS: [keyof Weights, string][] = [
  ["merge_rate", "Merge rate"],
  ["ignored_rate", "Ignored"],
  ["response_days", "First response"],
  ["commits", "Commits"],
];

// A list of short texts, each removed with its cross and added with enter.
function Chips({ items, hint, onChange }: { items: string[]; hint: string; onChange: (items: string[]) => void }) {
  const [text, setText] = useState("");

  return (
    <div className={styles.chips}>
      {items.map((item) => (
        <button key={item} type="button" className={styles.chip} title="Remove" onClick={() => onChange(items.filter((other) => other !== item))}>
          {item}
        </button>
      ))}
      <span className={styles.adder}>
        <input
          placeholder={hint}
          aria-label={hint}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            const added = text.trim();
            if (event.key !== "Enter" || !added) return;
            if (!items.includes(added)) onChange([...items, added]);
            setText("");
          }}
        />
      </span>
    </div>
  );
}

// The four weights need not add up to one, the backend divides by their sum. The percent next
// to each slider shows the share that comes out of that.
export function ScoringPanel({ open, settings, onChange, onReset }: Props) {
  const metrics = (changes: Partial<Metrics>) => onChange({ ...settings, metrics: { ...settings.metrics, ...changes } });
  const total = WEIGHTS.reduce((sum, [key]) => sum + settings.weights[key], 0) || 1;

  return (
    <section className={styles.tune} data-open={open ? "" : undefined} aria-label="Scoring settings">
      <div className={styles.inner}>
        <div className={styles.grid}>
          <div>
            <h3>How metrics are counted</h3>
            {NUMBERS.map(([key, label]) => (
              <label key={key} className={styles.setting}>
                <span>{label}</span>
                {key === "commit_target" ? (
                  <NumberBox className={styles.number} align="right" value={settings.commit_target}
                    onChange={(commit_target) => onChange({ ...settings, commit_target })} />
                ) : (
                  <NumberBox className={styles.number} align="right" value={settings.metrics[key] as number}
                    onChange={(value) => metrics({ [key]: value })} />
                )}
              </label>
            ))}
          </div>

          <div>
            <h3>What the score cares about</h3>
            {WEIGHTS.map(([key, label]) => (
              <div key={key} className={styles.weight}>
                <span>{label}</span>
                <LimitSlider
                  position={settings.weights[key] * 100}
                  max={100}
                  step={5}
                  label={`${label} weight`}
                  fill="left"
                  onChange={(position) => onChange({ ...settings, weights: { ...settings.weights, [key]: position / 100 } })}
                />
                <b>{Math.round((settings.weights[key] / total) * 100)}%</b>
              </div>
            ))}
            <p className={styles.note}>The rates enter the score with their sample size in mind, so 12 of 12 ranks below 200 of 200. The columns always show the plain rate.</p>
          </div>

          <div>
            <h3>Leave out</h3>
            <h4>Pull requests with these labels</h4>
            <Chips items={settings.metrics.excluded_labels} hint="add a label" onChange={(excluded_labels) => metrics({ excluded_labels })} />
            <h4>These accounts count as bots</h4>
            <Chips items={settings.metrics.extra_bots} hint="add a bot" onChange={(extra_bots) => metrics({ extra_bots })} />
            <label className={styles.switch}>
              <input type="checkbox" checked={settings.hide_insufficient}
                onChange={(event) => onChange({ ...settings, hide_insufficient: event.target.checked })} />
              <i />
              Drop repos with too little data
            </label>
            <button type="button" className={styles.reset} onClick={onReset}>Back to default values</button>
          </div>
        </div>
      </div>
    </section>
  );
}
