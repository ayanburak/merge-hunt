/**
 * file: src/components/Segments.tsx
 * importable: Segments
 * description: Buttons that pick one value out of a few.
 */

import controls from "./Controls.module.css";

export function Segments<T>({ items, value, onChange }: { items: [T, string][]; value: T; onChange: (value: T) => void }) {
  return (
    <div className={controls.segments}>
      {items.map(([item, label]) => (
        <button key={label} type="button" className={controls.choice} aria-pressed={item === value} onClick={() => onChange(item)}>
          {label}
        </button>
      ))}
    </div>
  );
}
