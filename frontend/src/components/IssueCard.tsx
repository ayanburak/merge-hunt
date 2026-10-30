/**
 * file: src/components/IssueCard.tsx
 * importable: IssueCard
 * description: One issue with its facts, its feedback buttons and, when opened, its text and last comments.
 */

// The claim warning comes from the backend with its date. A claim from years ago usually means
// the issue is free again and the date lets the reader judge that. A hidden issue shows why it
// was hidden, the rule can be wrong and the reader decides.

import { useState } from "react";

import type { AnalyzedIssue, FeedbackValue, HideReason } from "../api/client";
import { ago, day } from "../format";
import styles from "./IssueCard.module.css";

const REASONS: Record<HideReason, string> = {
  CLAIMED: "Hidden: someone is on it",
  SOLVED: "Hidden: a comment says it is solved",
  NOT_READY: "Hidden: its label says wait",
  NOT_A_TASK: "Hidden: looks like a question or a plan",
};

type Props = {
  item: AnalyzedIssue;
  url: string;
  busy: boolean;
  onFeedback: (value: FeedbackValue | null) => void;
};

export function IssueCard({ item, url, busy, onFeedback }: Props) {
  const [open, setOpen] = useState(false);
  const { issue, analysis, feedback } = item;

  // Pressing the button that is already on takes the feedback back.
  const button = (value: FeedbackValue, label: string) => (
    <button type="button" className={styles.feedback} aria-pressed={feedback === value} disabled={busy} onClick={() => onFeedback(feedback === value ? null : value)}>
      {label}
    </button>
  );

  return (
    <article className={styles.card} data-skipped={feedback === "SKIPPED" ? "" : undefined} data-interested={feedback === "INTERESTED" ? "" : undefined}>
      <div className={styles.top}>
        <div className={styles.head}>
          <a className={styles.title} href={url} target="_blank" rel="noreferrer">{issue.title}</a>
          <div className={styles.facts}>
            <span>#{issue.number}</span>
            <span>opened {ago(issue.created_at)}</span>
            <span>updated {ago(issue.updated_at)}</span>
            <span>{issue.comment_count === 1 ? "1 comment" : `${issue.comment_count} comments`}</span>
            {issue.assigned && <span className={styles.flag}>Assigned</span>}
            {issue.linked_prs > 0 && <span className={styles.flag}>Linked pull request</span>}
            {analysis.hide && <span className={styles.flag}>{REASONS[analysis.hide]}</span>}
            {analysis.claimed_at && <span className={styles.flag} data-warn="">May be claimed, {day(analysis.claimed_at)}</span>}
          </div>
          <div className={styles.labels}>
            {issue.labels.map((label) => <span key={label}>{label}</span>)}
          </div>
        </div>
        <div className={styles.actions}>
          {button("INTERESTED", "Interested")}
          {button("SKIPPED", "Skip")}
          <button type="button" className={styles.toggle} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Close" : "Read"}</button>
        </div>
      </div>
      {open && (
        <div className={styles.more}>
          <p className={styles.body}>{issue.body || "No description."}</p>
          {issue.comments.length > 0 && (
            <>
              <h4>{issue.comment_count > issue.comments.length ? `Last ${issue.comments.length} of ${issue.comment_count} comments` : "Comments"}</h4>
              {issue.comments.map((comment, index) => (
                <div key={index} className={styles.comment}>
                  <div className={styles.who}><b>{comment.author.login}</b> {ago(comment.at)}</div>
                  <p className={styles.body}>{comment.body}</p>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </article>
  );
}
