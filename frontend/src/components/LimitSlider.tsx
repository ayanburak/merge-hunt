/**
 * file: src/components/LimitSlider.tsx
 * importable: LimitSlider
 * description: A slider with one handle, the dark part of its track shows what it lets through.
 */

import type { CSSProperties } from "react";

import styles from "./LimitSlider.module.css";

type Props = {
  position: number;
  max: number;
  step?: number;
  label: string;
  fill: "left" | "right";
  off?: boolean;
  onChange: (position: number) => void;
};

export function LimitSlider({ position, max, step = 1, label, fill, off = false, onChange }: Props) {
  const share = `${(position / max) * 100}%`;
  const track = fill === "right" ? { "--a": share, "--b": "100%" } : { "--a": "0%", "--b": share };

  return (
    <input
      className={styles.range}
      type="range"
      min={0}
      max={max}
      step={step}
      value={position}
      aria-label={label}
      data-off={off ? "" : undefined}
      style={track as CSSProperties}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  );
}
