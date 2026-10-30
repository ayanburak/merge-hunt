/**
 * file: src/components/DualSlider.tsx
 * importable: DualSlider
 * description: A range with two handles and a boxed number for each end.
 */

import type { CSSProperties } from "react";

import { NumberBox } from "./NumberBox";
import styles from "./DualSlider.module.css";

type Props = {
  name: string;
  low: number;
  high: number;
  top: number;
  curve: number;
  onChange: (low: number, high: number) => void;
};

const STEPS = 1000;

// The scale is curved, most repos sit at the low end of these ranges and a straight scale would
// squeeze them into the first few pixels.
function toPosition(value: number, top: number, curve: number): number {
  return Math.round((Math.min(value, top) / top) ** (1 / curve) * STEPS);
}

// Rounded to two meaningful digits, a handle stopping at 4,873 stars would only look random.
function toValue(position: number, top: number, curve: number): number {
  const raw = (position / STEPS) ** curve * top;
  const unit = 10 ** Math.max(0, Math.floor(Math.log10(Math.max(raw, 1))) - 1);
  return Math.round(raw / unit) * unit;
}

// A typed value can go beyond top, the handle then rests at the end and the box keeps the number.
// The handles stop at each other, a typed low above high is left for the form to report.
export function DualSlider({ name, low, high, top, curve, onChange }: Props) {
  const a = toPosition(low, top, curve);
  const b = toPosition(high, top, curve);

  return (
    <>
      <div className={styles.pair}>
        <NumberBox value={low} label={`Lowest ${name}`} align="right" onChange={(value) => onChange(value, high)} />
        <span>to</span>
        <NumberBox value={high} label={`Highest ${name}`} align="right" onChange={(value) => onChange(low, value)} />
      </div>
      <div className={styles.slider} style={{ "--a": `${a / 10}%`, "--b": `${b / 10}%` } as CSSProperties}>
        <input
          type="range"
          min={0}
          max={STEPS}
          value={a}
          aria-label={`Lowest ${name}`}
          onChange={(event) => onChange(Math.min(toValue(Number(event.target.value), top, curve), high), high)}
        />
        <input
          type="range"
          min={0}
          max={STEPS}
          value={b}
          aria-label={`Highest ${name}`}
          onChange={(event) => onChange(low, Math.max(toValue(Number(event.target.value), top, curve), low))}
        />
      </div>
    </>
  );
}
