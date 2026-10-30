/**
 * file: src/components/ScanFailures.tsx
 * importable: ScanFailures
 * description: The repos a scan could not fetch completely and why, folded unless told otherwise.
 */

import type { ScanDetail } from "../api/client";
import styles from "./ScanFailures.module.css";

type Props = { failures: ScanDetail["failures"]; open?: boolean };

// Native details keeps the folding out of React. The results page keeps it closed, the repos
// there matter more, a scan that has just ended shows it open.
export function ScanFailures({ failures, open = false }: Props) {
  if (failures.length === 0) return null;

  return (
    <details className={styles.failures} open={open}>
      <summary>{failures.length} {failures.length === 1 ? "repo" : "repos"} could not be fetched completely</summary>
      <ul>
        {failures.map((failure) => (
          <li key={failure.repo}><b>{failure.repo}</b>{failure.error}</li>
        ))}
      </ul>
    </details>
  );
}
