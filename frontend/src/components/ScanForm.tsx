/**
 * file: src/components/ScanForm.tsx
 * importable: ScanForm, problems, estimate
 * description: The new scan panel, every field of ScanRequest can be changed here.
 */

// The estimate only speaks when a finished scan exists to go by, that scan is measured and a
// number written here would go stale. It scales the last scan by the number of repos, the real
// time also depends on how many are skipped, so the wording stays rough on purpose.

import { useState } from "react";

import type { Scan, ScanRequest } from "../api/client";
import { duration } from "../format";
import { DualSlider } from "./DualSlider";
import { NumberBox } from "./NumberBox";
import controls from "./Controls.module.css";
import { Segments } from "./Segments";
import styles from "./ScanForm.module.css";

type Options = Required<ScanRequest>;

type Props = {
  defaults: ScanRequest;
  lastDone?: Scan;
  error?: string;
  busy: boolean;
  onStart: (options: ScanRequest) => void;
};

const LANGUAGES = ["Python", "TypeScript", "JavaScript", "HTML", "Go", "Rust", "Java", "C++", "C#", "PHP", "Ruby"];

const LICENSES = [
  ["mit", "MIT"],
  ["apache-2.0", "Apache 2.0"],
  ["gpl-3.0", "GPL 3.0"],
  ["agpl-3.0", "AGPL 3.0"],
  ["lgpl-3.0", "LGPL 3.0"],
  ["bsd-3-clause", "BSD 3-Clause"],
  ["bsd-2-clause", "BSD 2-Clause"],
  ["mpl-2.0", "MPL 2.0"],
  ["isc", "ISC"],
];

const PUSHED: [number, string][] = [[30, "30 d"], [90, "90 d"], [180, "180 d"], [365, "1 year"]];
const CANDIDATES: [number, string][] = [[25, "25"], [50, "50"], [100, "100"], [250, "250"], [500, "500"], [1000, "1,000"]];
const SORTS: [Options["sort"], string][] = [["stars-desc", "Most stars"], ["stars-asc", "Fewest stars"], ["updated-desc", "Recently updated"]];

// A label is added with Enter, GitHub matches the name exactly so it stays as typed.
function IssueLabels({ labels, onChange }: { labels: string[]; onChange: (labels: string[]) => void }) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const label = draft.trim();
    if (label && !labels.includes(label)) onChange([...labels, label]);
    setDraft("");
  };

  return (
    <>
      <input className={controls.box} id="issue-labels" placeholder="Add a label, then press Enter" autoComplete="off" value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          add();
        }} />
      <ul className={styles.labels}>
        {labels.map((label) => (
          <li key={label}>
            {label}
            <button type="button" aria-label={`Remove ${label}`} onClick={() => onChange(labels.filter((item) => item !== label))}>×</button>
          </li>
        ))}
      </ul>
    </>
  );
}

// These are the same rules the backend checks, here they only turn the button off before the
// user has to send anything. The backend stays the one that decides.
export function problems(options: Options): string[] {
  const found = [];
  if (options.min_stars > options.max_stars) found.push("The lowest star count is above the highest.");
  if (options.min_prs > options.max_prs) found.push("The lowest pull request count is above the highest.");
  if (options.window_days < 1 || options.commit_days < 1) found.push("A period has to be at least 1 day.");
  if (options.issue_labels.length === 0) found.push("Add at least one issue label.");
  return found;
}

export function estimate(candidates: number, last?: Scan): string {
  if (!last?.finished_at || last.candidates === 0) return "";
  const took = (Date.parse(last.finished_at) - Date.parse(last.started_at)) / 1000;
  if (candidates === last.candidates) return `The last scan of ${candidates} repos took ${duration(took)}.`;
  return `Roughly ${Math.max(1, Math.round((candidates * took) / last.candidates / 60))} min, going by the last scan.`;
}

