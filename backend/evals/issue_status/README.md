# Issue status evaluation

Measures how well the issue rules tell a free issue from one that should stay out of the list.

## The data

Each file is a snapshot of real recent unassigned issues, taken with `fetch.py` on 2026-10-30. Logins are left out and long texts are cut. Every issue carries a verdict given by hand from its title, text and last comments:

| Verdict | Meaning |
|---|---|
| `FREE` | A task someone could start today, nobody shows signs of working on it |
| `TAKEN` | An open pull request closes it, or a comment shows someone is on it |
| `NOT_A_TASK` | A question, a survey, a discussion, an internal plan or something already answered |

The verdicts were written by an AI assistant and are a judgement call, some issues could go either way.

## The two sets

`dev.json` holds 98 issues from five repos and is what the rules were written against. `holdout.json` holds 51 issues from five other repos. It was labelled before the rules were written and is only there to check them, a rule must never be tuned on it.

## Results

Run `uv run python evals/issue_status/evaluate.py evals/issue_status/dev.json` from the backend folder, add `--errors` to list every miss. Precision is the share of shown issues that are really free, recall is the share of free issues that are shown.

| Set | Precision before | Precision now | Recall now |
|---|---|---|---|
| `dev.json` | 47% | 65% | 100% |
| `holdout.json` | 65% | 70% | 100% |

Before means the old list, which only left out issues with a linked pull request. Most of what is still shown by mistake is not a task, like a discussion written as a plain sentence. Rules cannot tell those apart, which is the gap a language model is meant to fill.
