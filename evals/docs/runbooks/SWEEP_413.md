# Issue #413 sweep — runbook

Set up 2026-07-09, not yet run. This is the "run in the morning, loop all day, capture a ton
of data" pass for [issue #413](https://github.com/MatthewLoyed/AIAssistant/issues/413), broadened
per Matthew's ask to cover complex workflows, testing gaps, coding/automation, and the
never-tested new MCPs (Presenton, Excalidraw, Notion, Todoist, GitHub, Google Drive).

## What's new vs. the existing harness

Everything in `evals/REFERENCE.md` / `evals/AGENTS.md` already existed (dry-run/judge/live/workflow lanes, prompt-audit,
tool-contract, replay, judge-calibration). Added for #413:

| File | Lane | Gap it closes |
|---|---|---|
| `evals/datasets/mcp_untested_live.jsonl` | `--live` | Presenton, Excalidraw, Notion, Todoist, GitHub, Google Drive MCPs have **zero** prior eval coverage — first live pass over each. |
| `evals/datasets/research_deep_live.jsonl` | `--live` | Web search/research (Matthew's #1 stated concern) — existing datasets only check "did *a* search tool fire" on easy queries. This stresses ambiguity, multi-hop synthesis, recency, conflicting sources, no-results, follow-up refinement. |
| `evals/datasets/agent_lane_complex_live.jsonl` | `--live` | `planner.enabled: false` (config/config.yaml:214) means every complex multi-step request now falls back to the agent lane, not the plan lane. Confirms multi-tool/multi-MCP chains (research→file, drive→email, coding write→run→debug→fix) still complete in a single agent-lane turn with no planner to sequence them. This is #413's explicit "special focus" item. |
| `evals/datasets/codebase_audit_live.jsonl` | `--live` | "Read a codebase, audit it for a specific issue, report back" — distinct from generic coding or web research. Verifiable claims (real file paths/registrations) so `response_grounded` can catch a fabricated-sounding report. Includes a GitHub-MCP variant (read-only, points at the public repo). |
| `evals/datasets/website_from_data_live.jsonl` | `--live` | "Build a website from my Google Drive / email data" — gather (drive/gmail search+read) → synthesize → `build_html_file`. Read-only on the source side; includes a negative case (nothing found → must not fabricate a page anyway). |
| `evals/datasets/document_generation_live.jsonl` | `--live` | docx/xlsx/pptx (code-execution skills) + a heavier multi-section pdf case + a cross-format docx→pdf chain. Companion to `visual_artifacts_live.jsonl`, which only covers the typst/kroki/marp/animation/Presenton tools — this fills the docx/xlsx/manual-pptx gap. |
| `evals/run_sweep_413.ps1` | orchestrator | Loops free + judged-dry-run + live + workflow lanes (including all 6 new datasets) for N hours, connect-only (never starts/restarts a server), heartbeats after every lane. |
| `evals/check_sweep_heartbeat.ps1` | monitor | One-shot liveness check — run hourly to confirm the loop hasn't silently died. |

## Before running

1. **Confirm the judge model actually loaded in LM Studio** — must be small (never
   `qwen/qwen3.6-35b-a3b`):
   ```powershell
   (irm http://localhost:1234/v1/models).data.id
   ```
   Pass the real id via `-JudgeModel` if it differs from the script default
   (`google/gemma-4-31b-qat`).
2. **Confirm the target Brain is up** — Matthew approved using prod since nothing's published
   yet: `irm http://localhost:8767/health`. The script is **connect-only**: it never starts,
   restarts, or kills the server. If it's down, live/workflow lanes are skipped that pass
   (logged) and retried next pass — the free/judge lanes keep running regardless.
3. Skim `evals/datasets/mcp_untested_live.jsonl`'s header comment — it **will** create real
   content (a GitHub issue, a Notion page, a Todoist task, Presenton decks), all prefixed
   `"Chad Eval Test"` for easy bulk cleanup. Matthew confirmed this is fine ("they can create
   what they want, we can clean up whenever") — just no update/overwrite/delete of anything
   pre-existing (the dataset is written create-only / read-only by design).

## Kick it off

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File evals/run_sweep_413.ps1
# or override: -Hours 10 -JudgeModel "qwen/qwen3.5-9b" -ServerUrl "ws://localhost:8788/ws" -HealthUrl "http://localhost:8788/health"
```

Defaults: 9 hours, judge `google/gemma-4-31b-qat`, targets prod `:8767`.

## Hourly check-in (do this, don't just fire-and-forget)

Matthew explicitly wants the loop checked roughly hourly for a silent crash, not left
unsupervised all day:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File evals/check_sweep_heartbeat.ps1
```

Reads `evals/runs/sweep_heartbeat.json` (updated after every lane). Prints `OK` / `STALE`
(tails the last 25 log lines automatically) / `MISSING`. If `STALE`, check whether the Brain
process itself died (don't restart :8767 — ask Matthew) vs. a Python exception in the loop
(the try/catch in `run_sweep_413.ps1` should have logged and continued past that — a true stall
usually means the server or LM Studio hung, not the script).

## After it's done (or the next morning)

1. `evals/runs/latest.md` — most recent pass's failures (each `Run-Lane` call overwrites this,
   so it reflects the *last lane* that ran that pass, not a full-night rollup).
2. `evals/runs/triage.md` — same caveat, most-recent-lane only.
3. `evals/runs/scoreboard.md` (via `evals/report.py`, regenerated every pass) — trend across all
   `run_<ts>.json` files, which **do** accumulate all night (timestamped, never overwritten) —
   this is the real full-night rollup.
4. `evals/runs/sweep_413.log` — chronological pass/lane log, useful for correlating a failure
   timestamp back to which suite/lane produced it.
5. `evals/runs/transcripts/run_<ts>/<case_id>.md` — full trajectory per failing case.
6. Triage every real failure into its own GitHub issue per #413's acceptance criteria — don't
   just note it and move on. Distinguish "real harness bug" from "threshold to tune" per the
   normal triage workflow in `evals/AGENTS.md` / `evals/REFERENCE.md`.
7. Clean up the scratch content `mcp_untested_live.jsonl` created (search "Chad Eval Test" across
   GitHub issues, Notion, Todoist, downloaded Presenton decks).
