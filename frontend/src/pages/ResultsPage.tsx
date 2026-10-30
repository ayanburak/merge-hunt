/**
 * file: src/pages/ResultsPage.tsx
 * importable: ResultsPage
 * description: Shows the scored repos of one scan and the filters and profiles over them.
 */

// settings is what the screen shows and changes at once, profile is the saved state it is
// compared with to show Changed. Both start only when the default values and the saved profiles
// are there, so ResultsView can take them as plain props and never deals with loading.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import {
  ApiError,
  createProfile,
  defaultProfile,
  deleteProfile,
  listProfiles,
  updateProfile,
} from "../api/client";
import type { ProfileAnswer, ProfileSettings } from "../api/client";
import { FilterPanel } from "../components/FilterPanel";
import { ProfileMenu } from "../components/ProfileMenu";
import { RepoSort, RepoTable } from "../components/RepoTable";
import type { SortKey } from "../components/RepoTable";
import { ScoringPanel } from "../components/ScoringPanel";
import { TopBar } from "../components/TopBar";
import { clock, day, number } from "../format";
import { useCountTo } from "../hooks/useCountTo";
import { useResults } from "../hooks/useResults";
import { useScanStatus } from "../hooks/useScanStatus";
import styles from "./ResultsPage.module.css";

const PROFILE_KEY = ["profiles"];
const REMEMBERED = "merge-hunt.profile";

// On arrival every repo sits on the sheet, then the failing ones fall through one after another.
const SIEVE_MS = 650;
const STAGGER_MS = 1600;

type Saved = { id: number | null; name: string; settings: ProfileSettings };
type Discovery = { language: string | null; min_stars: number; max_stars: number };

function saved(profile: ProfileAnswer | null, defaults: ProfileSettings): Saved {
  return profile ? { id: profile.id, name: profile.name, settings: profile.settings } : { id: null, name: "Default", settings: defaults };
}

function remember(id: number | null) {
  if (id === null) localStorage.removeItem(REMEMBERED);
  else localStorage.setItem(REMEMBERED, String(id));
}

function Blank({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.blank}>
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}

function Unavailable({ error }: { error: Error }) {
  if (error instanceof ApiError && error.status === 0) {
    return <Blank title="The backend is not answering">Start it with uv run uvicorn merge_hunt.main:app in the backend folder, then reload this page.</Blank>;
  }
  if (error instanceof ApiError && error.status === 404) {
    return <Blank title="This scan does not exist">Pick one from the <Link to="/">list of scans</Link>.</Blank>;
  }
  return <Blank title="The results could not be loaded">{error.message}</Blank>;
}

