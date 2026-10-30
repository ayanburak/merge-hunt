/**
 * file: src/components/NumberBox.tsx
 * importable: NumberBox
 * description: A boxed field for a whole number, written with commas once it loses focus.
 */

import { useLayoutEffect, useRef, useState } from "react";

import { number } from "../format";
import controls from "./Controls.module.css";

type Props = {
  value: number;
  onChange: (value: number) => void;
  id?: string;
  label?: string;
  align?: "left" | "right";
  className?: string;
};

// While the box has focus it shows what is being typed, otherwise reformatting would move the
// cursor under the user's fingers. An empty box counts as zero. Taking focus drops the commas,
// which also drops any selection, so selecting has to wait until that is on the screen. If it
// did not, typing into a box holding 5,000 would add to the number and not replace it.
export function NumberBox({ value, onChange, id, label, align = "left", className = controls.box }: Props) {
  const [typing, setTyping] = useState<string | null>(null);
  const box = useRef<HTMLInputElement>(null);
  const editing = typing !== null;

  useLayoutEffect(() => {
    if (editing) box.current?.select();
  }, [editing]);

  return (
    <input
      ref={box}
      className={className}
      id={id}
      aria-label={label}
      inputMode="numeric"
      style={{ textAlign: align }}
      value={typing ?? number(value)}
      onFocus={() => setTyping(String(value))}
      onBlur={() => setTyping(null)}
      onChange={(event) => {
        const digits = event.target.value.replace(/\D/g, "");
        setTyping(digits);
        onChange(Number(digits));
      }}
    />
  );
}
