/**
 * file: src/hooks/useCountTo.ts
 * importable: useCountTo
 * description: A number that runs up or down to its target instead of jumping there.
 */

import { useEffect, useRef, useState } from "react";

const DURATION_MS = 700;

export function useCountTo(target: number): number {
  const [value, setValue] = useState(target);
  const from = useRef(target);

  useEffect(() => {
    const start = from.current;
    const began = performance.now();
    let frame = 0;

    const step = (time: number) => {
      const part = Math.min(1, (time - began) / DURATION_MS);
      const next = Math.round(start + (target - start) * part * (2 - part));
      from.current = next;
      setValue(next);
      if (part < 1) frame = requestAnimationFrame(step);
    };

    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  return value;
}
