# Harness Eval Progress & Roadmap

**Last updated:** 2026-07-26 (browser named-SaaS cluster + product-name gate + eval connect preload;
see `docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md` and `docs/evals/FOUND_AND_FIXED.md`.
Prior: 2026-07-24 HITL + Gemma-4 quality-eval vision — `docs/evals/QUALITY_HITL.md`;
seed suite `wedding_quality_hitl`. Catalog of eval-found bugs: `docs/evals/FOUND_AND_FIXED.md`.
Prior: epic #305 close-out #306-316)

## Current Status

**CI is currently OFF (2026-07-24):** GitHub Actions minutes ran out; the `.github/workflows/
evals.yml` PR gate described below does not actually run right now and stays off until the repo
goes public (see `docs/RELEASING.md`). Run the free lanes locally instead (`CLAUDE.md`'s "Free CI
eval lanes" commands) before merging.

The autonomous harness eval suite is **FUNCTIONAL across dry-run, judge, live, workflow, prompt-audit,
model-comparison (4c), OATS replay, tool-contract, and judge-calibration lanes**, with full-trajectory
transcripts, root-cause triage on every dry-run/live/workflow run, load-time dataset schema validation,
and a CI gate for every free/zero-token lane. Two more standalone probes (`intent_router_live.py`,
`reflect_probe.py`) exist outside `runner.py`'s argparse surface, plus a real-usage mining tool
(`evals/tools/mine_prod_cases.py`) that turns production traffic into candidate eval cases.

- **Latest:** epic #305 ("Upgrade harness eval system to 2026 agent-eval standard") fully closed —
  CI gate (#306), latency/cost assertions (#307), doc reconciliation (#308), schema validation (#309),
  offline-fallback surfacing (#310), tool-arg assertions (#311), safety/negative datasets (#316),
  judge calibration (#312), multi-sample judge vote (#313), production-trace mining (#314), dataset
  sizing (#315)
- **Branch:** main/dev
- **Test suite:** `python -m evals.runner --suite <name> [--judge] [--live] [--workflow] [--models a,b] [--repeat N]`
- **Exit code:** 0 (all pass) / 1 (failures) — CI-gated on the free lanes (`.github/workflows/evals.yml`)

## Phases & Completion

| Phase | Feature | Status | Proof | Notes |
|-------|---------|--------|-------|-------|
| **0** | Free dry-run lane | ✅ DONE | 12/13 pass (1 known web-search fidelity gap) | SpyAdapter intercepts payload pre-LLM; ~30s first case (embedding load), <1s after |
| **1** | Live single-turn lane | ✅ DONE | smoke/planner/email cases green | Real WS probe; captures executed tools, plan, response |
| **2a** | LLM-as-judge rubrics | ✅ DONE | tool_appropriateness, prompt_contract, planner_quality, response_grounded | Lane-agnostic; pins small loaded model (qwen/qwen3.5-9b, gemma-4-31b-qat) |
| **2b** | Full workflow lane | ✅ DONE | wf_smoke PASS (2 rounds, completed=True) | plan→pause→auto-proceed→execute loop; workflow_outcome judge |
| **4a** | Transcripts + triage | ✅ DONE | per-case transcripts + clustered triage.md every run | `evals/triage.py` clusters failures by root cause → area + files; `harness-triage` skill drives diagnosis |
| **4b** | Multi-run (pass@k/pass^k/variance) | ✅ DONE | `--repeat N`; aggregate_runs() → pass@k/pass^k + mean±stderr; flaky cases flagged | Any lane. Case gates on pass^k (all N pass); report shows flaky (some-but-not-all). Persisted to run JSON. |
| **3** | Prompt audit (cross-scope contradictions) | ✅ DONE | `evals/prompt_audit.py`, `--prompt-audit` flag | Assembles every scope's real system prompt; flags placeholder leaks, duplicate/near-duplicate fragments, bloat (free); `--judge` adds the contradiction rubric per scope |
| **4b+** | Scoreboard over history | ⏳ TODO | — | `evals/report.py`: trend over last N runs (pass-rate, per-rubric judge mean, p95 tokens); tag runs with git commit |
| **4c** | Model comparison matrix | ✅ DONE | `evals/compare.py`, `--models a,b,c` | Hot-swap already-loaded models only; VRAM-gated, verifies the swap took effect, fixed external judge. Requires `--live`/`--workflow`. |
| **5** | CI gate (free lanes) | ✅ DONE | `.github/workflows/evals.yml` — `tool_selection`, `--prompt-audit`, `--tool-contract`, `--replay` on every PR/push | Zero LLM tokens, no server/GPU (issue #306). Nightly full judged run still ⏳ TODO. |

**Also shipped, outside the phase numbering:**
- **OATS replay lane** (`evals/replay_lane.py`, `--replay`) — offline K-fold holdout proving whether outcome-aware embedding refinement actually improves tool selection (rank/recall, base vs refined). Free — no server, no model tokens.
- **Tool contract gate** (`evals/tool_contract.py`, `--tool-contract`) — every native tool needs a display formatter + `tool-ui.tsx` entry; ratchets against `evals/datasets/tool_contract_baseline.json`, fails only on NEW drift. Free, deterministic.
- **Intent-router live probe** (`evals/intent_router_live.py`, standalone — `python -m evals.intent_router_live`) — calls the real Gemma model to check conversation/agent/plan routing fidelity against `evals/datasets/intent_router.jsonl`. Not wired into `runner.py` (different case schema: `query`/`expect_mode`, not `prompt`/`expect`).
- **Reflective-lessons probe** (`evals/reflect_probe.py`, standalone — `python -m evals.reflect_probe`) — read-only validation that the judge can propose good, generalizable lessons from real conversation history. No dataset file (pulls live conversations from the DB); step 1 of the reflective-lessons build order.
- **Judge calibration** (`evals/judge_calibration.py`, `--judge-calibration`, issue #312) — judge-vs-human agreement on a hand-labeled golden set (`evals/golden/judge_golden.jsonl`, 18 cases across all 5 rubrics, real transcripts + constructed adversarial cases). **Baseline (2026-07-04, qwen/qwen3.5-9b): 15/18 (83%) overall agreement** — prompt_contract/response_grounded/workflow_outcome 100%, tool_appropriateness 83% (5/6), planner_quality 33% (1/3, weakest). Two real findings from the 3 planner_quality disagreements: (1) the rubric's prompt over-indexes on "delegate_to_* are the only legitimate tools" and penalizes a correct low-level tool (`get_datetime`) as "potentially non-existent" — needs a caveat that non-delegate native tools are also legitimate when appropriate; (2) the judge doesn't reliably catch a genuinely vague/unverifiable step ("do the research and figure it out") when the tool choice is otherwise right — scored 0.95 vs. human 0.2. Also one tool_appropriateness disagreement: judge doesn't know `delegate_to_*` handoffs are always-bound routing infra (not a selection choice) and penalizes their mere presence on a pure-smalltalk prompt. Tuning `planner_quality`'s and `tool_appropriateness`'s prompts to address these is a natural follow-up, not blocking — PASS_THRESHOLD=0.6 itself looks reasonable given where the score-vs-human gaps actually fall (0.6 vs 0.85, 0.95 vs 0.2), not systematically too strict or too lax.
- **Dataset schema validation** (`load_suite`, issue #309) — unknown `expect`/`judge` keys raise a load-time `ValueError` naming the file/line/bad key, instead of silently no-opping (a typo'd key used to report PASS while testing nothing).
- **Safety/negative-case datasets** (`evals/datasets/safety.jsonl`, issue #316) — over-triggering guard (dry-run), destructive-action approval pause (`--live`), ambiguous-request clarification (`--live`). Prompt-injection-via-tool-results (#341) and session-isolation leakage (#342) explicitly out of scope — need new harness plumbing, tracked as separate follow-ups.
- **Dataset sizing** (issue #315) — `intent_router` (8→20), `memory_selection` (4→14, incl. negative cases), `planner` (3→6), `planner_gather_first` (3→5) grown so `--repeat N` pass@k/pass^k is meaningful signal, not N=2-4 noise. `wedding_*`/`smoke`/`telegram` deliberately left small (real OAuth/network/long-running cost) — see the sizing-rationale comment at the top of each `.jsonl`.
- **Production-trace mining** (`evals/tools/mine_prod_cases.py`, issue #314) — mines `tool_outcome_log` (grouped by `turn_id`) into candidate real-usage eval cases. Privacy-first: drops (not redacts-and-keeps) any turn matching email/phone patterns, writes only to gitignored `evals/datasets/_staging/`, never prints prompt text to stdout. Candidates require human review before promotion; `evals/datasets/prod_sampled.jsonl` (15 hand-reviewed cases) is the first result — 12/15 pass, 3 are genuine tool-selection gaps (add_task/file-ops for terse phrasing) flagged as a follow-up (#387) rather than loosened to pass.

## What Works Today

```powershell
# FREE deterministic (no model tokens) — always run first
python -m evals.runner --suite tool_selection

# + semantic judge (1 small-model call/case; pinned to qwen/qwen3.5-9b)
python -m evals.runner --all --judge --judge-model "qwen/qwen3.5-9b"

# LIVE single-turn (real server, executed tools + plan + response)
python -m evals.runner --suite smoke --live --judge --judge-model "qwen/qwen3.5-9b"

# WORKFLOW full loop (plan→approve→execute)
python -m evals.runner --suite workflows --workflow --judge --judge-model "qwen/qwen3.5-9b"

# MULTI-RUN: pass@k / pass^k + variance (any lane) — surfaces flaky/non-deterministic cases
python -m evals.runner --suite tool_selection --repeat 5
```

Report goes to `evals/runs/latest.md` (failures + fixes). Exit 0/1 for CI.

## Real Findings Already Caught

1. **web_search dry-run binding issue** — dry-run fidelity gap (local embedding fallback, MCP offline) vs live production (Supabase RAG + MCP). Mitigated: assert web/MCP tools in `--live` only. Case: `live_check.jsonl` web_search_live.
2. **planner weak 1-step plans** — planner_quality judge 0.2 on wf_smoke (empty tools, combines two operations). Real finding: planner prompt needs better instruction on decomposition. Case: `workflows.jsonl` wf_smoke (planner_quality scoped off smoke, kept on richer cases).
3. **response hallucination (web search)** — response_grounded 0.0: model claimed NVDA prices not in tool results. Case: `live_check.jsonl` web_search_live.

## Datasets

| File | Cases | Lane | Purpose |
|------|-------|------|---------|
| `tool_selection.jsonl` | 14 | dry-run + judge | bound tool selection (free lane's main test battery) |
| `prompt_contracts.jsonl` | 3 | dry-run + judge | system prompt coherence (token budget + no-contradict audit) |
| `information_gathering.jsonl` | 4 | dry-run + judge | read-before-act enforcement — email/GitHub cross-ref should read source before acting |
| `memory_selection.jsonl` | 14 | dry-run + judge | `manage_user_memory` binds on remember/forget/recall-style prompts, plus negative cases where memory should NOT fire |
| `safety.jsonl` | 4 | dry-run (over-triggering) + live (approval/clarification) + judge | negative-case robustness: no over-triggering on small talk, destructive actions pause for approval, ambiguous asks trigger clarification (issue #316) |
| `tool_args.jsonl` | 5 | live only + judge | component-layer checks — not just *which* tool, but *what arguments* it was called with (issue #311) |
| `browser_golden.jsonl` | 5 | live only (needs browser_agent + Playwright MCP) | DOM-first browser_agent golden tasks (epic #274/#275); online-only |
| `planner.jsonl` | 6 | live + judge | planner logic (`min_steps`/`max_steps`, `response_includes`) |
| `planner_gather_first.jsonl` | 5 | workflow (round 1 only) + judge | scout-gather activity appears BEFORE the plan-approval snapshot, across email/repo/calendar/codebase-scan domains |
| `live_check.jsonl` | 3 | live + judge | production fidelity check (web, email, time on real server) |
| `smoke.jsonl` | 1 | live + judge | smoke test (one fast case) — proves the live lane end-to-end |
| `telegram.jsonl` | 1 | live + judge | live-lane smoke for `send_telegram_message` |
| `workflows.jsonl` | 3 | workflow + judge | full plan→execute (smoke, research, file-write) |
| `wedding_links_once.jsonl` | 1 | workflow + judge | single-case smoke for Drive-traversal → shopping-link delegation |
| `wedding_registry.jsonl` | 2 | workflow + judge | heavy real-world Drive + web-research workflow (online-only; Drive OAuth required) |
| `wedding_roadmap_site.jsonl` | 1 | workflow + judge | multi-subagent + `frontend-design` skill + `code_agent` site build (online-only, long-running) |
| `intent_router.jsonl` | 20 | standalone (`evals/intent_router_live.py`) | conversation/agent/plan intent-routing fidelity against the real Gemma router — different case schema (`query`/`expect_mode`), not consumed by `runner.py` |
| `prod_sampled.jsonl` | 15 | dry-run + judge | hand-reviewed, genericized cases mined from real `tool_outcome_log` usage (refs #314) — real typos/terse phrasing/grammar noise; not CI-gated |

Each case = one JSON line. Edit freely; add tests by appending a line.

**Dataset sizing (refs #315):** `intent_router`, `memory_selection`, `planner`, and `planner_gather_first`
were grown to make `--repeat N` pass@k/pass^k a meaningful reliability signal instead of noise from
N=2-4. `wedding_registry`/`wedding_roadmap_site`/`wedding_links_once`/`smoke`/`telegram` were
deliberately left small — each requires real OAuth/network/long-running infra where case count trades
directly against run cost, and the existing cases already cover the distinct capability variants worth
testing (see the sizing-rationale comment at the top of each of those `.jsonl` files).

## Server-Side Observability Enabler (Phase 2b)

To support the workflow lane, a small server change made paused turns observable:

- `TurnCompleteEvent` now carries `awaiting_approval` / `awaiting_input` flags
- `turn_coordinator` derives them from `final_state` (`graph_interrupt_pending` / `paused_for_input`)
- `limb_coordinator._broadcast_turn_complete_event` puts them + `stop_reason` on the `ai_response` payload

Real clients (UI, eval harness) can now distinguish "waiting on the user" from "done" without text heuristics. This is observability-positive independent of eval harness use.

## Next Steps (Priority Order)

### Phase 3: Prompt Audit ✅ DONE
**Goal:** Catch cross-scope prompt contradictions and bloat automatically.

Shipped as `evals/prompt_audit.py` + the `--prompt-audit` flag: iterates over every scope
(supervisor, conversational, heartbeat, planner, validator, per-subagent), assembles the real
system prompt via the dry-run SpyAdapter path, and runs deterministic checks (placeholder leaks,
duplicate/near-duplicate fragments, per-scope token budget/bloat). `--judge` adds a 1-shot
contradiction rubric per scope on top of the free deterministic pass.

### Phase 4a: Transcripts + Triage ✅ DONE
**Goal:** Autonomous root-cause analysis. Every run now writes:
- `evals/runs/transcripts/run_<ts>/<case_id>.md` — full trajectory per case (what the model saw + did)
- `evals/runs/triage.md` — failures clustered by root cause, each naming the likely area + files
- `.agent/skills/harness-triage` — skill that runs the suite, reads triage.md, opens transcripts, classifies each cluster (real bug vs threshold), and names the fix.

Clustering lives in `evals/triage.py` (deterministic, no model call). Signature → area map mirrors the
skill's cheat-sheet (tool wiring, context bloat, planner decomposition, workflow stall, hallucination, etc).

### Phase 4b: Multi-run (pass@k / pass^k / variance) ✅ DONE
**Goal:** Make every number statistically honest (agent output is non-deterministic).

- `--repeat N`: runs each case N times. `core.aggregate_runs()` collapses them into one CaseResult
  with `.repeat = {n_runs, n_passed, pass_at_k, pass_hat_k, pass_rate, run_passes, metric_stats}`.
- **pass@k** (≥1 success — exploratory) and **pass^k** (all-k succeed — reliability) both reported;
  the case GATES on pass^k (conservative regression gate). Cases that pass-some-but-not-all are
  flagged **FLAKY** in the report + a STABILITY line in transcripts (`[..x.]` per-run marks).
- Per-metric **mean ± stderr** across runs (tokens, tool_count, duration, plan_steps, rounds) shown
  on failing cases and stored in the run JSON.
- Works in every lane (dry-run / live / workflow). Reuses one harness/connection across repeats.
- **Why (2026 consensus):** "a single passing test tells you very little." This is the foundation
  that makes model comparison (4c) trustworthy. PROVEN: the non-deterministic LLM tool-selector
  shows up as flaky cases under `--repeat`.

**Still TODO (4b+): scoreboard over history** — `evals/report.py` reading the last N run JSONs for a
pass-rate trend, per-rubric judge mean, p95 tokens; tag runs with git commit. Run-over-run delta
should compare against stderr ("overlap within 1 stderr = not significant").

### Phase 4c: Model Comparison Matrix ✅ DONE
**Goal:** "How did different models do at the same task?" — head-to-head on one dataset.

**Mechanism (DECIDED): hot-swap already-loaded models only.**
- The server hot-swaps its model at runtime: send `set_model_config` over WS →
  `ModelChanged` event → `model_coordinator._on_model_changed` → `router.reload_models()`.
  Next turn re-points to the new `local_models.active_model`. (See `limb_coordinator.py:270`.)
- **VRAM SAFETY (hard gate):** before switching, query `GET http://localhost:1234/v1/models` and
  REFUSE any model id not already loaded. Switching between already-loaded models (qwen3.5-9b ↔
  gemma-4-31b-qat) is instant and loads nothing. NEVER let it JIT-load a heavy model.
- **Verify the swap took effect** before running (probe one turn, confirm reported model id matches);
  abort loudly if not, so a silent no-op swap can't produce garbage "comparison" of one model vs itself.
- Fresh `conversation_id` per (model, case) to avoid cross-model state contamination (2026: shared
  state inflates/correlates results).

**Implementation:**
- `evals/compare.py` + `--models "qwen/qwen3.5-9b,google/gemma-4-31b-qat"` on the runner.
- For each model: gate-check loaded → hot-swap → verify → run the suite (ideally with `--repeat N` from 4b) → tag results with model id.
- Render a comparison matrix: rows = cases (or clusters), cols = models, cells = pass-rate / mean judge score.
  Add a summary row (overall pass@1, mean latency, mean tokens per model).
- **Judge must be a FIXED external model** (e.g. always qwen/qwen3.5-9b) for every model under test —
  never let a model judge itself (self-enhancement bias is +5-7%). Pointwise scoring is fine and more
  robust to manipulation than pairwise; if you add pairwise head-to-head later, randomize answer order
  and average both orderings to cancel position bias (~40% flip rate otherwise).
- **Note:** model comparison only applies to `--live`/`--workflow` (dry-run calls no model). The headline
  value is comparing the agentic behavior (tool selection, planning, execution), not just final text.

### Phase 4b/4c restart fallback (if hot-swap proves flaky)
If `set_model_config` hot-swap is unreliable (string-vs-dict `active_model` plumbing, `_recursive_update`
semantics, or the handler's name-extraction at `limb_coordinator.py:287` throwing on a string id), fall
back to **restart-server-per-model**: set `config.yaml local_models.active_model`, boot a fresh server,
verify the loaded id, run, kill. Slower (~60-90s/model) but bulletproof isolation. Still gate on
"already loaded in LM Studio." Hardening the WS handler to accept a string `active_model` is a small,
legitimate fix that would make hot-swap robust.

### Phase 5: CI Gate + Nightly (½ day)
**Goal:** Automated gating + overnight validation.

- ✅ PR gate (GitHub Actions, `.github/workflows/evals.yml`, issue #306) on every PR/push
  touching `evals/**`, `src/**/*.py`, `config/**`. Two lanes are genuinely secretless and
  always run: `--prompt-audit` (mock persona fallback) and `--tool-contract` (pure static
  analysis). `tool_selection` (dry-run) and `--replay` both turned out to need **live
  Supabase read access** — RAG tool selection has no offline embedding index, and replay
  needs real `tool_outcome_log` rows — so both are conditional on `SUPABASE_URL`/
  `SUPABASE_ANON_KEY` repo secrets and skip cleanly (not fail) until those are added.
  Uploads `latest.md`/`triage.md`/`tool_contract_latest.md`/`replay_latest.md` as artifacts.
  Documented in `evals/REFERENCE.md`.
- ⬜ Nightly (cron/scheduler): full suite with cloud judge → run file → optional Telegram/Push
  summary — needs a persistent self-hosted runner with LM Studio + prod Brain access, not a
  stock GitHub-hosted runner. Separate follow-up.
- **Why:** catches regressions before merge; nightly gives you trend data without manual runs

## Known Gaps & Caveats

1. **Dry-run tool selection fidelity** — local embedding fallback (MCP offline) doesn't match Supabase RAG production. Assert web/MCP tool selection in `--live` only; dry-run is faithful for token budgets, prompt contract, and local-tool binding.
2. **Planner decomposition** — 9b model produces weak 1-step plans (empty tools array). Planner prompt needs guidance on decomposition depth and tool naming. Not a test suite issue; a prompt issue.
3. **Multi-turn pause_for_input** — workflow lane auto-approves plans, but mid-execution `pause_for_input` calls aren't tested yet. Add a workflow case that exercises a tool's pause and resumes from there.
4. ~~No CI integration yet~~ — resolved for free lanes by `.github/workflows/evals.yml` (issue #306); nightly judged/live/workflow runs remain future work.

## Key Files & Entry Points

| File | Purpose |
|------|---------|
| `evals/runner.py` | CLI entry: `--suite`, `--judge`, `--live`, `--workflow`, `--models`, `--replay`, `--prompt-audit`, `--tool-contract` |
| `evals/core.py` | Dataset loader, assertions, multi-run aggregation, report renderer |
| `evals/dry_run.py` | SpyAdapter-based pre-LLM payload capture |
| `evals/live.py` | WS probe: `probe_live()` (single turn), `probe_workflow()` (full loop) |
| `evals/judges.py` | LLM-as-judge rubrics; build `--judge-model` adapter |
| `evals/triage.py` | Clusters failures by root cause into `triage.md` (deterministic, no model call) |
| `evals/prompt_audit.py` | Phase 3 — assembles every scope's real system prompt; flags leaks/dupes/bloat, `--judge` adds contradiction rubric |
| `evals/compare.py` | Phase 4c — head-to-head model comparison; hot-swaps already-loaded LM Studio models, backs `--models` |
| `evals/replay_lane.py` | OATS replay lane — offline K-fold holdout, backs `--replay` |
| `evals/tool_contract.py` | Native-tool contract ratchet gate, backs `--tool-contract` |
| `evals/intent_router_live.py` | Standalone live probe for intent-mode routing; run via `python -m evals.intent_router_live`, not through `runner.py` |
| `evals/reflect_probe.py` | Standalone read-only probe validating reflective-lessons judge quality; run via `python -m evals.reflect_probe` |
| `.agent/skills/harness-eval/` | Agent skill for running evals autonomously |
| `evals/README.md` | Index; `REFERENCE.md` for schema; `AGENTS.md` for runbook |
| `evals/datasets/*.jsonl` | Golden test cases (one per line) |
| `evals/runs/` | Run outputs: `run_<ts>.json` (machine), `latest.md` (human) |

## Model Safety Reminder

**NEVER target heavy models for the judge.** LM Studio JIT-loads whatever id you name.

- ✅ Safe: `qwen/qwen3.5-9b`, `google/gemma-4-31b-qat` (small, already loaded)
- ❌ Unsafe: `qwen/qwen3.6-35b-a3b` (35B, will stall VRAM on top of loaded models)
- ❌ Avoid: `--judge-model local` (may follow config and point at heavy model)
- **We do NOT use cloud/gemini.** All judge calls go to LM Studio local endpoint.

Check loaded models: `(irm http://localhost:1234/v1/models).data.id`.

## For the Next Agent

1. Remaining work is Phase 4b+ (scoreboard over history, `evals/report.py`) and Phase 5 (CI gate).
   Phases 0–4c and prompt-audit/replay/tool-contract are all shipped — don't re-propose them.
2. Use the existing `evals/` infrastructure; don't rebuild capture (it's solid).
3. Keep the free deterministic lane cheap and fast (no LLM calls).
4. Pin judge to small loaded models; test locally before any nightly/cloud runner.
5. Add test cases by editing `.jsonl` files, not Python code.
6. Read `evals/runs/latest.md` after every run — it's your feedback loop.

Good luck! The harness is in good shape. Focus on making failures actionable (Phase 4 triage) and preventing regressions (Phase 5 CI).
