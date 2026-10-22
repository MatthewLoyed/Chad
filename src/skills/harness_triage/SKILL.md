---
name: harness_triage
description: "Turn eval failures into well-structured GitHub issues without duplicates. Use when the user says 'triage the last eval run', 'file issues for the eval failures', or after running the harness and wanting failures tracked. Reads the run's triage output, dedupes against open issues, and files one clean issue per real failure — after you approve."
version: 1.0.0
license: MIT
tags: [evals, triage, github, devops, harness]
requires: github
---

# Harness Triage → GitHub Issues

Turn a harness run's failures into **deduped, well-structured GitHub issues** so nothing is
lost and nothing is filed twice. The heavy lifting — clustering failures by root cause — is
already done by `evals/triage.py`; this skill is the SOP that reads its output, checks for
duplicates, and files issues **only after you approve**.

## Safety: filing issues is an outward action
Creating GitHub issues posts public content to the repo. **Never file automatically.**
Always present the planned issues (post-dedupe) and get an explicit "yes" before creating
them. "Triage the last run" authorizes the *analysis*, not unattended issue creation.

## Inputs — where the run's data lives
After a run (`python -m evals.runner --suite <suite>`), the harness writes to `evals/runs/`:
- **`triage.md`** — failures grouped into root-cause clusters, each naming the likely area +
  files. **This is your primary input.**
- **`latest.md`** — the human report for the latest run (per-case pass/fail + reasons).
- **`transcripts/run_<ts>/<case_id>.md`** — the full trajectory for a single case.
- **`scoreboard.md`** — cross-run aggregate ("what fails MOST"), from `python -m evals.report`.
  Use it to prioritize chronic failures over one-off flakes.

If these don't exist yet, run the suite first (see repro below).

## Standard Operating Procedure

### 1. Read the triage output
Open `evals/runs/triage.md`. Work the clusters **top-down** (biggest/root-cause first). For
each cluster note: label, likely area, the named files, and the member case IDs.

### 2. Decide real bug vs. dataset/threshold (per cluster)
`triage.md`'s "Next step" already nudges this — honor it:
- **Real harness bug** — deterministic `tools_*` / `must_complete` failures, infra errors,
  wrong wiring. → File an issue.
- **Dataset/threshold tune** — an all-judge cluster with sound trajectories, an over-strict
  assertion. → File as a *tuning* issue (or skip if trivial), labeled as such — don't dress a
  threshold nit up as a code bug.
- **Environment artifact** — e.g. the `prompt_fallback` cluster (offline mock prompts). → Do
  **not** file; note that Supabase must be reachable and the run re-done.

### 3. Dedupe against open issues (case_id is the primary key)
For each candidate, query GitHub (via the `github` skill) for open issues matching the
**case ID** or the title fingerprint. Use a consistent title prefix:

```
evals: <case_id> — <short reason>
```

If an open issue already covers it, **skip** (optionally add a comment that it recurred in
this run). Only file when there's no duplicate.

### 4. Draft the issues (don't create yet)
For each non-duplicate, draft the body from this template:

```markdown
## Eval failure
- **Suite:** <suite>
- **Case ID:** <case_id>
- **Metric:** <assertion/judge that failed — expected vs got>
- **Cluster:** <root-cause label from triage.md>

## Repro
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.runner --suite <suite>
# then read evals/runs/latest.md and transcripts/run_<ts>/<case_id>.md
# (the runner has no per-case flag; filter to <case_id> in the report/transcript)

## Suggested fix
- <the file(s) triage.md named for this cluster>

## Triage
- [ ] Harness bug
- [ ] Dataset threshold tune
```

> Repro accuracy: run at the **suite** level — there is no `--case` flag. Point the reader to
> the specific case's transcript for the trajectory.

### 5. Present for approval, then file
Show the deduped list: "N new issues to file, M skipped as duplicates." On the user's yes,
create them via the `github` skill. Report back the created issue numbers/links.

## Local-model notes
- **Work from `triage.md`, don't re-derive clusters.** The clustering is deterministic and
  already done — reading it is far cheaper and more reliable than re-analyzing raw results.
- One pass over clusters; don't loop re-reading the same transcript.

## Common pitfalls
- ❌ Filing issues without approval, or auto-filing duplicates.
- ❌ Inventing a `--case` runner flag in the repro (suite-level only).
- ❌ Filing `prompt_fallback` / offline-mock clusters as real prompt bugs.
- ❌ One giant issue for a whole cluster instead of one per distinct case/root cause
  (unless the cluster genuinely is a single fix — then say so).
- ❌ Re-deriving root cause by hand when `triage.md` already names the files.