function ResultsView({ id, defaults, profiles }: { id: number; defaults: ProfileSettings; profiles: ProfileAnswer[] }) {
  const client = useQueryClient();
  const [profile, setProfile] = useState(() => {
    const stored = Number(localStorage.getItem(REMEMBERED));
    return saved(profiles.find((item) => item.id === stored) ?? null, defaults);
  });
  const [settings, setSettings] = useState(profile.settings);
  const [sort, setSort] = useState<SortKey>("score");
  const [tuning, setTuning] = useState(false);
  const [sieved, setSieved] = useState(false);
  const [staggered, setStaggered] = useState(true);

  const { repos, applied, updating, error } = useResults(id, settings);
  const scan = useScanStatus(id).data;
  const ready = repos !== undefined;

  // A running scan keeps adding repos, each one it finishes asks for fresh results. The first
  // sight of a scan changes nothing, the page has already asked for its results by then. The ref
  // has to outlive the effect, StrictMode runs an effect twice on arrival.
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!scan) return;
    const state = `${scan.done} ${scan.status}`;
    if (seen.current !== null && seen.current !== state) client.invalidateQueries({ queryKey: ["results", id] });
    seen.current = state;
  }, [client, id, scan?.done, scan?.status]);

  useEffect(() => {
    if (!ready) return;
    const sieve = setTimeout(() => setSieved(true), SIEVE_MS);
    const settle = setTimeout(() => setStaggered(false), SIEVE_MS + STAGGER_MS);
    return () => {
      clearTimeout(sieve);
      clearTimeout(settle);
    };
  }, [ready]);

  const load = (next: Saved) => {
    setProfile(next);
    setSettings(next.settings);
    remember(next.id);
  };

  const keep = (answer: ProfileAnswer) => {
    load(saved(answer, defaults));
    client.invalidateQueries({ queryKey: PROFILE_KEY });
  };

  const save = useMutation({ mutationFn: () => updateProfile(profile.id!, { name: profile.name, settings }), onSuccess: keep });
  const saveAs = useMutation({ mutationFn: (name: string) => createProfile({ name, settings }), onSuccess: keep });
  const remove = useMutation({
    mutationFn: deleteProfile,
    onSuccess: (_, removed) => {
      if (removed === profile.id) load(saved(null, defaults));
      client.invalidateQueries({ queryKey: PROFILE_KEY });
    },
  });

  const passing = repos?.filter((item) => item.failed_filters.length === 0).length ?? 0;
  const shownCount = useCountTo(sieved ? passing : 0);
  const changed = JSON.stringify(settings) !== JSON.stringify(profile.settings);

  if (error && !repos) return <Unavailable error={error} />;

  const discovery = scan?.options.discovery as Discovery | undefined;
  const expected = scan ? scan.candidates - scan.skipped : 0;
  const notice = error?.message ?? (scan?.status === "RUNNING" && repos ? `Scan still running, ${repos.length} of ${expected} repos so far.` : "");

  return (
    <>
      <div className={styles.upper}>
        <section className={styles.summary}>
          <div className={styles.tally}>
            <div className={styles.passed} aria-live="polite">{shownCount}</div>
            <div className={styles.text}>
              <b>of {repos?.length ?? 0} repos pass{updating && <span className={styles.updating}>Updating</span>}</b>
              {scan && discovery && (
                <span>
                  {discovery.language ?? "Any language"}, {number(discovery.min_stars)}–{number(discovery.max_stars)} stars,
                  {" "}scanned {day(scan.started_at)} at {clock(scan.started_at)}. <Link to="/">Other scans</Link>
                </span>
              )}
              {notice && <p className={styles.notice}>{notice}</p>}
            </div>
          </div>
          <div className={styles.controls}>
            {changed && (
              <div className={styles.changed}>
                <span>Changed</span>
                {profile.id !== null && <button type="button" onClick={() => save.mutate()}>Save</button>}
                <button type="button" onClick={() => setSettings(profile.settings)}>Undo</button>
              </div>
            )}
            <ProfileMenu
              profiles={profiles}
              currentId={profile.id}
              currentName={profile.name}
              onLoad={(next) => load(saved(next, defaults))}
              onDelete={(removed) => remove.mutate(removed)}
              onSaveAs={saveAs.mutateAsync}
            />
            <button type="button" className={styles.tune} aria-expanded={tuning} onClick={() => setTuning(!tuning)}>Scoring settings</button>
          </div>
        </section>
        <ScoringPanel open={tuning} settings={settings} onChange={setSettings} onReset={() => setSettings(defaults)} />
      </div>

      {repos && repos.length === 0 && (
        <Blank title="This scan has no finished repos yet">
          Repos appear here as soon as they are fetched. You can <Link to="/">follow the scan</Link> in the meantime.
        </Blank>
      )}
      {repos && applied && repos.length > 0 && (
        <div className={styles.scroller}>
          <div className={styles.head}>
            <FilterPanel settings={settings} repos={repos} onChange={setSettings} />
            <RepoSort sort={sort} onChange={setSort} />
          </div>
          <RepoTable repos={repos} settings={applied} sort={sort} sieved={sieved} staggered={staggered} />
          <div className={styles.end} />
        </div>
      )}
    </>
  );
}

export function ResultsPage() {
  const id = Number(useParams().id);
  const defaults = useQuery({ queryKey: ["profile-default"], queryFn: defaultProfile, staleTime: Infinity });
  const profiles = useQuery({ queryKey: PROFILE_KEY, queryFn: listProfiles });
  const problem = defaults.error ?? profiles.error;

  return (
    <>
      <title>merge-hunt · Results</title>
      <div className={styles.bar}><TopBar /></div>
      {problem && <Unavailable error={problem} />}
      {defaults.data && profiles.data && <ResultsView key={id} id={id} defaults={defaults.data} profiles={profiles.data} />}
    </>
  );
}