export function ScanForm({ defaults, lastDone, error, busy, onStart }: Props) {
  const [options, setOptions] = useState(defaults as Options);
  const [other, setOther] = useState("");
  const set = (changes: Partial<Options>) => setOptions((current) => ({ ...current, ...changes }));

  const language = options.language;
  const shown = [...problems(options), ...(error ? [error] : [])];

  return (
    <>
      <h1 className={styles.title}>New scan</h1>
      <p className={styles.intro}>Choose which GitHub repos to look at. Merge rate and response time are filtered later, on Results.</p>

      <div className={styles.block}>
        <div className={styles.group}>Language</div>
        <div className={styles.languages}>
          {[null, ...LANGUAGES].map((name) => (
            <button key={name ?? "Any"} type="button" className={controls.choice} aria-pressed={name === language} onClick={() => {
                setOther("");
                set({ language: name });
              }}>
              {name ?? "Any"}
            </button>
          ))}
          <input
            className={`${controls.box} ${styles.other}`}
            placeholder="Other language"
            aria-label="Other language"
            autoComplete="off"
            data-on={other.trim() && !LANGUAGES.includes(other.trim()) ? "" : undefined}
            value={other}
            onChange={(event) => {
              setOther(event.target.value);
              set({ language: event.target.value.trim() || null });
            }}
          />
        </div>
      </div>

      <div className={styles.columns}>
        <div className={styles.column}>
          <div className={styles.group}>Repo</div>
          <div className={styles.control}>
            <div className={styles.label}><span>Stars</span></div>
            <DualSlider name="stars" low={options.min_stars} high={options.max_stars} top={100000} curve={3}
              onChange={(min_stars, max_stars) => set({ min_stars, max_stars })} />
          </div>
          <div className={styles.control}>
            <label className={styles.label} htmlFor="topic"><span>Topic</span><span className={styles.hint}>Optional</span></label>
            <input className={controls.box} id="topic" placeholder="Any topic, for example cli" autoComplete="off"
              value={options.topic ?? ""} onChange={(event) => set({ topic: event.target.value || null })} />
          </div>
          <div className={styles.control}>
            <label className={styles.label} htmlFor="license"><span>License</span><span className={styles.hint}>Optional</span></label>
            <select className={controls.box} id="license" value={options.license ?? ""} onChange={(event) => set({ license: event.target.value || null })}>
              <option value="">Any license</option>
              {LICENSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        </div>

        <div className={styles.column}>
          <div className={styles.group}>Activity</div>
          <div className={styles.control}>
            <div className={styles.label}><span>Last push within</span></div>
            <Segments items={PUSHED} value={options.pushed_within_days} onChange={(pushed_within_days) => set({ pushed_within_days })} />
          </div>
          <div className={styles.control}>
            <div className={styles.label}><span>Pull requests in the period</span><span className={styles.hint}>Others are skipped</span></div>
            <DualSlider name="pull requests" low={options.min_prs} high={options.max_prs} top={2000} curve={2}
              onChange={(min_prs, max_prs) => set({ min_prs, max_prs })} />
          </div>
          <div className={`${styles.control} ${styles.twin}`}>
            <div>
              <label className={styles.label} htmlFor="good-first">Good first issues, at least</label>
              <NumberBox id="good-first" value={options.min_good_first_issues} onChange={(min_good_first_issues) => set({ min_good_first_issues })} />
            </div>
            <div>
              <label className={styles.label} htmlFor="help-wanted">Help wanted, at least</label>
              <NumberBox id="help-wanted" value={options.min_help_wanted_issues} onChange={(min_help_wanted_issues) => set({ min_help_wanted_issues })} />
            </div>
          </div>
          <div className={styles.control}>
            <label className={styles.label} htmlFor="issue-labels"><span>Issue labels to fetch</span><span className={styles.hint}>Full name, any case</span></label>
            <IssueLabels labels={options.issue_labels} onChange={(issue_labels) => set({ issue_labels })} />
          </div>
        </div>

        <div className={styles.column}>
          <div className={styles.group}>Scan</div>
          <div className={styles.control}>
            <div className={styles.label}><span>How many repos to check</span></div>
            <Segments items={CANDIDATES} value={options.max_candidates} onChange={(max_candidates) => set({ max_candidates })} />
          </div>
          <div className={styles.control}>
            <div className={styles.label}><span>Start from</span></div>
            <Segments items={SORTS} value={options.sort} onChange={(sort) => set({ sort })} />
          </div>
          <div className={`${styles.control} ${styles.twin}`}>
            <div>
              <label className={styles.label} htmlFor="window-days">Pull request period, days</label>
              <NumberBox id="window-days" value={options.window_days} onChange={(window_days) => set({ window_days })} />
            </div>
            <div>
              <label className={styles.label} htmlFor="commit-days">Commit period, days</label>
              <NumberBox id="commit-days" value={options.commit_days} onChange={(commit_days) => set({ commit_days })} />
            </div>
          </div>
        </div>
      </div>

      <div className={styles.foot}>
        {shown.length > 0 && <p className={styles.problem}>{shown.join(" ")}</p>}
        <span className={styles.estimate}>{estimate(options.max_candidates, lastDone)}</span>
        <button type="button" className={styles.start} disabled={busy || problems(options).length > 0} onClick={() => onStart({ ...options, topic: options.topic?.trim() || null })}>
          Start scan
        </button>
      </div>
    </>
  );
}
