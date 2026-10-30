/**
 * file: src/components/ScanHistory.tsx
 * importable: ScanHistory
 * description: The list of earlier scans, each one leading to its results or to how it ended.
 */

import { Link } from "react-router";

import type { Scan } from "../api/client";
import { clock, day, duration, number } from "../format";
import styles from "./ScanHistory.module.css";

type Discovery = { language: string | null; min_stars: number; max_stars: number };

const LABELS = { RUNNING: "Running", DONE: "Done", FAILED: "Failed", INTERRUPTED: "Interrupted" };

function took(scan: Scan): string {
  if (!scan.finished_at) return "–";
  return duration((Date.parse(scan.finished_at) - Date.parse(scan.started_at)) / 1000);
}

function Cells({ scan }: { scan: Scan }) {
  const discovery = scan.options.discovery as Discovery;
  const calm = scan.status === "DONE" || scan.status === "RUNNING";

  return (
    <>
      <span className={styles.when}>{day(scan.started_at)}<small>{clock(scan.started_at)}</small></span>
      <span className={styles.search}>
        <b>{discovery.language ?? "Any language"}</b>, {number(discovery.min_stars)}–{number(discovery.max_stars)} stars
      </span>
      <span className={styles.num}>{number(scan.candidates)}</span>
      <span className={styles.num}>{number(scan.done)}</span>
      <span className={styles.num} data-zero={scan.failed === 0 ? "" : undefined}>{number(scan.failed)}</span>
      <span className={styles.num}>{took(scan)}</span>
      <span className={styles.status} data-bad={calm ? undefined : ""}>{LABELS[scan.status]}</span>
    </>
  );
}

type Props = {
  scans: Scan[];
  error?: string;
  onOpen?: (id: number) => void;
};

// A scan with nothing fetched has no results to open, its row shows how it ended instead. Without
// onOpen such a row stays plain, the page leaves it out while a scan is running on screen.
export function ScanHistory({ scans, error, onOpen }: Props) {
  return (
    <section className={styles.basin}>
      <h2>Earlier scans</h2>
      {error && <p className={styles.note}>{error}</p>}
      {!error && scans.length === 0 && <p className={styles.note}>No scans yet.</p>}
      {scans.length > 0 && (
        <>
          <div className={`${styles.row} ${styles.head}`}>
            <span>Started</span>
            <span>Search</span>
            <span className={styles.num}>Found</span>
            <span className={styles.num}>Fetched</span>
            <span className={styles.num}>Failed</span>
            <span className={styles.num}>Took</span>
            <span className={styles.status}>Status</span>
          </div>
          {scans.map((scan) => {
            if (scan.done > 0) {
              return <Link key={scan.id} className={styles.row} to={`/scans/${scan.id}/results`}><Cells scan={scan} /></Link>;
            }
            if (onOpen) {
              return <button key={scan.id} type="button" className={styles.row} onClick={() => onOpen(scan.id)}><Cells scan={scan} /></button>;
            }
            return <div key={scan.id} className={styles.row}><Cells scan={scan} /></div>;
          })}
        </>
      )}
    </section>
  );
}
