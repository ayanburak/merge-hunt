/**
 * file: src/components/TopBar.tsx
 * importable: TopBar
 * description: The name of the app and the way between the two pages.
 */

import { Link, useMatch } from "react-router";

import { useScans } from "../hooks/useScans";
import styles from "./TopBar.module.css";

// Results always belongs to a scan, so the link goes to the newest one that has any. Before
// the first such scan there is nowhere to go and it stays plain text.
export function TopBar() {
  const scans = useScans();
  const onScans = useMatch("/") !== null;
  const onResults = useMatch("/scans/:id/results") !== null;
  const latest = scans.data?.find((scan) => scan.done > 0);

  return (
    <header className={styles.bar}>
      <Link className={styles.mark} to="/" aria-label="merge-hunt">
        <svg width="22" height="18" viewBox="0 0 22 18" aria-hidden="true">
          <path d="M1 1.5c6 0 7 7.5 11 7.5M1 16.5c6 0 7-7.5 11-7.5h9" fill="none" stroke="currentColor" strokeWidth="2.4" />
        </svg>
        merge-hunt
      </Link>
      <nav className={styles.nav}>
        <Link to="/" aria-current={onScans ? "page" : undefined}>Scans</Link>
        {latest ? (
          <Link to={`/scans/${latest.id}/results`} aria-current={onResults ? "page" : undefined}>Results</Link>
        ) : (
          <span>Results</span>
        )}
      </nav>
    </header>
  );
}
