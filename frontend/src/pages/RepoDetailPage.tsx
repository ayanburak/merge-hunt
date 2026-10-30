/**
 * file: src/pages/RepoDetailPage.tsx
 * importable: RepoDetailPage
 * description: Shows one repo with its issues, the filters over them and the way to fetch them again.
 */

// No metrics here, they depend on a profile and this page belongs to no profile. The filters
// stay on the page for the same reason. Every change asks the backend again, the rules of what
// is hidden live only there.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError, getRepo, giveFeedback, refreshIssues, repoIssues, takeBackFeedback } from "../api/client";
import type { FeedbackValue, IssueFilter } from "../api/client";
import controls from "../components/Controls.module.css";
import { IssueCard } from "../components/IssueCard";
import { Segments } from "../components/Segments";
import { TopBar } from "../components/TopBar";
import { ago, number } from "../format";
import styles from "./RepoDetailPage.module.css";

const AGES: [number | null, string][] = [[null, "Any age"], [365, "1 year"], [730, "2 years"]];

const START: IssueFilter = { hide_assigned: true, hide_linked: true, max_age_days: null, show_skipped: false };

function RepoView({ id }: { id: number }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState(START);
  const set = (changes: Partial<IssueFilter>) => setFilter((current) => ({ ...current, ...changes }));

  const repo = useQuery({ queryKey: ["repo", id], queryFn: () => getRepo(id) });
  const issues = useQuery({ queryKey: ["issues", id, filter], queryFn: () => repoIssues(id, filter), placeholderData: keepPreviousData });

  const reload = () => client.invalidateQueries({ queryKey: ["issues", id] });
  const refresh = useMutation({ mutationFn: () => refreshIssues(id), onSuccess: reload });
  const feedback = useMutation({
    mutationFn: ({ issue, value }: { issue: number; value: FeedbackValue | null }) =>
      value === null ? takeBackFeedback(id, issue) : giveFeedback(id, issue, value),
    onSuccess: reload,
  });

  const toggle = (key: "hide_assigned" | "hide_linked" | "show_skipped", label: string) => (
    <button type="button" className={controls.choice} aria-pressed={filter[key]} onClick={() => set({ [key]: !filter[key] })}>{label}</button>
  );

  const problem = repo.error ?? issues.error;
  if (problem instanceof ApiError && problem.status === 404) {
    return (
      <div className={styles.blank}>
        <h2>This repo is not known here</h2>
        <p>Pick one from the <Link to="/">results of a scan</Link>.</p>
      </div>
    );
  }
  if (problem && !repo.data) {
    return <div className={styles.blank}><h2>This repo could not be loaded</h2><p>{problem.message}</p></div>;
  }
  if (!repo.data) return null;

  const { owner, name, stars, language, license } = repo.data;
  const answer = issues.data;
  const never = answer !== undefined && answer.fetched_at === null;
  const github = `https://github.com/${owner}/${name}`;
  const error = refresh.error ?? feedback.error ?? issues.error;

  return (
    <>
      <section className={styles.summary}>
        <button type="button" className={styles.back} onClick={() => navigate(-1)}>← Back</button>
        <h1 className={styles.title}><span>{owner}/</span>{name}</h1>
        <div className={styles.facts}>
          <span>{number(stars)} stars</span>
          <span>{language ?? "No language"}</span>
          <span>{license === "NOASSERTION" ? "Other license" : license ?? "No license"}</span>
          <a href={github} target="_blank" rel="noreferrer">Open on GitHub</a>
        </div>
        <div className={styles.fetch}>
          <button type="button" className={controls.choice} disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? "Fetching" : "Refresh issues"}
          </button>
          {answer?.fetched_at && <span>Fetched {ago(answer.fetched_at)} with {answer.labels.join(", ")}</span>}
        </div>
        {error && <p className={styles.notice}>{error.message}</p>}
      </section>

      {!never && (
        <div className={styles.filters}>
          <b className={styles.count}>{answer?.issues.length ?? 0}</b>
          <span className={styles.word}>{answer?.issues.length === 1 ? "issue" : "issues"}</span>
          {toggle("hide_assigned", "Hide assigned")}
          {toggle("hide_linked", "Hide linked")}
          {toggle("show_skipped", "Show skipped")}
          <div className={styles.age}>
            <Segments items={AGES} value={filter.max_age_days} onChange={(max_age_days) => set({ max_age_days })} />
          </div>
        </div>
      )}

      {never && (
        <div className={styles.blank}>
          <h2>Issues have not been fetched yet</h2>
          <p>This repo was scanned before issues were part of a scan. Refresh issues fetches them now.</p>
        </div>
      )}
      {answer && !never && answer.issues.length === 0 && (
        <div className={styles.blank}>
          <h2>No issues to show</h2>
          <p>Either none carry the labels this repo was fetched with, or the filters above hide them.</p>
        </div>
      )}
      {answer?.issues.map((item) => (
        <IssueCard
          key={item.issue.number}
          item={item}
          url={`${github}/issues/${item.issue.number}`}
          busy={feedback.isPending}
          onFeedback={(value) => feedback.mutate({ issue: item.issue.number, value })}
        />
      ))}
    </>
  );
}

export function RepoDetailPage() {
  const id = Number(useParams().id);

  return (
    <div className={styles.page}>
      <title>merge-hunt · Repo</title>
      <TopBar />
      <RepoView key={id} id={id} />
    </div>
  );
}
