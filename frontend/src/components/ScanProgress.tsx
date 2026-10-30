/**
 * file: src/components/ScanProgress.tsx
 * importable: ScanProgress
 * description: Shows how a scan is going, or how it ended, with the way to its results.
 */

// candidates and skipped stay zero until discovery ends, so the size of the scan is only known
// from then on. Before that the bar just moves and the tally shows dashes.

import { Link } from "react-router";

import type { ScanDetail } from "../api/client";
import { clock, duration, number } from "../format";
import { ScanFailures } from "./ScanFailures";
import styles from "./ScanProgress.module.css";

type Props = {
  scan: ScanDetail;
  unreachable: boolean;
  onNew: () => void;
};

type Discovery = { language: string | null; min_stars: number; max_stars: number };

const STAGES: ScanDetail["stage"][] = ["DISCOVERY", "ENRICHMENT", "ISSUES"];

// The steps follow the scan, a stopped scan marks the step it stopped on.
function Steps({ scan }: { scan: ScanDetail }) {
  const stopped = scan.status === "FAILED" || scan.status === "INTERRUPTED";
  const current = STAGES.indexOf(scan.stage);
  const state = (index: number) => {
    if (scan.status === "DONE" || index < current) return "done";
    if (index > current) return undefined;
    return stopped ? "stopped" : "now";
  };

  return (
    <ol className={styles.steps}>
      {["Find repos", "Fetch their history", "Find issues", "Ready"].map((name, index) => (
        <li key={name} data-state={state(index)}>
          <i>{state(index) === "done" ? "✓" : index + 1}</i>
          {name}
        </li>
      ))}
    </ol>
  );
}

// The pace comes from this scan itself, the time since it started over the repos finished. Time
// spent in discovery is in there too, so early on it runs a little high and then settles.
function timeLeft(scan: ScanDetail, finished: number, left: number): string {
  if (finished === 0) return "You can leave this page, the scan goes on.";
  const pace = (Date.now() - Date.parse(scan.started_at)) / 1000 / finished;
  return `About ${duration(left * pace)} left. You can leave this page, the scan goes on.`;
}

function Headline({ scan, known, finished, kept }: { scan: ScanDetail; known: boolean; finished: number; kept: number }) {
  if (scan.status === "RUNNING" && !known) {
    return (
      <>
        <p className={styles.headline}>Searching GitHub</p>
        <p className={styles.detail}>Finding repos and counting their pull requests.</p>
      </>
    );
  }
  if (scan.status === "RUNNING" && scan.stage === "ISSUES" && !scan.waiting_until) {
    return (
      <>
        <p className={styles.headline}>Finding issues</p>
        <p className={styles.detail}>Reading the open issues of the repos it fetched. This is the short part.</p>
      </>
    );
  }
  if (scan.status === "RUNNING") {
    return (
      <>
        <div className={styles.count}>{finished}<span>of {kept}</span></div>
        {scan.waiting_until ? (
          <p className={styles.detail}>
            <b>Paused by GitHub’s limit.</b> It continues by itself at <b>{clock(scan.waiting_until)}</b>. Nothing is lost while it waits.
          </p>
        ) : (
          <p className={styles.detail}>{timeLeft(scan, finished, Math.max(0, kept - finished))}</p>
        )}
      </>
    );
  }
  if (scan.status === "DONE") {
    return <div className={styles.count}>{scan.done}<span>repos ready</span></div>;
  }
  if (scan.status === "FAILED") {
    return (
      <>
        <p className={styles.headline}>The scan could not finish</p>
        <p className={styles.detail}><b>{scan.error}</b></p>
      </>
    );
  }
  return (
    <>
      <p className={styles.headline}>The app closed during this scan</p>
      <p className={styles.detail}>Starting again is cheap: repos fetched in the last 7 days are not fetched twice.</p>
    </>
  );
}

export function ScanProgress({ scan, unreachable, onNew }: Props) {
  const running = scan.status === "RUNNING";
  const known = scan.stage !== "DISCOVERY" || !running;
  const kept = scan.candidates - scan.skipped;
  const finished = scan.done + scan.failed;
  const left = Math.max(0, kept - finished);
  const share = (part: number) => `${kept > 0 ? (part / kept) * 100 : 0}%`;
  const discovery = scan.options.discovery as Discovery;
  const results = `/scans/${scan.id}/results`;

  const row = (label: string, value: number) => (
    <div>
      <span>{label}</span>
      <b>{known ? number(value) : "–"}</b>
    </div>
  );

  return (
    <section className={styles.watch} aria-live="polite">
      <Steps scan={scan} />
      <p className={styles.recap}>
        Scan {scan.id}: {discovery.language ?? "any language"}, {number(discovery.min_stars)} to {number(discovery.max_stars)} stars.
      </p>

      <div className={styles.body}>
        <div>
          <Headline scan={scan} known={known} finished={finished} kept={kept} />
          {unreachable && <p className={styles.detail}><b>The backend is not reachable.</b> The numbers above may be old.</p>}
          {!running && <ScanFailures failures={scan.failures} open />}
          <div className={styles.actions}>
            {scan.done > 0 && (
              <Link className={styles.link} to={results}>
                {scan.status === "DONE" ? "See results" : running ? `See the ${scan.done} so far` : `See the ${scan.done} it finished`}
              </Link>
            )}
            {!running && <button type="button" className={styles.text} onClick={onNew}>New scan</button>}
          </div>
        </div>
        <div className={styles.tally}>
          {row("Found on GitHub", scan.candidates)}
          {row("Skipped by PR count", scan.skipped)}
          {row("Fetched", scan.done)}
          {row("Failed", scan.failed)}
        </div>
      </div>

      {known && (
        <div className={styles.legend}>
          <span><i />{scan.done} fetched</span>
          {scan.failed > 0 && <span data-kind="failed"><i />{scan.failed} failed</span>}
          {running && left > 0 && <span data-kind="left"><i />{left} to go</span>}
        </div>
      )}
      <div className={styles.progress} data-seeking={known ? undefined : ""}>
        <i style={known ? { width: share(scan.done) } : undefined} />
        <i style={{ width: known ? share(scan.failed) : 0 }} />
      </div>
    </section>
  );
}
