# Harness lessons from eval loops

Durable log of **what the harness got wrong** (and what we fixed) so we do not
re-learn the same mistake. Append when an eval loop surfaces a class of failure
that is **infrastructure / prompt / tool wiring**, not “the model had a bad day.”

Related:

- Agent runbook: [`AGENTS.md`](./AGENTS.md)
- Schema/rubrics: [`REFERENCE.md`](./REFERENCE.md)
- Chad-on-Chad fix → retest: [`docs/architecture/CHAD_SELF_EVAL_LOOP.md`](../docs/architecture/CHAD_SELF_EVAL_LOOP.md) (issue **#358**)
- Diagnosis skill: [`src/skills/eval_diagnosis/SKILL.md`](../src/skills/eval_diagnosis/SKILL.md)
- HITL quality design: [`docs/evals/QUALITY_HITL.md`](../docs/evals/QUALITY_HITL.md)

## Division of labor (do not blur)

| Layer | Job | Not its job |
|---|---|---|
| **LLM-as-judge** (`evals/judges.py`) | Score (0–10), list inefficiencies, **classify** failure modes from the trajectory | Invent harness patches (“prompt the agent better”), edit code, re-run Brain |
| **Chad-on-Chad / `eval_diagnosis`** | Root-cause → generic fix proposal → Chad Code edits → re-eval → before/after verdict | Soft quality scoring of every live case (that’s the judge) |
| **Deterministic asserts** | Catch classes that should never need a judge (wrong `stop_reason`, missing tool, title-like `file_id`) | Open-ended quality |

Free-form judge “suggestions” tend to collapse to prompt fluff. Prefer a **failure
taxonomy** in the verdict; route classified failures into the Chad-on-Chad loop
(**#358** — full observe→fix→retest not closed yet; complete when that epic is
worked).

## Lessons (newest first)

### 2026-10-18 — LFM 2.5 Supervisor Transition & Dual-Case Nudge (#722)

Swapped Bonsai 27B for `lfm2.5-24b-a2b` as the primary supervisor model and evaluated on end-to-end planning-to-execution workflows.

**Wins & Architecture Hardening:**
- **Centralized Supervisor Configuration**: Consolidated model selection into `supervisor` across `config/config.yaml`, `preferences.yaml`, and `src/shared/config_manager.py`. Telegram and Reflection dynamically inherit without scattered hardcoded IDs.
- **Model Registry & Thinking Control**: Added `lfm2.5-24b-a2b` to `config/model_registry.py` (`thinking_param_style="qwen3"`, `thinking_budget=8192`) and `config/model_capabilities.yaml` (`context_window: 32000`).
- **Dual-Case Continue Nudge**: Refactored the continue-nudge in `evals/live.py` to identify the active open step and prompt both outcomes: continue work if steps remain, or emit `complete_step` if the deliverable is finished.
- **Mandatory `complete_step` Contract**: Hardened supervisor and planner doctrines (`config/persona/supervisor_routing.md` and `config/persona/planner.md`) forbidding finishing turns with prose alone while steps remain open, and instructing supervisors not to draft raw HTML/CSS inside thinking tokens.
- **Benchmark Pass**: Verified clean 100% PASS on `matthew_wf_wedding_roadmap_timeline` (3 rounds, 43 tools, 2/2 steps complete).

**Critical Traps Uncovered & Fixed:**
1. **Unregistered Model Thinking Disable Bypass**:
   - *Trap*: When `ReasoningRunawayGuard` tripped on LFM 2.5 during large document reads and retried with `disable_thinking=True`, the absence of a `ModelProfile` in `model_registry.py` caused the adapter to skip template parameter injection (`chat_template_kwargs.enable_thinking=False`). The retry restarted thinking without guard protection, bloating to 9k+ tokens.
   - *Fix*: Registered `lfm2.5-24b-a2b` in `config/model_registry.py` and added explicit routing rules directing the model to delegate HTML/CSS artifact generation immediately to `delegate_to_code_agent`.


First multi-hour autonomous sweep testing 10 personalized, grounded end-to-end planning-to-execution prompts on Dev Brain (`:8788`).

**Wins:**
- **Zero-Nudge Full Autonomy**: Every executed case achieved `completed=True` with **0 continue nudges**, 0 human inputs, and **0 clarifying questions** (strict question discipline followed).
- **Quality Benchmarks (Gemma 26B Local Judge)**:
  - Wedding Timeline: **9.0 / 10.0**
  - Canvas Coursework Matrix: **10.0 / 10.0** Planner Quality, **9.0 / 10.0** Workflow Outcome
  - Life Dashboard: **10.0 / 10.0** Planner Quality, **9.0 / 10.0** Workflow Outcome
  - Bogleheads / Financial Brief: **10.0 / 10.0** Workflow Outcome (**PERFECT PASS**, 94 tools, 6 parallel research streams)
- **High-Fidelity Artifacts**: Successfully generated live HTML/CSS dashboards and rendered charts directly from Google Drive, Canvas LMS, and User Memory.

**Critical Harness Traps Uncovered & Fixed (DO NOT REGRESS):**
1. **Tool Repeat Assertion Parameter Agnosticism (`max_tool_repeats > 3`)**:
   - *Trap*: The harness previously checked raw tool names (`Counter(trace.tool_names)`). If an agent legitimately iterated across 5 distinct Canvas courses, or fetched 8 different assignments, or searched 40 distinct queries in deep research, it failed as a "runaway repeat-call loop".
   - *Fix*: [`evals/core.py`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/evals/core.py) now hashes the **full call signature** (`name` + sorted JSON `params`). Only identical calls with the exact same arguments trip the runaway loop detector.
2. **Watchdog Silent-Turn Premature Kill**:
   - *Trap*: `evals/runner.py` buffered stdout until turn completion. Heavy local code generation (e.g. `qwen/qwen3-coder-30b` writing 450 lines of HTML) left `runner.log` unchanged for 25 minutes, triggering false-alarm watchdog termination.
   - *Fix*: [`evals/live.py`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/evals/live.py) streams tool execution events (`-> tool: <name>`) in real time with `flush=True`, and the watchdog timeout in [`evals/run_matthew_planning_3h.ps1`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/evals/run_matthew_planning_3h.ps1) was expanded to 45 minutes.
3. **Judge Empty Output & Unescaped Quotes**:
   - *Trap*: Local judges (Gemma 26B) occasionally emitted markdown quotes inside tool JSON, or returned an empty response on complex rubrics, causing JSON decode failures and immediate `needs_rejudge` voiding.
   - *Fix*: [`evals/judges.py`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/evals/judges.py) now parses string arguments with JSON repair routines and wraps the judge query in an automatic **3-attempt retry loop** before reporting that the judge could not evaluate.
4. **Bonsai Unconstrained Reasoning Loop**:
   - *Trap*: `prism-ml/bonsai-27b` burned 13 minutes and 25k tokens reasoning without outputting a tool call because `planner_model` had no token ceiling and `ReasoningRunawayGuard` only checked `reasoning_chunk`.
   - *Fix*: Explicitly set `max_tokens=4096` in [`src/core/orchestration/nodes/planner_node.py`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/src/core/orchestration/nodes/planner_node.py) and updated `ReasoningRunawayGuard` to observe `content_chunk`.
5. **Streaming Idle Timeout False Kill**:
   - *Trap*: Long local code generation runs exceeded LangGraph's 600s idle timeout if no intermediate events were yielded.
   - *Fix*: Added `emit_node_progress()` heartbeats every 20 streaming chunks in [`src/core/intelligence/adapters/litellm_adapter.py`](file:///c:/Users/ExampleUser/Documents/Coding/Fun/AIAssistant-dev/src/core/intelligence/adapters/litellm_adapter.py).

### 2026-08-01 — Long in-process lanes died mid-run (#675)

Two independent causes, both harness, neither "the model":

**1. Nobody ever tore the MCP fleet down.** `MCPServerEntry.disconnect()` existed but had
**zero callers** anywhere in the codebase — servers were spawned at warmup and abandoned,
on clean exits as much as crashes. Each run leaked ~12 node processes; consecutive runs
stacked fleets (42 stray node + 8 stray python were measured), so attempts got
progressively less likely to finish. Fixed with two layers:

- `MCPRegistry.disconnect_all()` + `evals.inprocess.shutdown_inprocess_session()`, called
  from the lane's `finally` — the graceful path.
- `src/shared/child_reaper.py`, a Windows **kill-on-job-close job object**. Children in the
  job are killed by the kernel when the process exits *however* it exits. `atexit` and
  `finally` do not run on a hard death, which is the case that produced the orphans, so
  this is the layer that actually matters. Verified with a real child + `os._exit`, with a
  negative control (an unregistered child must survive, or the test proves nothing).

**2. Per-turn 26B↔9B swap thrash.** Measured on this lane: `tool_9b_ms=41130` on the first
case, and a **22s** supervisor re-preload on the next — the 9B selector doesn't fit
alongside the 26B supervisor inside the 16GB budget, so every turn evicts the supervisor
and pays to reload it. Mitigation: **`CHAD_PINNED_TOOLS`** (comma-separated, empty string =
core tools only) makes the selector return a fixed menu and never call the 9B. Only sound
for a lane that isn't measuring tool selection and whose tools are known up front. Never
set in production.

> Reflex to keep: when a long local-model run dies with no traceback, look at process
> counts and VRAM choreography **before** concluding anything about the model.

### 2026-07-26 — Browser named-SaaS cluster (`browser_named_saas`)

Full writeup: [`docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md`](../docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md).
Catalog (FIXED + OPEN): [`docs/evals/FOUND_AND_FIXED.md`](../docs/evals/FOUND_AND_FIXED.md).

**Landed**

- Named product → browser gate (Outlook ≠ Gmail; private ChatGPT chat ≠ `web_search` doctrine).
- Knot list-preview → conversation detail via `page_surface` (PASS on `knot_vendor_terse`).
- Eval WS connect preload is `agent`/`plan`, never GUI `auto` (stops gemma-1b intent-router JIT).
- Persistent suite: `--suite browser_named_saas` (Outlook + ChatGPT cases marked OPEN in `note`).

**Still open (re-run the suite after fixes)**

| Class | Case | Prefer fix in |
|---|---|---|
| Login wall loops instead of `LOGIN REQUIRED` | `outlook_angela_baker` | `page_surface` MS URL heuristics + harness TERMINAL nudge |
| “what is…” still picks `web_search` | `chatgpt_prompt4` | Stronger private-chat ≠ public-fact routing |
| Soft chronology / speaker attribution | Knot residual | Verify-before-done / speaker cues (not site coaching) |

**Ops:** Dev Brain `:8788` only; never kill `:8767`. Restart Brain after routing/preload code lands.

### 2026-07-25 — Wedding / Golfport HITL (`wedding_dinner_plan`)

**Wins (harness / judge infra landed this loop)**

- Full-fidelity trajectory capture via shared modules (`agent_scope`, `tool_lifecycle`,
  `pack_judge_evidence`) — judges finally see tool bodies, not just headers.
- `propose_plan` + `submit_verdict` tool-native (flat schemas) so local Gemma can emit
  plans/verdicts without JSON-in-content / `$defs` 400s.
- Judge timeouts raised (default **1200s**); `context_too_large` only when est tokens
  exceed `num_ctx` (dropped the false 24k-char shortcut).
- Quality scores **0–10** with inefficiency docking (listed waste cannot stay a perfect 10);
  default judge temp **0.8** so open-ended waste is not locked into a green path at temp 0.

**Open harness failure classes (for #358 / deterministic guards — do not “fix” only by
asking the judge to suggest prompts)**

| Class | Evidence | Prefer fix in |
|---|---|---|
| `rediscovery_after_brief` / double Scout | Second `scout_gather` after brief already had facts/IDs (golfport 3/10) | Scout session cache, planner doctrine, orchestration nudge |
| `title_as_file_id` | `google_drive_read_file` with title-like ids → repeated failures | Tool arg guard / Drive tool validation |
| `duplicate_same_id_reads` | Same `file_id` searched/read multiple times (wedding 9/10) | Same-limb read cache |
| `plan_repeats_gather` | Plan steps re-list Search/Read after Scout already gathered | Planner persona: plan = build/ask, not re-gather |
| Judge soft-pass on waste | Temp-0 judges scored 10/10 while waste was visible | Keep temp + docking; classify, don’t rely on suggestions |

**Do not** treat a green `response_grounded` alone as “efficient” — score efficiency /
rediscovery on `planner_quality` (or a dedicated dimension) with the full trajectory.

### Older pointers

- Plan-approval stub ≠ final response — judge the **plan card**, not the canned chat stub
  (`response_grounded` plan-pause path).
- Incomplete capture (tool results as `{chars}` only) made judges blind — never ship
  quality suites without full event payloads again.
- Worktree Brain `:8788` for edit→re-eval loops; never treat prod `:8767` as the test target
  for uncommitted harness work (see eval-harness skill).

### 2026-10-18 — LFM native calls and false coding success

- LFM2.5 through LM Studio returned real `tool_calls`; the Qwen-derived text-mode
  setting instead produced mixed call syntax and a final unexecuted request.
  Use exact-model native-mode overrides rather than a universal local-model flag.
- `Conversation.run()` returning was not proof of completion. Inspect SDK status
  and the last agent event before emitting the success marker. Do not replay an
  incomplete task through MCP fallback; earlier actions may already have run.
- A finished job with files is not a passing coding eval: fixbugs seeds its files
  before the worker starts. Capture protected fixtures, check a source delta, and
  grade a fresh copy against authoritative tests. Keep no-op, tampering, runtime
  errors and timeouts distinct from a verified fix.
- Observed live: native LFM repaired a chunker and passed six independent tests.
  A larger harness-repair attempt was stopped after repeated read-only exploration
  with no edit. Tool compatibility and long-task effectiveness are separate claims.

### 2026-10-19 — Structured triage control and ignored persona evidence

- Do not equate a requested reasoning flag with effective control. On the installed
  LM Studio OpenAI-compatible endpoint, `reasoning_effort=none` works; `off` is
  rejected, while native API model metadata uses off/on. Verify final content,
  finish reason, reported reasoning usage and exact request settings.
- `response_format=json_schema` plus `none` completed the saved Gemma triage in
  6.781s with zero reasoning tokens. A conflicting-prompt/schema-only random-marker
  control also passed. Evidence validity returned investigate, so no repair followed.
- Git diff cannot retain ignored persona-only repairs. Explicitly select fragment
  IDs, snapshot original bytes and effective scope/body hashes, verify the clone
  matches, and capture ignored overlay deltas independently before cleanup. Hash
  other overlays without copying their text into reports; reject unapproved drift.
- Disk snapshots do not attest a live PromptRegistry cache. Full behavioral A/B
  still requires a freshly warmed dev Brain on 8788 with matched inputs/settings.

### 2026-10-19 — Identification-first proof and review evidence completeness

- A correct filename list can still violate an exact-output contract through fences,
  prose or unrelated personal context. Preserve the raw response locally and grade
  independently; do not normalize wrappers away to hide the failure.
- New full-Brain listing trials produced one format failure and one pass, with no
  forbidden recorded network attempts and unchanged synthetic fixtures. Keep the
  historical network-scope finding open without claiming it recurred or was fixed.
- Chad's prompted self-review correctly identified the formatting error, but also
  questioned filename verification when the review prompt omitted captured tool
  results/fixture evidence. Correct that harness input gap; do not blame the model
  for evidence it did not receive. The enriched prompt still needs a model replay.
- Schema/evidence-reference validity is not semantic approval of hypothesis prose.
  Detection and a useful cloud-model handoff are valuable outcomes without a fix.
  Keep self-repair optional; preserve contradictory or weak analysis as evidence.

### 2026-10-19 — Neutral discovery controls expose reviewer failures

- A "diagnose this failure" prompt cannot fairly test false positives on correct
  traces. Permit no_failure and inconclusive, with empty findings/hypotheses.
- The held-out hidden-file run returned valid JSON but omitted the explicitly
  requested dotfile. Captured include_hidden=null omitted it; a direct read-only
  control with true returned it. Keep this answer-value bug separate from formatting.
- The batch reviewer moved a forbidden network event into the wrong case and
  missed the actual network case. Isolated formatting review still falsely called
  fenced JSON compliant; isolated network review found the call but broke schema.
  Do not assume isolation alone establishes review reliability.
- Recopying response text invites substituting the expected answer for actual
  evidence. Prefer a captured-field predicate for valid-JSON mismatches. The targeted
  check still returned duplicate/contradictory verdicts; retain and reject it rather
  than repair the output into a pass. Recovered tool errors do not imply an
  incomplete overall turn.
- Strict listing grading must reject duplicate JSON keys; ordinary json.loads can
  silently select the last value and hide an earlier wrong answer. Original live
  captures and later deterministic adjudication belong in separate artifacts.

### 2026-10-19 — Preserve timed-out rounds and isolate evidence review

- Reuse live.py's idle detector, workflow nudges and disconnect cancellation. A hard
  240s round cutoff defeated existing progress handling. Timeout must merge the
  current round before exiting; otherwise an active run becomes an empty trace.
- Only attributable task progress extends idle time; heartbeats and wrong-chat events
  must not. The legacy untagged-event acceptance rule remains a stated limitation.
- Pin the evaluator through the judge adapter, not probe_live's supervisor route.
  Empty tools plus no dispatcher enforce analysis-only behavior independently of
  instruction adherence. Confirm execution cancellation before evaluator model load.
- A soft result-text cap does not bound a serialized evidence bundle. Preserve all
  call and result-event indices with excerpt limits and preflight token budgeting;
  record omissions. Keep raw captures locally. Do not repeat external actions to fix
  an evidence-packaging issue.
- The Qwen real-task review completed but labelled event positions as tool-call
  indices. Preserve that error as an evaluator finding, not a harness-corrected pass.

### 2026-10-19 — Architecture context is necessary but not reviewer acceptance

- Supply retrieval injection, tool-selection/menu, specialist and workflow semantics
  to evidence reviewers. Preserve actual per-case context and delegation objectives;
  architecture descriptions alone do not attest a run's effective inputs.
- A tool-free enriched Qwen review passed all canonical references but blended calls
  across before/after runs, invented capability API names, and under-analyzed the
  supplied retrieval evidence. Keep semantic adjudication separate from citation
  checks; more input context alone is not a demonstrated diagnosis fix.
- Pair prompt snapshots with the actual capture window and retain correlation limits.
  The earlier 0.42 snapshot differed from the runtime-audit prompt in Calendar lines;
  preserve the original review and separately package the corrected evidence.

### 2026-10-19 — Interactive resume needs a follow-up turn

- `tool_approval_response` only resolves the server approval store. It does not
  resume model generation. The eval `resume_token` path omitted the subsequent
  `user_input`, unlike both the normal Limb and the noninteractive auto-answer path.
  This produced idle capture without a new model turn, not an acting-model failure.
- Send the supplied answer in a same-conversation `user_input` immediately after
  the decision, with the original category and eval platform. The regression checks
  both messages and their ordering; live dev logs confirm a new clarified turn.
- Record effective specialist pins, not only the supervisor name: Bonsai supervisor
  still delegated browser execution to an explicitly pinned LFM specialist in this
  environment. Do not label its browser errors as Bonsai's direct tool behavior.

### 2026-10-19 — Small-model extraction and concurrency are separate gates

- Qwen2.5-3B returned structured JSON yet confused product/instance/artifact types,
  split a known product alias and invented dependency spans. Validate request spans
  and semantics independently; schema success does not authorize routing or search.
- Two development variants passed 0/16 full extraction+dependency contracts and
  4/16 simplified extraction-only contracts. Keep original failures, report stricter
  uncertainty/exact-span checks explicitly, and do not call this held-out accuracy.
- One loaded model with four prediction slots handled concurrent extraction and
  selection workload faster than serial pairs. This endpoint measurement bypassed
  gate routing and simplified selector inputs; it is not full-pipeline latency or
  a tool-selection quality measurement. Preserve the knowledge/selection dependency.
- Resolve people from supplied local facts with provenance/conflict handling after
  extraction. Do not send contact facts to a public search or let a model fabricate
  addresses. Correct local resolution does not prove the acting model uses it.

### 2026-10-19 — Reference facts in the user envelope change retrieval too

- A dev first-decision grounding pilot held the system prompt/model fixed, but
  appended source facts to the submitted request before normal retrieval. That
  changed graph/other hits and dropped the known recipient email from the grounded
  input. It was not an isolated test of supervisor knowledge.
- Keep original-task retrieval queries independent of appended public grounding.
  Preserve sourced identity facts separately. Audit actual outbound prompts, not
  just the reference block submitted by the eval client.
- Supplied facts appeared in initial reasoning but did not yield stable action;
  a later reasoning-guard error is not a task pass or proof of a context-length
  failure. Preserve the failed sample and its confound before attempting repairs.

### 2026-10-19 — Browser success can disappear at representation and handoff boundaries

- Refined GUI trace: a ready dialog, code and live-call controls established a
  successful meeting, but specialist prose reclassified it as pre-existing.
  Parent-only summaries are insufficient evidence of what inner tools accomplished.
- MCP registry returns dictionaries while snapshot hydration/pruning expects object
  `.text`. Test the actual producer/consumer shape; file links are not inline trees.
- Successful screenshot capture can become clipped base64 text in model input.
  Verify final multimodal payload shape separately from tool success.
- Preserve terminal domain budgets and partial artifacts across handoff. A prose
  failure can evade a machine failure-signature parser and appear successful.
- Validate planner statuses at entry: `in_progress` survives normalization but is
  ignored by consumers expecting `running`, silently skipping an unfinished step.
- Initial report: `docs/evals/CHAD_REFINED_GUI_TRACE_TRIAGE.md`. Subsequent repairs
  and offline verification: `docs/evals/CHAD_GUI_HARNESS_REPAIRS.md` (111 tests).
  Screenshot containment is an explicit text-channel fallback, not native vision.

### 2026-10-19 — Approval identity and pause lifetime are separate contracts

- A successful tool can inherit an old checkpoint's pending approval even when the
  current ContextVar is reset. Clear transient pause state at turn/tool/specialist
  boundaries and let only a newly raised approval replace it.
- Persist the approval's tool name and preview arguments. Never infer an approval's
  identity from an unrelated last tool event; question fallback must also respect
  identity. Test the persisted action shape and real rendered component together.
- Short execution continuations should inherit their own checkpoint's execution
  mode before prompt preparation, without capturing initial planner clarification
  or substantive requests for new plans.
- Model construction and residency must share one pin policy. Browser inherits the
  supervisor; ignoring a stale pin only in graph construction still leaves a model
  swap mismatch. See `docs/evals/CHAD_BONSAI_APPROVAL_REPAIRS.md`.

### 2026-10-19 — UI success needs target identity and matched postconditions

- An input operation can succeed on the wrong control. Preserve returned targets,
  enabled state and tool-call IDs in UI handoffs; do not equate click success with
  requested task completion or repeat mutations just to gain confidence.
- Screenshot annotations, element IDs, automation IDs and window handles differ.
  Check current schemas: this UI server suggests elementId in results but its
  click/type/read schemas accept semantic selectors instead. Local overlays can
  also hide updated baseline guidance (an obsolete three-call cap did so here).
- Fresh desktop observations must not hit identical-success reuse blockers after
  state changes. Keep mutation protections and independent runtime budgets.
- Approval previews are serialized control data: leaking a runtime registry object
  changes a JSON result into Python repr and can silently bypass pause extraction.
  Test the real executor-to-compiled-graph boundary with runtime kwargs.
- A launcher instance name does not imply `<name>.exe`. Negative guessed-process
  searches cannot invalidate a UI report; verify relevant observed app state.
- Repair/evidence report: `docs/evals/CHAD_SUCCESS_UI_TRACE_REPAIRS.md`; 98 offline
  tests pass. User-observed success and independently captured postconditions remain
  separate; no external task replay or production restart was performed.


### 2026-10-19 — Scout visibility and specialist budgets

Scout root events must explicitly clear inherited parent context. A planner dispatch
placeholder must not complete the real Scout lifecycle early. Preserve one call ID
and carry the final report through the stream to the panel. Increased tool budgets
also require sufficient child-graph recursion. See
`docs/evals/CHAD_SCOUT_UI_AND_BUDGET_REPAIRS.md` for fixes and validation.


### Scout input isolation follow-up

See `docs/evals/CHAD_SCOUT_CONTEXT_ISOLATION.md`: Scout now starts with an independent
core search/read tool set and Scout-only persona, brief and original request; no
composite supervisor context. Discovery filtering and memory mutation guards were
repaired. Thirty focused tests pass; no live replay or manual Brain restart.


### Required planner Scout decision

See `docs/evals/CHAD_PLANNER_SCOUT_DECISION.md`. Tool binding and execution now
require a nonempty dispatch or explicit skip before planning; early proposals,
mixed batches and degraded fallback paths cannot bypass the decision. Keep local
planner overlays aligned with baseline policy. No live replay or manual restart.


### Scout evidence survives planning and approval

`docs/evals/CHAD_SCOUT_EVIDENCE_HANDOFF.md` records checkpointed findings, mandatory
coverage review, one targeted follow-up, supervisor/delegate evidence injection and
read-only operation classification. Checkpoints must preserve evidence across
approval; cached child system prompts still need current facts on each task message.
Tool-call provenance is checked, but semantic correctness remains a model/eval concern.
No live task replay or manual Brain restart.


### Scout report phase must preserve its report tool

A user-role fallback saying "No more tool calls" with zero bound tools conflicted
with finish_scout_report and made Scout attribute a harness restriction to the user.
Use explicit gather/report phases, a real gather task message, one enabled-tool
retry and one bounded report-only attempt. See the report-phase correction in
`docs/evals/CHAD_SCOUT_EVIDENCE_HANDOFF.md`.


### check_app_exists restored as read-only

The protocol/partition declaration survived but the callable tool and daemon handler
were missing. Restored both using a shared non-launching app resolver, with explicit
shortcut/alias limits and Scout discovery visibility. 44 focused tests pass; a local
Prism lookup verified its executable without launching. No services restarted.
See `docs/evals/CHAD_SCOUT_EVIDENCE_HANDOFF.md`.


### Scout discovery/report contract — live verified

See `docs/evals/CHAD_SCOUT_LIVE_CONTRACT_FIX.md`: explicit outgoing report schema,
source IDs in results, actionable validation, bounded report retries, exact tool
discovery/category recovery and refreshed loaded-tool context. 77 focused tests
pass; two real Bonsai planning runs on dev 8788 reached approval with zero report
rejections. Run 1 exercised app discovery and actual read-only Hands inspection;
run 2 returned matching personal memory and an official Meet entry link. These are
Scout/planning checks, not completed meeting/email/Prism actions. Production untouched.


### Direct tool approval continuation and visible resumed stream

Approval now replays the saved atomic call before model inference, bypassing routing,
selection and compaction. The pause checkpoints normally instead of cancelling the
stream mid-tool. Fixed desktop approval clearing stream ownership, plus stale final
responses closing the next turn's emitter. Real graph tests cover direct/specialist
calls, exact arguments, decline, receipts and sibling isolation; UI tests cover
visibility/session isolation. Production untouched; no live GUI rerun for this patch.
See `docs/evals/CHAD_DIRECT_TOOL_APPROVAL_RESUME.md`.
