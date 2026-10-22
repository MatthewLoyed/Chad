# Harness Evals — reference

Deep reference for lanes, dataset schema, judges, probes, and outputs.
**Agents:** start at [`AGENTS.md`](./AGENTS.md). **Index:** [`README.md`](./README.md).
**Lessons:** [`HARNESS_LESSONS.md`](./HARNESS_LESSONS.md).

Judge defaults (current code): scores **0–10** (pass ≥ 6); `EVAL_JUDGE_TIMEOUT_S` /
`EVAL_JUDGE_CALL_TIMEOUT_S` default **1200**; `EVAL_JUDGE_TEMPERATURE` default **0.8**;
`EVAL_JUDGE_NUM_CTX` default **32768**; `EVAL_JUDGE_USER_CHAR_BUDGET` default **0** (no cap);
`EVAL_JUDGE_DISAGREEMENT_SPREAD` default **3.5**. Analysis only — harness fixes go through
Chad-on-Chad (#358), not free-form judge suggestions.

---

## How it works (lanes)

| Lane | Cost | What it checks | How |
|---|---|---|---|
| **dry-run** (default) | FREE (no LLM) | bound tools, token budgets, prompt-contract substrings | `evals/dry_run.py` runs the real `TurnCoordinator` with a `SpyAdapter` that intercepts the payload *before* the model is called |
| **judge** (`--judge`) | 1 small-model call/case | semantic quality: right tools? subagent reachable? contradictory prompt? sound plan? workflow done? | `evals/judges.py` — pin a small loaded model with `--judge-model "<id>"` |
| **live** (`--live`) | 1 real server turn/case | what the model *actually did* in **one** turn: executed tools, the planner's plan, the final response | `evals/live.py` `probe_live()` connects to the running server over WS |
| **workflow** (`--workflow`) | N real turns/case | the **whole** plan→approve→execute loop: does a proposed plan actually get carried out to completion? | `evals/live.py` `probe_workflow()` drives plan → auto-`proceed` → execution over one WS connection |

The dry-run captures *exactly what the model would have received* (cheapest signal — run it first).
The live lane captures *what the model actually did* (needed for `planner_quality` / `response_grounded`
and for asserting on **executed** tools). In the live lane, `tools_include/any/exclude` check executed
tools, and `min_steps`/`max_steps`, `response_includes`/`response_excludes` become available.

> ### Eval mode ≠ live user turns (`platform="eval"`)
> Harness turns are **not** scheduled like desktop chat. They tag `platform="eval"` and get a
> distinct residency policy: eval owns the machine while running, keeps the 9B selector hot,
> skips production post-turn warm thrash, and keeps **live/workflow sequential** (subagents /
> VRAM). Dry-run tool-selection may parallelize. Canonical: `src/core/orchestration/eval_exclusivity.py`
> · `evals/AGENTS.md` · `docs/features/MODEL_LIFECYCLE.md`.

> ### Fidelity caveat — where each lane is trustworthy
> - **dry-run** is faithful for **token budgets, prompt contract, and locally-registered tool binding**.
>   It is **NOT** faithful for **web/MCP tool selection**: offline it falls back to local embeddings and
>   may not have MCP tools registered, so a web/email/MCP prompt can bind meta-fallback tools
>   (`ask_user_question`, `search_for_tool`) instead of the real tool. **Assert web/MCP tool selection in
>   the `--live` lane** (Supabase RAG + MCP = production). Verified: "search the web …" binds nothing in
>   dry-run but correctly calls `brave_web_search` live.
> - **live** is production-faithful but costs a real turn and depends on the running server's model (`CHAD_SERVER_URL` — worktree `:8788` for edit loops; prod `:8767` connect-only).
> - **Offline mock prompts**: if Supabase `persona_config` warmup fails (network down, bad creds), dry-run
>   and `--prompt-audit` silently substitute short hardcoded placeholder prompts so the run still completes
>   — but any `system_includes`/`system_excludes`/`prompt_contract` verdict from that run is checking the
>   stub text, not the real production prompt. This is now surfaced loudly: `latest.md` gets a `⚠️ WARNING`
>   banner at the top, `metrics.prompts_offline: true` on every affected case in the run JSON, and
>   `triage.md` buckets prompt-content failures under a distinct "Offline mock prompts active" cluster
>   instead of misattributing them to a real prompt bug. Fix Supabase connectivity and re-run.

## Datasets (`evals/datasets/*.jsonl`)

One case per line. Every field except `id`/`prompt` is optional — assert only what you care about.

```jsonc
{"id": "email_check",
 "prompt": "check my emails for project updates",
 "category": "agent",
 "expect": {                       // FREE deterministic assertions (dry-run)
   "tools_any": ["get_recent_emails", "gmail_check_inbox"],  // ≥1 must be bound
   "tools_include": ["x"],         // ALL must be bound (use for must-have subagent/tool)
   "tools_exclude": ["control_volume"],  // none may be bound
   "max_tools": 16,                // context-bloat guard
   "min_tools": 1,
   "max_system_tokens": 8000,      // system-prompt bloat guard
   "max_total_tokens": 14000,
   "system_includes": ["text"],    // substring must be in system prompt
   "system_excludes": ["text"]
 },
 "judge": ["tool_appropriateness"], // semantic rubrics, run only with --judge
 "note": "free-text triage hint"}
```

**Live-only `expect` keys** (with `--live`): `min_steps` / `max_steps` (plan length — catches a planner
that under/over-decomposes), `response_includes` / `response_excludes` (substring checks on the final answer),
`max_duration_s` (turn latency regression guard — checked against `LiveTrace.duration_s`), `max_total_tokens`
(cost regression guard — checked against an ~4-chars/token estimate over reasoning + tool results + final
response; approximate, not an exact token count), `tool_args` (component-layer: not just *which* tool was
called but *what it was called with* — see below), `requires_approval` (safety guard — `true` asserts the
turn ended paused at `stop_reason` in `core.PAUSE_REASONS` = `{"awaiting_approval", "awaiting_input"}`, i.e.
a side-effecting action stopped for a human instead of auto-executing on a single turn — see "Safety /
negative-case datasets" below).

**`tool_args`** (live/workflow only — dry-run never executes a tool call, only binds schemas, so there's
nothing to check args against): a list of per-tool-call constraints, checked against the **last** call to
that tool name in `LiveTrace.tool_calls`.
```jsonc
"expect": {
  "tool_args": [
    {"tool": "web_search", "arg": "queries", "not_empty": true},
    {"tool": "web_search", "arg": "queries", "contains": "Seattle"},   // substring (str) or membership/substring-in-item (list)
    {"tool": "control_volume", "arg": "level", "equals": 0},
    {"tool": "control_volume", "arg": "level", "min": 0, "max": 100},
    {"tool": "send_email", "arg": "to", "regex": "^[^@]+@example\\.com$"}
  ]
}
```
Supported constraint keys: `equals`, `contains` (substring for a string arg, or membership/substring-of-item
for a list arg), `not_empty`, `min`/`max` (numeric range), `regex`. If the named tool was never called, that
fails with a distinct "never called" message (not confused with an args-mismatch). See `evals/datasets/tool_args.jsonl`
for worked examples, e.g. `web_search_on_topic_query` catches "right tool, wrong query" — something a
name-only `tools_include`/`tools_any` check can't see.

**Workflow-only `expect` keys** (with `--workflow`): `must_complete` (the loop reached a real completion —
didn't stall awaiting input or hit the round cap), `max_rounds` / `min_rounds` (how many plan/approve/execute
turns it took), `max_duration_s` / `max_total_tokens` (same latency/cost guards as `--live`, but measured
across the **whole** multi-round workflow). In this lane `tools_include/any` check tools executed **across
all rounds**.

**Judge rubrics** (`evals/judges.py`) — score **0–10** (pass ≥ 6); dock for listed inefficiencies; classify failure modes. Do not treat suggestions as harness patches (see `HARNESS_LESSONS.md`):

Env knobs: `EVAL_JUDGE_NUM_CTX` (default 32768), `EVAL_JUDGE_USER_CHAR_BUDGET` (default 0 = uncapped), `EVAL_JUDGE_TIMEOUT_S` / `EVAL_JUDGE_CALL_TIMEOUT_S` (1200), `EVAL_JUDGE_TEMPERATURE` (0.8), `EVAL_JUDGE_SAMPLES` / `EVAL_JUDGE_MAJORITY_TEMPERATURE` / `EVAL_JUDGE_DISAGREEMENT_SPREAD` (3.5).

**Judge rubrics** (`evals/judges.py`):
- `tool_appropriateness` — right tools chosen? missing subagent/delegate tool = fail. *(dry-run: bound tools; `--live`: executed tools)*
- `prompt_contract` — contradictory/duplicated instructions in the system prompt? *(dry-run payload — cheap)*
- `planner_quality` — is the planner's step list logically ordered & achievable? *(needs `--live`/`--workflow`; category `planning`)*
- `response_grounded` — did the final answer follow from tool results, or hallucinate? *(needs `--live`)*
  On **plan-approval pauses**, the chat text is a canned stub; this rubric judges the **plan
  steps** (the real deliverable) against tool results instead.
- `workflow_outcome` — did the **full** workflow actually accomplish the goal (plan carried out, not a premature "done")? *(needs `--workflow`)*

Judges skip themselves cleanly (score `null`, pass) when their artifact is absent — e.g. `planner_quality`
in the free dry-run lane — so you can list any rubric on any case.

When the judge **times out / returns empty / unparseable JSON**, the harness marks that rubric
`needs_rejudge` (with `diag.likely_cause`, e.g. `context_too_large`) and voids the case as
`void_harness` / `judge_needs_rejudge` — **not** an auto-PASS and **not** a Brain FAIL. Re-run
`python -m evals.tools.rejudge_run` after fixing context (coalesce traces, cap evidence, or raise
`EVAL_JUDGE_NUM_CTX`).

**Schema validation (load-time, strict):** `load_suite` (`evals/core.py`) rejects any `expect` key not in
`_VALID_EXPECT_KEYS` or any `judge` rubric not in `judges.JUDGES`, raising `ValueError` with the dataset
file name, line number, and the bad key — at load time, before any test runs. This exists because a typo'd
key (e.g. `tools_includ`) used to be silently ignored by `check_assertions`/`check_live_assertions`: the
case would report PASS while testing nothing. Both `expect` and `judge` are load-time-strict now (the
runtime fallback in `runner.py::_run_judges` that scores an unknown rubric as "inconclusive" instead of
raising is dead in practice — `load_suite` already refuses to load it — but is kept as defense-in-depth for
any future caller that builds a `Case` without going through `load_suite`). If you add a new `expect` key
or judge rubric, add it to `_VALID_EXPECT_KEYS` / `judges.JUDGES` in the same change, or every dataset using
it will fail to load.

Current suites: `tool_selection`, `prompt_contracts`, `information_gathering`, `memory_selection`,
`prod_sampled` (dry-run); `planner`, `live_check`, `smoke`, `telegram`, `planner_gather_first`,
`tool_args`, `browser_golden` (live/workflow); `workflows`; `safety` (dry-run + live).
`wedding_links_once`, `wedding_registry`, `wedding_roadmap_site`, `wedding_quality_hitl` are
**maintainer-local** (gitignored under `evals/datasets/wedding_*.jsonl` — personal workflow /
HITL quality cases, not in the public repo). Vision for human-reviewed + Gemma-4 behavioral
quality judging: [`docs/evals/QUALITY_HITL.md`](../docs/evals/QUALITY_HITL.md). `intent_router` is
consumed by the standalone `evals/intent_router_live.py`, not `runner.py`. See
`docs/evals/PROGRESS.md` for the full table with case counts. Add more by dropping a `.jsonl` in
`datasets/`.

**Dataset sizing (refs #315):** case count should scale with how much a suite is actually gated on,
not be padded uniformly. Real regression gates (`intent_router`, `memory_selection`, `planner`,
`planner_gather_first`) were grown toward ~15-20 (or as far as cost allows) so `--repeat N` pass@k/
pass^k reflects real reliability signal rather than N=2-4 noise. Suites requiring real OAuth/network/
long-running infra (`wedding_*`, `smoke`, `telegram`) were deliberately kept small — see the sizing-
rationale comment at the top of each of those `.jsonl` files for why.

### Safety / negative-case datasets (`evals/datasets/safety.jsonl`)

Every other dataset is happy-path ("given this prompt, the right tool should fire"). `safety.jsonl`
asserts what should **not** happen — 2026 agent-eval practice treats this as a required layer
alongside task-completion scoring, not an optional extra:
- **Over-triggering guard** (dry-run, free): a purely conversational/factual prompt must not pull
  in any side-effecting action tool (`tools_exclude` on `send_email`/`hubspace_control`/
  `control_volume`/`run_sandboxed_python`). Note: `delegate_to_*` handoff tools are **always** bound
  on the supervisor (`handoffs.create_handoff_tools`), not RAG-selected — don't put them in
  `tools_exclude`, they'll always "leak" and that's not a real finding.
- **Destructive-action approval** (`--live` only, single turn): asserts `requires_approval: true` —
  a side-effecting action must pause (`stop_reason` in `PAUSE_REASONS`) rather than execute directly.
  `probe_live` (unlike `probe_workflow`) never auto-sends an approval reply, so running this case is
  safe regardless of the prompt's real-world destructiveness — it only *observes* whether round 1
  paused; it can't itself complete a multi-round approve-and-execute.
- **Ambiguous-request clarification** (`--live`, safe — asking a question has no side effect): a
  genuinely ambiguous prompt with no prior context should trigger `ask_user_question`
  (`tools_any`) rather than the model guessing.

**Explicitly out of scope for this dataset** (split into follow-ups, refs #305/#316): prompt-injection-
via-tool-results (#341) needs new harness plumbing (synthetic poisoned tool results) beyond a
dataset-only change. Session-isolation cross-conversation leakage (#342) is now covered by its own
standalone live probe — see below.

### Session-isolation regression probe (`evals/session_isolation_probe.py`, issue #342)

A genuine **live, two-conversation** regression guard for the exact incident in
`docs/architecture/SESSION_ISOLATION.md` / issue #269: a clarifying-question / approval card raised
in one chat leaked onto another because the approval message shipped **without** a `conversation_id`.
This doesn't fit the one-prompt-per-line dataset model (it interleaves multiple conversations with
overlapping in-flight state), so it lives as a standalone probe like `parallel_e2e_probe.py`, not a
`datasets/*.jsonl` case.

**The faithful model it tests.** The production web Limb multiplexes **every** conversation over a
**single** websocket (the server keeps one `active_websocket`; `src/remote/limb_coordinator.py`).
Isolation is therefore *not* "one socket per chat" — it is "every turn message the server emits
carries the `conversation_id` it belongs to, and a mismatch is dropped" (the client half is
`sessionGuard.ts::guardConversation`). So the probe drives several conversations, **interleaved,
over one connection** (`evals/live.py::probe_concurrent`) and checks the server's per-event tagging
directly — the server-side mirror of the client guard.

**The scenario** (`session_isolation_probe.py`): drive conversation **A** on the planning lane →
it pauses deterministically at `awaiting_approval` (in practice also raising a real
`tool_approval_request` card — the exact #269 artifact); **leave it unresolved**; then, while A is
paused, drive conversation **B** (a different `conversation_id`) with a trivial plain turn that
completes. `_detect_isolation_leaks` then asserts, mirroring `conversationMatches` semantics
exactly:
- **Misattribution** — no event received while driving X carries a *concrete* `conversation_id` ≠ X
  (B must never see anything tagged A, and vice-versa).
- **Untagged pause payload** — A's `tool_approval_request` card and its paused `ai_response` must be
  *concretely* tagged with A's id. `null` is the legacy allow-path everywhere else, but for these
  human-facing pause payloads an untagged value **is** the #269 bug (it falls through the client's
  "untagged ⇒ allow" branch and lands on whatever chat is on screen).

**Run it:**
```powershell
# LIVE — needs a server (safe against prod :8767: benign prompts, the paused plan is never approved).
$env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.session_isolation_probe
# OFFLINE self-test — proves the detector actually catches the incident class (would fail vs the
# pre-#269 code) with zero live cost, so CI can gate the guard's own correctness. <1s, no server.
$env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.session_isolation_probe --self-test
```
Exit code `0` = isolated (or self-test passed), `1` = leak / could-not-verify / error — CI-gateable.
If the model happens not to pause (shouldn't, on the planning lane), the probe reports
**INCONCLUSIVE** and exits non-zero rather than false-greening. The offline `--self-test` feeds the
detector a clean interleave, the **exact untagged-card bug**, and a cross-tagged event, asserting it
flags the two leaky cases and passes the clean one.

### Coder harness bench (`evals/coder_bench.py`, issue #514)

SWE-bench-style **outcome** eval for `code_agent` — Chad's coding harness had zero measurement
before this ("not reliable enough to do stuff with" per #514). Backend-pluggable
(`CoderBackend` protocol) so it doubles as the bake-off tool for #447 (delegate coding to an
external harness like OpenHands instead of the in-house LangGraph loop): run the same real
tasks through both and compare resolved-rate + cost side by side instead of guessing.

**Why this doesn't fit the `.jsonl`-per-case model above.** `code_agent` has no direct
call/diff API — it's only reachable through the full supervisor graph via the
`delegate_to_code_agent` handoff — and has no existing sandbox/worktree isolation
(`FileWriter` writes real files into whatever directory the process was launched from). So this
probe, unlike the suites above, owns its own execution environment per task: a disposable
`git worktree` checked out at the task's `base_commit`, and a **throwaway server subprocess**
(`src/remote/server.py`) launched with `cwd=<worktree>` on a random **non-8767** port — never
the production Brain, never touches your real working tree. `FileWriter`'s cwd-based
write-whitelist means code_agent's edits land only inside that worktree.

**Task shape** (`evals/datasets/coder_tasks.jsonl`) mirrors real SWE-bench methodology —
execution-based grading, not string-matching a reference patch:
```jsonc
{"id": "reflection_lessons_local_backend", "issue_ref": "#467", "base_commit": "42a38c91",
 "prompt": "<the REAL pre-fix GitHub issue body — never the fix commit's own message, which\n leaks the solution>",
 "fail_to_pass": ["pytest tests/unit/test_reflection_store_local.py"],  // must go fail→pass
 "pass_to_pass": ["pytest tests/unit/test_reflection_dedup.py"],        // must stay green
 "timeout_s": 180}
```
Every task in the committed dataset is **hand-verified**: `fail_to_pass` genuinely fails (or
outright errors — the file often doesn't exist pre-fix) at `base_commit`, and genuinely passes
at the real historical fix commit, checked live via disposable worktrees before being promoted.
Grow the set with the mining helper (mirrors `mine_prod_cases.py`'s shape — draft, then human
review, never auto-promote):
```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tools.mine_coder_tasks --limit 300
```
It scans `git log` for commits touching both `tests/` and `src/`, and drafts candidates to the
gitignored `evals/datasets/_staging/coder_mined.jsonl` — **every candidate's `prompt` is a
placeholder** (the fix commit's own message) that must be replaced with the real pre-fix issue
text (`gh issue view <issue_ref>`) before promotion; using the fix message as-is leaks the
answer and makes the eval meaningless.

**Run it:**
```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.coder_bench                       # all seed tasks, in-house only
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.coder_bench --task <id>            # one task
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.coder_bench --backends inhouse,openhands  # the #447 bake-off
```
⚠️ **Model safety:** `code_agent` pins `qwen/qwen3-coder-30b` (`src/core/orchestration/agents/factory.py`)
— same JIT-load caution as `--judge`/`--live` above: make sure it's the only heavy model you're
about to load, don't run this alongside another heavy model already loaded.

**Outcome classes** (per task, per backend): `resolved` (fail_to_pass now passes, pass_to_pass
still green), `regression` (fixed the target but broke something else), `unresolved` (patch
produced, tests still red), `no-op` (empty diff — agent didn't attempt an edit), `backend-error`
(the harness/backend itself failed — e.g. server didn't boot, or the `openhands` backend, which
is a stub pending #447 Phase 1). Report: `evals/runs/coder_bench_latest.md` +
`coder_bench_<ts>.json`. Exit `0` only if every ran task resolved. Not wired into CI (needs
LM Studio + real git history to check out against).

### How the `--workflow` lane drives a full run

A planner turn (`category: "planning"`) proposes a plan and the graph **pauses for approval**
(`interrupt_after=["approval_wait"]`). The server marks that paused turn with `awaiting_approval`
on the final `ai_response`. `probe_workflow()` watches for it, sends the `--approval` text
(default `"proceed"`) on the **same** `conversation_id` to resume execution, and keeps advancing
on every pause until the turn ends without one (**completed**) or `--max-rounds` is hit (**stuck**).
Executed tools, the plan, and the reasoning accumulate across rounds; the `workflow_outcome` judge
sees the whole run. Flags: `--approval "<text>"`, `--max-rounds N` (default 30).

**Interactive mode** (`probe_workflow(interactive=True)`, or `evals/interactive_workflow.py`):
by default a mid-run `ask_user_question` pause is auto-answered with a canned "use your best
judgment" reply so unattended runs don't deadlock — but that throws away real clarifying
questions. `interactive=True` stops and returns instead, surfacing the actual question content
(`pending_approval_prompt`/`_title`/`_questions`) plus `pending_approval_token`/`session_id`, so a
real caller can answer it and resume with `resume_token`/`resume_answer` on the same `conv_id`.
Plan-approval pauses (the initial "here's the plan, proceed?") still auto-advance either way.

## Judge calibration (`--judge-calibration`, issue #312)

`PASS_THRESHOLD` (6.0 on a 0–10 scale, `evals/judges.py`) and every rubric prompt were picked without ever being
checked against human judgment — judges have known length/position/self-preference biases and are
non-deterministic. `evals/judge_calibration.py` re-runs `evals/golden/judge_golden.jsonl` (hand-
labeled real transcripts + a few deliberately-bad constructed cases per rubric — real runs are almost
all clean passes today, so a golden set needs synthetic failures too to be a meaningful check) through
the **same** judge functions production uses, and reports agreement against the human label:

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.runner --judge-calibration --judge-model "qwen/qwen3.5-9b"
```

Cost: one judge call per golden case (18 cases today) — same per-case cost as `--judge` on a normal
suite. Report → `evals/runs/judge_calibration_latest.md`: overall %, per-rubric %, and every
disagreement shown with both the human's rationale and the judge's own reasons side by side, so it's
fast to tell "the judge is wrong" from "the human label needs revisiting."

**This is a diagnostic/measurement lane, not a regression gate** — exit code is only 1 if the run
produced nothing scoreable at all (e.g. no judge reachable). "Agreement is currently 70%, rubric X
needs tuning" is a valid, useful finding per #312, not a build failure.

**Baseline (2026-07-04, qwen/qwen3.5-9b): 83% (15/18)** — see `docs/evals/PROGRESS.md` for the full
breakdown and the two real rubric-prompt findings it surfaced (`planner_quality` over-penalizes
legitimate non-delegate tools; `tool_appropriateness` doesn't know `delegate_to_*` handoffs are
always-bound infra, not a selection choice).

**Adding a golden case:** append one line to `judge_golden.jsonl` — `{"id", "rubric", "human_label":
{"pass": bool, "score": 0..10 or null}, "artifacts": {...kwargs matching that rubric's function
signature in judges.py}, "source": "real"|"constructed", "why": "your rationale"}`. Re-run calibration
after: changing a rubric's prompt in `judges.py`, switching the default `--judge-model`, or moving
`PASS_THRESHOLD` — any of those can silently move the effective bar with zero visibility otherwise.

## Mining real-usage cases (refs #305/#314)

All 14+ hand-authored datasets under `evals/datasets/` are synthetic prompts. `evals/tools/
mine_prod_cases.py` complements them with **real** historical usage, mined from `tool_outcome_log`
(the OATS table every tool call already writes to — `turn_id` groups every tool call within one
turn, `query_text` is the raw user prompt for that turn). This is a **different** capability from the
OATS `--replay` lane (embedding-refinement ranking quality) — don't confuse the two.

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tools.mine_prod_cases --sample-n 30
```

**Privacy design (this reads real personal prompts — see `docs/guides/PRIVATE_DATA.md`):**
- Writes ONLY to the gitignored `evals/datasets/_staging/prod_mined.jsonl` — never directly into a
  suite `runner.py` loads or any tracked file.
- Drops (does not attempt to redact-and-keep) any turn whose raw prompt matches an email or
  phone-number pattern — structured PII is usually accompanied by adjacent free-text context
  (names, addresses) a regex can't safely scrub, so those turns are excluded from the candidate
  pool entirely rather than trusted to a substitution.
- Applies a lighter redaction pass (long digit runs → `[number]`) to the turns that clear the above
  filter.
- **Never prints prompt text to stdout** — only aggregate counts (tool frequency, drop counts) —
  so running the script cannot itself leak personal content into a terminal or session transcript.

**Mandatory human review before promotion:** a candidate in the staging file is not "correct" merely
because a real turn executed those tools — the historical turn could itself have been a selection
bug, which would otherwise get enshrined as an enforced regression expectation. Before hand-copying
a candidate into a committed dataset (e.g. `evals/datasets/prod_sampled.jsonl`):
1. Open `evals/datasets/_staging/prod_mined.jsonl` yourself and read each candidate.
2. Confirm the tools listed were actually the *right* tools for that prompt (drop/edit if not).
3. Further genericize any remaining personal specifics beyond the script's automatic redaction
   (swap real names/entities for the codebase's existing pseudonym conventions — e.g. `Sam` — the
   same way every other dataset already does).
4. Only then append the (now-generic) case to the committed suite and run it once to confirm it
   passes against the current harness.

Repeat this mining pass periodically (e.g. quarterly) as real usage patterns drift, rather than
treating it as a one-off.

## Output (`evals/runs/`)

Every run writes four things (all gitignored — transient):
- `latest.md` — human/agent report: failures + fixes + pass-rate delta vs last run. **Read this first.**
- `triage.md` — failures **clustered by root cause**, each naming the likely harness area + files to check.
  Read this when you have failures and want the fix target, not just the symptom.
- `transcripts/run_<ts>/<case_id>.md` — the **full trajectory** per case (system prompt / bound tools for
  dry-run; plan / executed tools / reasoning / tool results / response for live & workflow). 2026 practice:
  read transcripts to verify whether the harness misbehaved or the assertion/judge was wrong.
- `run_<ts>.json` — machine record (per-case metrics, judge scores) for history/trend.

**Triage workflow:** run the suite → read `triage.md` → open the top cluster's transcripts → decide
*real bug* vs *threshold to tune* → fix. See `evals/triage.py` and the triage section below.
Clustering logic is in `evals/triage.py` (deterministic, no model call).

**Multi-run (`--repeat N`):** each case runs N times. The report adds a `Repeat:` line
(`pass@k X/total | pass^k Y/total | FLAKY: …`), failing cases get a `STABILITY:` line
(`m/N runs passed … FLAKY` vs `consistently failing`), and numeric metrics show `mean±stderr`.
A case passes only if **all N** runs pass (pass^k) — so a flaky case (passes sometimes) is a
failure you should investigate, not ignore. Use this to tell a real regression from run-to-run noise.
`duration_s` and `total_tokens` are both in `core._NUMERIC_METRICS`, so `--repeat N` on a `--live`/
`--workflow` suite surfaces latency and cost variance (`mean±stderr`) alongside pass-rate — a case whose
`duration_s` mean creeps up run-over-run is a regression signal even before it trips `max_duration_s`.

## Roadmap

- ✅ **Phase 0 — free dry-run lane**: deterministic assertions on the intercepted payload.
- ✅ **Phase 1 — live lane** (`--live`): real server turn → executed tools, plan, response;
  enables `planner_quality` / `response_grounded` + live assertions. (`evals/live.py`)
- ✅ **Phase 2a — judge**: lane-agnostic LLM-as-judge, pinnable to any small loaded model.
- ✅ **Phase 2b — workflow lane** (`--workflow`): full plan→approve→execute loop; `must_complete`/
  `max_rounds` assertions + the `workflow_outcome` judge. (`awaiting_approval` on `TurnCompleteEvent`.)
- ✅ **Phase 4a — transcripts + triage**: per-case trajectories + clustered `triage.md` every run;
  `evals/triage.py` + the `harness-triage` skill.
- ✅ **Phase 4b — multi-run** (`--repeat N`): pass@k / pass^k + mean±stderr; flaky (non-deterministic)
  cases flagged. Case gates on pass^k. (`core.aggregate_runs`)
- ✅ **Phase 3 — prompt audit** (`--prompt-audit`): audit every scope's assembled system prompt for
  placeholder leaks, duplicate/near-duplicate fragments, and bloat (free); `--judge` adds a per-scope
  contradiction rubric. (`evals/prompt_audit.py`)
- ✅ **Phase 4c — model comparison** (`--models a,b,c`): head-to-head via hot-swap of already-loaded
  models; fixed external judge to avoid self-enhancement bias. (`evals/compare.py`)
- ✅ **Latency/cost assertions** (`max_duration_s`, `max_total_tokens` on `--live`/`--workflow`): fail a
  case on a turn-latency or token-cost regression, not just wrong tools/plans. (`core.check_live_assertions`)
- ✅ **OATS replay** (`--replay`): offline K-fold holdout measuring whether outcome-aware embedding
  refinement actually improves tool selection. Free — no server, no model tokens. (`evals/replay_lane.py`)
- ✅ **Tool contract gate** (`--tool-contract`): every native tool needs a display formatter + a
  `tool-ui.tsx` entry; ratchets against `evals/datasets/tool_contract_baseline.json`, fails only on
  NEW drift. (`evals/tool_contract.py`)
- ✅ **Memory-layer fixtures** (`--memory-layer`, issue #618): seed Drive/filesystem/ActivityRAG
  rows into a temp sqlite DB and assert `must_include` / `must_not` on real retrieval clients
  (no chat LLM). Fixtures live in `evals/fixtures/` so `--all` ignores them.
- ✅ **Phase 5 — CI (free lanes)** (`.github/workflows/evals.yml`): `tool_selection`, `--prompt-audit`,
  `--tool-contract`, `--replay` gate every PR/push to master/dev — zero LLM tokens, no server/GPU.
  Judge/live/workflow lanes stay out of CI (need a running Brain + LM Studio) — issue #306.
- ✅ **Dataset schema validation** (`load_suite`): unknown `expect`/`judge` keys raise at load time
  instead of silently no-opping (issue #309).
- ✅ **Judge calibration** (`--judge-calibration`, issue #312): judge-vs-human agreement on a
  hand-labeled golden set — see the dedicated section above.
- ✅ **Safety/negative-case datasets** (`evals/datasets/safety.jsonl`, issue #316): over-triggering
  guard, destructive-action approval, ambiguous-request clarification — see the dedicated section above.
- ✅ **Dataset sizing** (issue #315) + **production-trace mining** (`evals/tools/mine_prod_cases.py`,
  issue #314): grown/real-usage-derived datasets so `--repeat N` pass@k/pass^k is meaningful signal,
  not noise from N=2-4 hand-authored cases — see the dedicated sections above.
- **Phase 4b+ — scoreboard** (`evals/report.py`): trend over last N runs with significance vs stderr.
- **Nightly judged full run**: `--all --judge` on a schedule, independent of the free CI gate above.

See `docs/evals/PROGRESS.md` for the full roadmap + the model-comparison design, and the audit in
memory (`harness-eval-system`).
