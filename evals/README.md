# Harness Evals

Tests Chad's **harness** (prompt assembly, tool/subagent selection, orchestration, context
budget) — not just whether a model gave a good answer. Complements live probes like
`scripts/trace_probe.py`. Triage: `evals/triage.py` → `evals/runs/triage.md`.

## Where to go

| Doc | Purpose |
|---|---|
| [`AGENTS.md`](./AGENTS.md) | **Agent runbook** (Matt defaults, worktree Brain bootstrap, lanes, ports) |
| [`REFERENCE.md`](./REFERENCE.md) | Schema, rubrics, probes, CI, mining, outputs |
| [`HARNESS_LESSONS.md`](./HARNESS_LESSONS.md) | Append-only: harness failure classes + wins (don't re-learn) |
| [`docs/evals/FOUND_AND_FIXED.md`](../docs/evals/FOUND_AND_FIXED.md) | Known live/workflow bugs already fixed |
| [`docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md`](../docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md) | Named-SaaS browser cluster + OPEN re-test suite |
| [`docs/evals/QUALITY_HITL.md`](../docs/evals/QUALITY_HITL.md) | Behavioral quality / Gemma HITL lane design |
| [`docs/architecture/CHAD_SELF_EVAL_LOOP.md`](../docs/architecture/CHAD_SELF_EVAL_LOOP.md) | Chad-on-Chad observe→fix→retest (#358) |
| [`SELF_EVAL_DO_NOT_SHIP.md`](./SELF_EVAL_DO_NOT_SHIP.md) | Packaging exclude list for self-eval-only files |
| [`docs/runbooks/`](./docs/runbooks/) | One-off sweep/overnight runbooks |

**Division of labor:** LLM-as-judge **analyzes** (scores + classifies). Chad-on-Chad **fixes and
retests**. Details in `HARNESS_LESSONS.md`.

## Human quick start

```powershell
$env:PYTHONPATH="."
# Free CI-style lanes (no Brain required)
.\.venv\Scripts\python.exe -m evals.runner --prompt-audit
.\.venv\Scripts\python.exe -m evals.runner --tool-contract
.\.venv\Scripts\python.exe -m evals.runner --list

# Live/workflow: point at a running Brain (worktree :8788 for uncommitted code; prod :8767
# connect-only — never kill/restart :8767). See AGENTS.md.
$env:CHAD_SERVER_URL = "ws://localhost:8788/ws"
.\.venv\Scripts\python.exe -m evals.runner --suite smoke --live --judge --judge-model "qwen/qwen3.5-9b"
```

Read `evals/runs/latest.md` (and `triage.md`) after a run. Exit `0`/`1` for CI.

Add a synthetic case: one JSON line in `evals/datasets/*.jsonl` — schema in [`REFERENCE.md`](./REFERENCE.md).

## Memory lanes

Three free lanes plus one that costs tokens. The split matters: the free lanes prove
the **harness** behaves, the live one is the only thing that says the **model** does.

```powershell
# Free — no server, no model tokens
.\.venv\Scripts\python.exe -m evals.memory_recall_probe   # does the right fact reach the prompt?
.\.venv\Scripts\python.exe -m evals.memory_write_probe    # does the gate keep the graph clean?
.\.venv\Scripts\python.exe -m evals.runner --graph-memory # typed-edge walk fixtures

# COSTS MODEL TOKENS — one real turn per case, deliberate lane, never in CI
.\.venv\Scripts\python.exe -m evals.runner --memory-write-live
.\.venv\Scripts\python.exe -m evals.runner --memory-write-live --limit 4   # smoke
```

`--memory-write-live` (#660) answers the question the offline write probe cannot:
**does the model call `save` at the right moments, with content worth storing?**
A model that never saves anything at all scores full marks on `memory_write_probe`.
Categories: `should_save`, `should_not_save`, `supersede`, `tier1_ask_first`
(the consent tiers in `prompts/persona/memory_policy.md`).

It also reports **repair@2** — how often a save the gate rejected was corrected
within the same turn. The gate's whole "rejections are prompts" design rests on
that number; if it is low, fix the rejection *text*, not the model.

Cases that error out (model/provider failure, context blowout) are marked **void**
and excluded from the rate rather than counted as failures — a turn that never ran
says nothing about whether the model would have saved, and scoring it as "didn't
save" invents a finding. Re-run voids before reading anything into a result.

Isolation: it points `knowledge_graph.graph_path` at a temp file seeded from the
fictional persona fixture, one fresh graph per case, so it never touches your real
`data/knowledge_graph.json` and never uses real personal data. It runs turns
in-process (`evals/inprocess.py`), so it needs no Brain and never touches prod `:8767`.

Cost: **~70–130s per case** on this box, plus a one-off core build of ~60s, so the
full 15-case set is roughly 20–30 minutes of local GPU. Use `--limit` while
iterating. Progress is flushed to `evals/runs/memory_write_live_latest.md` after
every case, so a long run can be watched (and a hang told from slowness).

The lane builds the core **once** and reuses it (`build_inprocess_session` +
`run_turn_with` in `evals/inprocess.py`). Rebuilding per case — what
`run_turn_inprocess` does — re-spawns the whole MCP fleet and re-preloads the
supervisor model every turn; at ~12 node subprocesses and one 26B load/evict cycle
per case that was enough to kill a run partway through.

### Teardown and the 9B swap tax (#675)

Any in-process lane must call `shutdown_inprocess_session(container)` in a `finally`.
Warmup spawns a node subprocess per MCP server, and nothing used to disconnect them —
so every run leaked its fleet and consecutive runs stacked fleets until the box had
dozens of stray node processes and new runs died almost immediately. A crash is covered
separately by `src/shared/child_reaper.py` (a kill-on-job-close job object), because
`finally` and `atexit` do not run on a hard death.

If a lane does **not** measure tool selection, set `CHAD_PINNED_TOOLS` to the tools it
needs and the 9B selector is skipped entirely:

```powershell
$env:CHAD_PINNED_TOOLS = "manage_user_memory"
.\.venv\Scripts\python.exe -m evals.runner --memory-write-live
```

The 9B does not fit alongside the 26B supervisor inside the VRAM budget, so each
selector call evicts the supervisor and the next turn pays to reload it — measured here
at 41s of selector plus a 22s re-preload. Core tools are still bound; an empty string
means "core tools only". **Never set it in production.**

## CI

`.github/workflows/evals.yml` gates free lanes (`--prompt-audit`, `--tool-contract`; optionally
`tool_selection` / `--replay` when Supabase secrets exist). Judge/live/workflow are **not** in CI.
Details: [`REFERENCE.md`](./REFERENCE.md) (and the workflow file).

## Project orientation and resume routes

`python -m evals.runner --project-context` checks the five task routes for
persistence, specialists, memory, coding and compaction. It runs offline without
starting the model gate or Brain. Reports use `evals/runs/project_context_latest.*`
(gitignored); maintain this procedure here rather than adding another runbook.

For a controlled reader comparison, opt in with an **already-loaded** LM Studio
instance and the baseline revision:

```powershell
python -m evals.runner --project-context --model <loaded-instance-id> --compare-ref <baseline-commit>
```

The same model, temperature, ordered orientation files and character budget are
used on both revisions. Requests run sequentially on localhost; the probe issues
no load/unload commands. It records context, responses, token usage and latency.
Scores check expected references/check signals, not semantic correctness.
Manually inspect every answer for preserved constraints, grounded rationale and
unnecessary parallel implementations. Unknown references should be reported as
missing information, not invented. A small reader trial is not evidence of
successful autonomous repository exploration or live compaction.

For a full coding-agent cold start, give the same scenario tasks in
`evals/project_context_probe.py` to a fresh session on each revision; record what
files it actually opened, wrong turns, checks selected and token use. For resume,
run the compaction task through a real context reset and confirm that the goal,
constraints, references and rationale remain available. Update the owning route
when a scenario fails, instead of creating a task-specific instruction file.
