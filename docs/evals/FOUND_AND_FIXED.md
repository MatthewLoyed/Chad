# Eval findings: found → fixed

### 2026-10-19 — UI verification loops and non-JSON approval previews

- Runtime `tool_registry` in preview params broke approval-result JSON, allowing
  generation to advance past a pending send. Previews now contain only JSON-safe
  user-facing arguments; a compiled real-executor fixture verifies the pause.
- Screenshots and desktop reads no longer hit identical-success/failure reuse
  blockers across mutable UI states. Window mutations remain guarded.
- Click success no longer claims task completion. UI handoffs preserve bounded
  actual-target receipts and current-delegation provenance alongside prose.
- Corrected UI selector/index guidance and obsolete overlay cap; verification must
  use real application postconditions, not guessed instance executable names.
- Details and private evidence map: `CHAD_SUCCESS_UI_TRACE_REPAIRS.md`.

### 2026-10-19 — Bonsai GUI approval identity, stale pause and browser inheritance

- Draft creation is ungated; sends remain gated. Approval tool identity and previews
  survive frontend persistence, so an email card cannot borrow open_app arguments.
- Successful tools/new invocations clear stale checkpointed pause signals. Short
  execution continuations inherit the prior execution lane before prompt preparation.
- Browser graph construction and residency ignore old browser pins and inherit the
  supervisor model. Tests cover switching the supplied supervisor instance.
- 107 backend tests and 11 frontend tests passed; TypeScript and frontend bundle
  build passed.
  See `CHAD_BONSAI_APPROVAL_REPAIRS.md` for trace boundaries and future prompt reduction.

### 2026-10-19 — Refined GUI trace harness boundary repairs

- **Symptoms:** snapshot file links reached the model without page text; successful
  screenshots became truncated base64; `in_progress` skipped an unfinished plan
  step; terminal browser exhaustion did not prevent another delegation.
- **Causes:** MCP dictionary/object mismatch, binary data serialized through a text
  channel, unchecked new-plan statuses, and child budget state reduced to prose.
- **Fixes:** normalize MCP text blocks, preserve generic text leaves, give explicit
  screenshot artifact/fallback notices, force new steps pending, forward domain
  budget state and label capped summaries partial. Keep local caps distinct from
  conversation browser exhaustion; avoid stale-result advice for snapshots/scrolls.
- **Verification:** 111 focused/adjacent tests and saved Meet-snapshot retention
  checks. No live task replay; production untouched. Native vision, draft-selection
  quality and private contact retrieval remain separate work.
- **Details:** `docs/evals/CHAD_GUI_HARNESS_REPAIRS.md`.

### 2026-10-19 — GUI model selection reverted to stale supervisor pin

- **Symptom:** selecting Bonsai saved `local_models.active_model`, while GUI polling
  and routing continued using LFM from the higher-priority `local_models.supervisor`.
- **Evidence:** dev preferences held Bonsai in active_model and LFM in supervisor;
  repeated GUI selections rebuilt a Bonsai adapter without changing the preferred pin.
- **Fix:** local-model facade persists both aliases in one preference write, updates
  memory and forwards both to Brain even when readiness returns a not-loaded warning.
  Brain normalizes older active_model-only clients to the supervisor alias too.
- **Validation:** six picker/router tests pass, covering stale-pin replacement,
  reload/polling resolution and unloaded-model updates. Applied the user's already
  saved Bonsai choice to dev 8788; ModelChanged log confirms Bonsai router reload.
  Production 8767 was not restarted. This is selection verification, not a task run.

### 2026-10-19 — Interactive eval resume resolved the answer but never started a turn

- **Symptom:** `probe_workflow(resume_token=...)` waited idle after an answered
  clarification; the inference gate had zero active/waiting requests.
- **Evidence:** private `chad-relevance-cutoff/bonsai-clarified-01/` and dev logs:
  approval resolved, no subsequent turn start. Recorded as `void_harness`.
- **Cause:** the helper sent only `tool_approval_response`; the server handler
  records that decision and requires a separate normal `user_input` to resume.
- **Fix:** send the supplied answer as same-conversation `user_input` immediately
  after resolving the token, retaining category/eval platform. Regression in
  `test_live_timeout_capture.py`; corrected Bonsai continuation starts a real turn.
- **Limit:** fixes eval transport, not model interpretation or task completion.

**Purpose:** Compact catalog of harness/orchestration bugs surfaced by evals (esp. live/workflow HITL).  
Future agents: **search this file first** when an eval shows thrash, VOID, path fights, or mysterious replans — many “new” failures are known patterns with known fixes.

**How to add an entry:** one block per bug. Keep it tight. Link commits/PRs when known. Prefer evidence (transcript path, log line) over narrative.

Template:

```
### <short title>
- **Symptom:** …
- **Found via:** suite/case + what to look at (transcript / Brain log / LangSmith)
- **Root cause:** …
- **Fix:** files + one-line behavior change
- **Effect:** what passes / what stops happening
- **Watch for:** related failure modes still open (optional)
```

---

## 2026-07-24 — wedding plan→build HITL

### Plan approval follow-up latched as `goal="proceed"`
- **Symptom:** After plan pause, rounds stuck `0/N → 0/N`; plan card / planner goal became `"proceed"`.
- **Found via:** `wedding_plan_build_smoke` / `din_plan_wedding_site` workflow; Brain log `Plan ready` again; transcript goal field.
- **Root cause:** Harness/GUI sent bare `proceed` (or short approval) as a new `HumanMessage`; `resolve_goal` / planner took the latest human text as `plan_goal`. Mid-plan `create_plan` then re-entered approval.
- **Fix:** `PlanService.is_plan_followup_message` + `resolve_goal` skip follow-ups; planner uses that; eval default approval text is explicit “execute the existing plan…”; `create_plan` description forbids call when a plan is already active; `route_after_planner` skips approval on `plan_replanned`.
- **Effect:** Approve → supervisor continues the real goal; no second approval pause from `proceed`.
- **Watch for:** Any new nudge string that isn’t listed in follow-up detectors.

### Junk replan wiped a good mid-plan (`['Any text']` → `0/1`)
- **Symptom:** Progress `2/4` then collapsed to `0/1`; final step description = continue-nudge text; LangSmith `planner: TypeError(string indices…)`.
- **Found via:** Same smoke run + `brain_proceedfix_*.err.log` (`Skipping non-dict plan step… 'Any text'` then TypeError at `plan_list[0]["id"]`).
- **Root cause:** Step auto-fail → scout→planner; LM Studio grammar 400; `json_repair` returned non-dict steps; `normalized_steps or plan_list` restored junk; fallback single-step used last HumanMessage (the continue nudge).
- **Fix:** `_normalize_plan_steps` drops non-dicts and never restores raw junk; on empty parse with an existing plan, **keep** `active_plan`; first-plan fallback uses `PlanService.resolve_goal`, not the nudge.
- **Effect:** Bad replan no longer replaces a healthy multi-step plan with a 1-step continue-nudge.
- **Watch for:** Structured-plan grammar failures on Gemma still common; recovery is keep-plan, not “fix LM Studio.”

### Eval prompt prescribed absolute `dinner_artifacts/…` paths
- **Symptom:** Model thrashed on `file_manager` / `propose_file_plan` / fake approval tokens; wrote to Downloads; never finished deliverable.
- **Found via:** Transcript tool_results (`SAFETY LOCK: Missing approval_token`); plan step_4 = create folder under worktree path.
- **Root cause:** Dinner JSONL prompts embedded absolute save paths + “create folders,” so the model became the harness. `propose_file_plan` is for batch organize, not “emit one HTML artifact.”
- **Fix:** Strip paths from wedding_* prompts (local/gitignored); ask for a “local HTML artifact.” Default `write_file` → cwd `artifacts/` (align with sandbox); clarify `propose_file_plan` description.
- **Effect:** Model can finish with `write_file` / `build_html_file` / code_agent; harness collects under `artifacts/`.
- **Watch for:** Other datasets that still bake absolute paths into prompts.

### Per-step “iteration timeout” at 6 turns → scout replan
- **Symptom:** Log `Step 'step_N' auto-failed: iteration timeout` then immediately `failure_context → scout for re-gather + re-plan` mid successful plan.
- **Found via:** Same smoke; `_turn_count >= 6` in `supervisor_node` (supervisor tool-turns on a step, **not** wall-clock).
- **Root cause:** Limit too low for SAFETY LOCK / wrong-tool retries; failure path always scout+replan, wiping progress; feedback was generic “Re-planning.”
- **Fix:** Soft nudge at 10, hard ceiling at 20; on stall/`fail_step`, stay on **same** step with actionable `failure_context` (recent tool errors + hints); route to **supervisor**, not scout; `fail_step` re-opens step as pending.
- **Effect:** Stall forces a different approach on the current step instead of nuking the plan.
- **Watch for:** Global `iteration_count >= 25` graph guard still ends the turn (separate from per-step stall).

### Empty / unparseable judge → false FAIL (earlier same day)
- **Symptom:** Judge returned empty; case failed as model quality when it was judge infra.
- **Found via:** Review queue / harness report.
- **Root cause:** Empty verdict treated as scored failure.
- **Fix:** Void as `needs_rejudge` with size diagnostics (`60fa97dc` era).
- **Effect:** Pass rate isn’t polluted by dead judges; re-judge path is clear.

### Plan-approval judge scored chat stub (earlier same day)
- **Symptom:** Plan pause judged on “awaiting approval” chat text instead of plan steps.
- **Found via:** HITL judge review.
- **Fix:** Judge plan-approval pauses on plan steps (`42293d82`).
- **Effect:** `planner_quality` reflects the plan card, not the stub reply.

---

## Session notes (2026-07-24 evening)

### Wins
- Proven end-to-end: approve → execute to 2/4 with code_agent (proceed-fix held).
- Catalog + defaults now match architecture (artifacts/, no path-in-prompt).
- Failure recovery no longer equals “throw away the plan.”

### Failures / still open
- LM Studio structured-plan grammar 400 on Gemma during mid-run replan.
- VRAM thrash: Gemma ↔ coder ↔ 9B when other agents run unit tests against LM Studio.
- Scout still can burn tools on broken MCP (e.g. GitHub) if something *does* route to scout.
- EventBus `no running event loop` on status broadcast (noisy, non-fatal).
- Wedding datasets remain gitignored (`wedding_*.jsonl`) — personal HITL; keep prompts path-free when editing locally.

### Operating lessons for agents
1. **Never hit prod `:8767` for uncommitted `src/`** — worktree Brain `:8788` + main `.env` via `CHAD_MAIN_ROOT`.
2. **Don’t co-schedule judge (Gemma) with a heavy Brain turn** — VRAM fight.
3. **Read transcript + Brain err log before “fixing the prompt.”** LangSmith/planner TypeErrors and `SAFETY LOCK` lines beat guesswork.
4. **Harness owns artifact paths; prompts own the deliverable.** Absolute paths in JSONL are a smell.
5. **Unit-test the resume/stall path** (`tests/unit/test_plan_proceed_goal.py`) before another 20+ min workflow burn.

---

## 2026-07-25 — model-feedback audit (failure envelope, limits, nudges)

Full rationale and "where do I add X" rules: **`docs/architecture/FAILURE_AND_LIMITS.md`**.
Boundary values: `python -m src.core.tools.limits`.

### Every failure the model saw had a different shape
- **Symptom:** recovery quality depended on which tool happened to break. One tool family
  (E2B sandbox) classified its failures; ~200 other return sites handed the model a bare
  human-phrased string ("Please try again or verify the path"), which makes an agent
  apologize and stop rather than recover.
- **Found via:** static audit of every failure surface, not a specific eval case.
- **Root cause:** no shared vocabulary. Error text reaching an LLM is a *prompt*, and nothing
  treated it as one.
- **Fix:** `src/core/tools/failure.py` — 13-class vocabulary, each with a retry policy and
  default NEXT_ACTIONS, one renderer, one envelope. `ensure_envelope()` runs in the executor,
  so a tool returning a bare dict is still classified on the way out (whole-fleet coverage
  without editing 200 sites). Domain matchers `register_classifier` instead of forking.
- **Effect:** delegate crashes, timeouts, param validation, loop blocks and tool errors all
  render as `TOOL_FAILED[<class>] ... RETRY: ... NEXT_ACTIONS:` — one shape to learn.
- **Watch for:** a new tool family inventing its own error shape. Point it at the doc.

### N failed attempts cost N x full tool output
- **Symptom:** a model retrying a wedged tool stopped making progress long before it ran out
  of turns — it ran out of *context*, and could no longer see the plan or the goal.
- **Root cause:** attempts are free on local inference but context is not; each failure
  appended up to 8000 chars, and head-only truncation dropped the NEXT_ACTIONS at the tail.
- **Fix:** envelopes bounded by construction (200KB trace to under 3KB); failed output capped
  at 1800 vs 8000 for successes; middle-out clipping; repeated identical failures collapse to
  a counter in `supervisor_node._collapse_repeated_failures` (first + most recent kept whole).
- **Effect:** 40%+ context reclaimed on a 6-attempt streak, and the counter ("failed 4 times
  identically") is better signal than four copies.

### Boundaries were scattered and several were unreachable
- **Symptom:** "why did the run stop?" required grepping for magic numbers.
- **Root cause:** limits lived as module constants, a private `_STEP_TURN_HARD`, a dict in
  tool_timeouts, and `ConfigManager.get` calls buried three layers deep.
- **Fix:** `src/core/tools/limits.py` — every boundary declared with a config key, default,
  and KIND (hint vs ceiling). Hint is the default posture; every ceiling has a hint below it.
- **Corrected values:**
  - tool timeout 60s to 120s. 60 was a cloud-API number; it killed local work about to
    succeed. Sandbox tools' outer bound was *below* their own internal 300s timeout.
  - supervisor turns: silent hard 25 to warn-at-15 (with remaining count + land-the-plane)
    and stop at 60. **25 was arithmetically unreachable** alongside the 20-turn step budget —
    a two-step plan could never finish, and died mid-step with no message, looking to a user
    exactly like the model giving up.
  - delegate cap counted *delegations*, so three SUCCESSES locked a specialist out with
    "has failed 3 times". Now counts failures; a success clears it.
- **Watch for:** new CEILINGs. `test_harness_limits.py` makes you justify them.

### Loop detection was dead on the main lane
- **Symptom:** only sandbox and `browser_*` had loop protection; everything else could cycle.
- **Root cause:** `LoopDetector` sits behind `ToolCoordinator`, which the LangGraph lane
  bypasses (see the comment in `event_wrapper.py`). Its 5s poll window also could never fire
  at local-inference latencies.
- **Fix:** `src/core/tools/repetition.py` at `ToolRegistry.execute_tool` (the one choke point
  every lane uses). Blocks identical failing repeats; hints on redundant success and cycling.
  Turn-scoped via `turn_id_var`.

### Executor loop-breaker state never reset
- **Symptom:** a browser click that failed once was banned for the life of the Brain, in every
  later conversation.
- **Root cause:** executors live on a process-lifetime `ExecutionRoutingEngine`. The browser
  comment justified this as "fine for a single-user desktop app" — but the page changes
  between turns, which is exactly when a retry *should* be allowed.
- **Fix:** `_TurnScopedState` mixin; counters die with their turn.
  Regression test: `tests/unit/test_turn_scoped_executor_state.py`.

### Every failed tool call was logged as a SUCCESS
- **Symptom:** none visible — that is why it lasted. Failed calls also rendered green in the UI.
- **Root cause:** tools signal failure by *returning* `{"success": False}`; they do not raise.
  `event_wrapper` treated "did not raise" as success.
- **Effect of the bug:** OATS embedding refinement, `capability_patterns` (suggested skills),
  and reflection lesson generation were all training on data saying everything always worked.
- **Fix + guard:** check `result.get("success")`; `tests/unit/test_tool_outcome_signal.py`
  (this had NO test before, which is how it survived).

### The live persona had ZERO tool-calling doctrine
- **Symptom:** none visible. Verified empirically by assembling the real prompt.
- **Root cause:** the reflection loop appended lessons into `tools.md`, and the overlay
  overrides baseline **whole-file** — so `config/persona/tools.md` was a lessons list and
  nothing else, at `scope: always`, in every prompt including subagents. `reflection.py:56-62`
  documents this accident as fixed; the code fix landed, the damaged overlay file never did.
  A stale `tool_calling.md` overlay had also silently disabled PARALLEL BY DEFAULT.
- **Fix:** lessons moved to their own `lessons` fragment; stale overlays removed so baseline
  resolves; pruned a duplicate lesson and one that had frozen a transient outage ("sandbox
  returns 401") into permanent doctrine.
- **Guard:** new `prompt_audit` check `doctrine_displaced_by_lessons`.
- **Watch for:** there is still **no lesson expiry/GC** — prune by hand.

### A prompt promised an injection that never existed
- **Symptom:** `tool_calling.md` told the supervisor "System injects [COMPLETED SO FAR]/[NEXT
  ACTION] — prioritize NEXT ACTION". Nothing has ever emitted either marker.
- **Root cause:** prompt/code contradiction. Deterministic checks could not see it because the
  prompt is internally consistent; what is wrong is in the *code*.
- **Guard:** new `prompt_audit` check `phantom_injection` greps `src/` for any marker a prompt
  claims the harness injects.

### None of the above was measurable
- **Fix:** `src/core/tools/fault_injection.py` forces a failure class on the Nth call to a
  tool; `evals/fault_recovery.py` runs a real in-process turn and scores
  RECOVERED / LOOPED / GAVE_UP_HONEST / FABRICATED / VOID at Repair@k.

  ```powershell
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.fault_recovery --k 3
  ```

- **Expectations are per-case** because "good" depends on the class: for `auth`, stopping
  honestly is the pass and recovering-by-retrying is the failure. Fabrication is scored first
  (inventing the failed tool's output can otherwise look like recovery).
- **Safety:** off unless armed via env var or `injecting()`; no config.yaml key on purpose.
- **UPDATE 2026-07-25 (#639): first live baseline run, and it caught bugs in the eval itself.**
  See the section below.

## 2026-07-25 — first fault-recovery baseline, and three bugs the scorer itself had (#639)

Running `evals.fault_recovery --k 3` against a live model for the first time surfaced that the
scorer trusted text pattern-matching over ground truth it already had. Two live spot-checks and
one full 5-case x k=3 sweep were needed to find and fix all three; the corrected baseline is
**Repair@3: 4/5 cases (80%)**, from `evals/runs/fault_recovery_baseline.json` (gitignored —
rerun to reproduce).

### Scorer trusted tool NAMES/text instead of tool_results[].success
- **Symptom:** A retry of the same tool that then genuinely succeeded (`rate_limit_backoff_once`)
  was scored LOOPED; a route-switch that got a real answer (`unavailable_switch_route` attempt 3,
  `web_search → web_search → delegate_to_browser_agent → web_search → web_search`, real
  population figure) was ALSO scored LOOPED, because `repeats >= 2` of the failing tool's name
  short-circuited to LOOPED before the code ever checked whether a later call had succeeded.
- **Root cause:** the harness injects the fault, so it KNOWS which calls it failed — but the
  original `score()` inferred outcomes from tool names and response-text substrings instead of
  reading `trace["tool_results"][i]["success"]`, which was sitting right there.
- **Fix:** `score()` now anchors every verdict on `tool_results[].success` first. Fabrication
  markers and honest-language regex only apply once ground truth confirms nothing after the
  fault actually succeeded — text matching narrows a claim, it never asserts one that facts
  contradict.
- **Effect:** the two cases above now score RECOVERED, matching what actually happened.
- **Watch for:** any future scorer heuristic that classifies from names/text before checking
  `success` — that ordering is exactly what caused this.

### A model that never touched the faulted tool was scored as if it had
- **Symptom:** `rate_limit_backoff_once` attempt 1 — the model asked a clarifying question
  instead of ever calling `web_search` (fault target never engaged) — was scored FABRICATED
  ("stopped without surfacing the failure").
- **Root cause:** if the faulted tool name never appears in `tool_calls`, there was no failure
  to recover from, but the old code fell straight into "no calls after the fault" → FABRICATED.
- **Fix:** `score()` now returns `VOID: "fault never engaged"` when the faulted tool was never
  called at all, per the eval data-quality contract (voids excluded from the rate, never
  silently counted as pass or fail).

### A fault hidden inside a sub-agent delegation looked like it "never registered"
- **Symptom:** `timeout_narrow_and_retry` scored VOID on all 3/3 attempts — "injected fault did
  not register as a failure — injector/harness mismatch" — even though the injector log clearly
  showed `[ INJECTED ] Forcing timeout on web_search (call #1)`.
- **Found via:** reading the raw `tool_calls`/`tool_results` in the JSON output: the trace was
  `[delegate_to_deep_research_agent(False), web_search(True) x9]`. The fault fired on
  `web_search`'s first GLOBAL call, which happened *inside* the delegation — so the failure
  surfaced on the wrapping `delegate_to_deep_research_agent` call, not as a standalone
  `web_search` entry. The scorer's `names.index("web_search")` found the tool's first
  STANDALONE appearance instead — a later, genuinely-successful, post-recovery retry — and
  concluded (wrongly) that the injected fault never fired.
- **Fix:** `score()` now treats a failed FIRST call to a `delegate_to_*` tool as the fault
  surface when the named tool hasn't already appeared as an earlier, unambiguous standalone
  call — narrow enough not to misattribute an unrelated organic failure (verified by regression
  test `test_delegate_failure_unrelated_to_the_fault_tool_is_not_misattributed`).
- **Effect:** `timeout_narrow_and_retry` rescored from `VOID, VOID, VOID` to
  `RECOVERED, RECOVERED, RECOVERED` — using the SAME already-captured live trace data, no
  re-run needed. Rescoring existing JSON against a fixed `score()` is a legitimate, cheap way
  to validate a scorer fix without re-burning model time.
- **Watch for:** any eval that infers "which call is the fault" from a literal tool name should
  ask whether that tool can be invoked through a delegate/wrapper first.

### Evals were triggering a real, unbounded, blocking personal-data sync
- **Symptom:** A live eval turn appeared to hang — CPU flat, LM Studio idle, TCP connections in
  CLOSE_WAIT to Google/GCP-hosted IPs — with no eval-side cause.
- **Root cause:** `SystemFactory.warmup_async_infrastructure` unconditionally spawns a
  background Chrome/Gmail/Drive ingestion sync (`daemons.ingestion_sync: true` in
  `preferences.yaml`) on every process boot, including eval runs. `GmailIngestor.ingest()` /
  the Drive equivalent (`src/core/intelligence/ingestion/google_workspace.py`) make
  synchronous, blocking Google API `.execute()` calls with **no timeout** and **no
  `asyncio.to_thread` wrapping**, called directly from the async sync loop — so a slow or
  stuck real Gmail/Drive call freezes the ENTIRE single-threaded event loop, including the
  eval's own model turn. Not a network flake; a real architectural blocking-call-on-the-loop bug.
- **Fix (scoped to evals only — the underlying ingestion bug is still open):**
  `evals/inprocess.py` now forces `CHAD_DAEMONS_ENABLED=false` before `ConfigManager.load_config()`
  (sticks because `.env` doesn't set that key, so `load_dotenv(override=True)` can't clobber it),
  and overrides `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2=false` AFTER `load_config()` (since
  `.env` DOES set those, and override happens post-reload).
- **Watch for (separate issue, not fixed here):** `google_workspace.py`'s ingestion calls still
  have no timeout and aren't offloaded to a thread — any other caller of this ingestion path
  (not just evals) can still freeze the event loop on a slow/stuck Google API call.

### Operating lessons
1. **A bug with no visible symptom needs a test, not a fix.** The OATS mislabel and the
   doctrine loss both survived for weeks because nothing asserted the invariant.
2. **When comparing against a "baseline" worktree, gitignored config is missing** — that made
   5 env-dependent failures look like regressions. Revert `src/` *in place* instead.
3. **Prompt/code contradictions are invisible to prompt-only audits.** If a prompt claims the
   harness does something, verify that something exists.
4. **Attempts are free on local inference; context is not.** Cap repetition and context
   growth, not effort. A ceiling that fires mid-task destroys work to save a resource we have.

---

## FIXED — 2026-07-26 inbox list preview vs conversation detail

### Inbox list preview treated as full vendor message (The Knot)
- **Symptom:** Terse "go to theknot.com and see what the vendor most recent message is" → agent quoted truncated list-row text ending mid-clause (`so you'll`) and never opened `/inbox/conversations/<id>`.
- **Found via:** live `browser_theknot` / `browser_theknot_terse` (runs through `140226`). Persona-only "Preview ≠ full content" did not change behavior.
- **Root cause:** Eager stop-on-visible-text beat weak prompt doctrine; list and detail were indistinguishable in the tool dump.
- **Fix (generic, not Knot-coached):**
  1. `src/core/orchestration/page_surface.py` — classify URL → `inbox_list` / `conversation_detail` / `search_results` / `login`.
  2. `MCPExecutor._annotate_browser_page_surface` — prepend `[PAGE SURFACE: …]` + **SURFACE GATE** continue nudge on every browser tool result while still on a list.
  3. Persona reorder so preview/verify beat "answer is in the snapshot → stop".
  4. Eval `tool_results_regex_any` — hard-fail if message asks never reach conversation detail / surface banner.
- **Verified:** `run_20260726_211604` (`browser_theknot_terse` on Dev `:8788`): SURFACE GATE on `/inbox/conversations`, model clicked Story Multimedia, landed `PAGE SURFACE: conversation_detail` + full thread body (including deposit/$100 and booking questions). Deterministic expect passed (`assertion_failures=[]`).
- **Still open (separate):** Final reply sometimes names the wrong turn in the thread (user line vs latest **vendor** line) even after opening detail — chronology, not list-preview.
- **Watch for:** Sites where detail shares the list URL (SPA with no path change) — path heuristics won't fire; need DOM cues.

### Named web app → wrong modality (Gmail/web_search instead of browser)
- **Symptom:** "summarize … on **outlook** … Angela Baker" → `search_gmail`/`read_gmail`. "what is prompt 4 on **chatgpt** … chat" → `web_search` only. Never `delegate_to_browser_agent`.
- **Found via:** `browser_3site_cluster` / `run_20260726_215446` (`evals/runs/browser_3site/RESULTS.md`).
- **Root cause:** Routing copy framed browser as post-API / dashboard / after failed search. `personal_context` + `search_gmail` preambles mapped generic "email/inbox" → Gmail. Private in-app chats looked like public trivia.
- **Fix (generic product-name gate):**
  1. `BROWSER_AGENT_HANDOFF_DESC` + supervisor DELEGATION RULES — named product wins unless API is for that *exact* product; no Gmail/web_search substitute.
  2. `search_gmail`/`read_gmail`/`gmail_check_inbox` preambles — Gmail-only; explicit Outlook exclusion.
  3. `personal_context` / `inbox_triage` / persona `supervisor_routing` / `mcp_instructions` / `tools.md` — same gate.
- **Effect:** Unit `test_browser_product_name_routing` locks the copy. Outlook/ChatGPT **re-probe after gate:** Outlook reached browser but hung on unsigned-in surface (killed); ChatGPT still `web_search`-only (`run_20260726_223745`). Persistent suite: `browser_named_saas` — see `docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md`.
- **Watch for:** Unnamed "check my email" still correctly stays on Gmail; bulk inbox triage must not auto-browser.

### Eval WS connect preload JIT-loaded gemma-1b (intent router), not narrator
- **Symptom:** Every live/workflow probe showed LM Studio `Reloaded 'google/gemma-3-1b'` ~8s after connect. Narrator already off (`wait_narrator_enabled: false`).
- **Found via:** Dev Brain `:8788` log during named-SaaS reprobes — `Model preload scheduled (ui_mode=auto)` then gemma reload; turn itself was `system_turn_explicit` (classify skipped).
- **Root cause:** Eval auth looked like a normal desktop Limb → `_maybe_preload_on_connect` used GUI `default_turn_mode=auto` → `aux_models_for_ui_mode("auto")` = intent-router 1B. `local_models.lifecycle.preload_on_connect: true`.
- **Fix:** Eval auth sends `client_type=eval` + `ui_mode=agent` (or `plan` when specified). `resolve_connect_preload_ui_mode` forces agent for eval and rejects eval+auto. Connect preload then warms the 9B selector path, not gemma-1b.
- **Also:** Playwright MCP `--sandbox` for headed ChadBrowser (desktop) — avoid yellow `--no-sandbox` infobar; reserve `--no-sandbox` for Docker/root/CI only.
- **Watch for:** Planner-heavy suites should `open_eval_ws(ui_mode="plan")` if they want plan-lane aux residency at connect.

---

## OPEN — 2026-07-26 browser named-SaaS follow-ups (not fixed)

Suite to re-run: `browser_named_saas` · writeup: `docs/evals/BROWSER_NAMED_SAAS_2026-07-26.md`

### Login / unsigned-in surface does not hard-stop (Outlook loop)
- **Symptom:** After product-name gate, Outlook ask reached `browser_agent` but landed on a non-signed-in / marketing / MS login surface and looped (~14m, huge truncated snapshots, stale refs) instead of exiting with `LOGIN REQUIRED`.
- **Found via:** killed mid-reprobe 2026-07-26 ~22:23–22:37 (Dev `:8788`).
- **Root cause (likely):** `page_surface` login URL heuristics miss Microsoft hosts (`login.microsoftonline.com`, account picker, splash); persona login text alone does not stop the loop.
- **Fix / future:** Expand login-surface detection + harness TERMINAL nudge (not just persona). Re-test `outlook_angela_baker` once ChadBrowser is signed into Outlook.

### ChatGPT private chat still routed to `web_search`
- **Symptom:** "what is prompt 4 on chatgpt ai agent test prompt chat" → `web_search` only; invented Prompt 4; `tools_any` hard-fail.
- **Found via:** `run_20260726_215446` + `run_20260726_223745` after product-name gate copy.
- **Root cause:** “what is…” framing still wins over named-product / private-chat doctrine.
- **Fix / future:** Stronger private-chat ≠ public-fact examples or pre-tool routing; then re-run `chatgpt_prompt4`.

### Knot chronology / speaker attribution (soft residual)
- **Symptom:** After opening conversation detail, reply may still cite truncated preview text or the wrong speaker turn (user vs vendor).
- **Found via:** earlier Knot runs + soft residual on PASS `knot_vendor_terse`.
- **Fix / future:** Generic verify-before-done / speaker cues — not Knot-only coaching.

---

## OPEN — 2026-07-26 eval ops / harness reliability (not fixed)

### Monitored overnight / church runs can waste hours without producing live signal
- **Symptom:** Long-running eval windows finish (or get abandoned) with little/no judged live transcript — dry-run+Gemma → mass `judge_needs_rejudge` VOID; `fault_recovery` hangs with empty log for ~2.5h; phase runner appears "running" while stdout never grows.
- **Found via:** church overnight attempt + first `run_church_2h_monitored.ps1` attempt (2026-07-26).
- **Root causes (this session):**
  1. `Start-Process` with both `-NoNewWindow` and `-WindowStyle Hidden` → child never starts; monitor sees timeout only.
  2. Redirected Python stdout fully buffered → phase log size stays 0 → false stall kill after ~10m.
  3. Unbounded `fault_recovery` / dry-run `--judge` with Gemma produces VOID noise or hangs; not a model-quality signal.
  4. Main checkout reclaiming `:8767` while Dev Brain expected there — use `:8788` for Dev evals.
- **Mitigation in tree:** `evals/run_church_2h_monitored.ps1` — hard phase timeouts, ~10m stall kill on log growth, skip `fault_recovery`, no dry-run judge, `PYTHONUNBUFFERED=1` + `python -u`, Dev Brain `ws://localhost:8788/ws`. Resume live phases after free lanes already snapshotted.
- **Fix / future:** Prefer unbuffered launch in any long eval wrapper; never combine incompatible `Start-Process` window flags; require preflight+first-case log growth within N minutes before calling a multi-hour window "healthy."
- **Watch for:** Phase `.log` size 0 while `evals.runner` PID alive; 8767 health that isn't the worktree Brain.

### Browser golden judge VOID on large tool traces (`amazon_aura_poster`)
- **Symptom:** Case runs (10 tools) then VOID `judge_needs_rejudge` / `context_too_large` (user_chars≈64k vs judge ctx 32k); LiteLLM also logged exceed_context on engine (~29k vs 25k available). Loop_2h stall-killed amazon mid-judge with no log growth.
- **Found via:** church_2h `05_browser_golden`; loop_2h wave3 stall kill on case 6.
- **Root cause:** `EVAL_JUDGE_USER_CHAR_BUDGET=0` forwarded raw Playwright YAML dumps into the judge prompt.
- **Fix:** `evals/judges.coalesce_tool_results_for_judge` collapses ```yaml``` fences / long lines and applies a soft cap even when budget=0; wired into `response_grounded` + workflow budgeting.
- **Effect:** Judge prompts stay inside LM Studio ctx; amazon/research-compare should score instead of VOID/stall.
- **Watch for:** Soft cap dropping the one snippet the response actually cited — raise cap before blaming the model.

### Concurrent `--live` suites on one Brain → empty-trace FAILs
- **Symptom:** Three parallel live runners (`live_check` + `church_safe_live` + `browser_golden`) each report case FAIL with `None tools / None plan / None resp` after ~360s.
- **Found via:** `evals/run_loop_2h.ps1` wave1 (2026-07-26 ~18:41). Brain log shows interleaved turns fighting one Gemma slot.
- **Root cause:** Live/workflow lanes are sequential-by-design for VRAM (noted in `eval_exclusivity.py`); concurrent live WS clients share the model and starve each other into empty/timeout traces. Eval exclusivity allows concurrent evals — it only blocks *non*-eval turns.
- **Fix:** Loop waves = **at most one** `--live`/`--workflow` job + free lanes (`tool_selection --concurrency 3`, `prompt_audit`) in parallel.
- **Effect:** Live signal stays interpretable; free lanes still run 3-wide.
- **Watch for:** Reintroducing multi-live parallelism “to go faster” and mistaking empty-trace FAILs for model regressions.

---

## 2026-07-26 — church_2h email judge false fails (fixed)

### Judge sees count-only `search_gmail` wire → false "hallucination" fails
- **Symptom:** Live email cases (`email_live`, `inbox_read_only`, …) FAIL `response_grounded` with "tool only indicated that 10 emails were found" while the assistant cites subjects/amounts. `church_safe_live` looked like 0/7.
- **Found via:** church_2h `03_live_check` + `04_church_safe_live` (Gemma-4-26b). Transcript **reasoning** listed all 10 subjects; WS `tool_end.result` was only the count+hint string.
- **Root cause:** `search_gmail` put metadata in sibling `emails[]`; `_broadcast_tool_end` forwarded only `raw["result"]`. Live eval + judge never saw the list the model used.
- **Fix:** `src/tools/workspace/gmail.py` `_format_email_search_result` puts id/from/subject/date/snippet into `result`. `ui_state_syncer._broadcast_tool_end` appends capped JSON of sibling payload keys when `result` is a short summary (<400 chars).
- **Effect:** Judge/UI evidence matches model-visible list; stops false grounded fails on healthy email summaries.
- **Watch for:** Other tools returning `{result: "Found N…", items/files/…: [...]}` without putting the list in `result`.

---

## 2026-10-16 — Bonsai-27b Planning & Execution Hardening (fixed)

### Nested subdirectory path stripping in `FileWriter`
- **Symptom:** Attempting to write to nested workspace paths (`docs/reports/audit.md`) collapsed to `docsreportsaudit.md` or failed with directory resolution errors outside `artifacts/`.
- **Found via:** Goal evaluation check: *"make sure he has the ability to go to docs/reports/narration_guard type thing"*.
- **Root cause:** `FileWriter._sanitize_filename` stripped slashes, and `_resolve_directory` blocked creating directories other than bare `artifacts/`.
- **Fix:** In `src/tools/filesystem/file_writer.py`, `_sanitize_filename` preserves and splits directory paths if present, and `_resolve_directory` permits `os.makedirs` if the resolved directory is within the project workspace `cwd` or `_artifacts_dir()`.
- **Effect:** Agents can write directly to nested project subdirectories (`docs/reports/...`) safely.

### Planner re-scout infinite loop
- **Symptom:** Planner re-evaluated history after a Scout gather turn and repeatedly called `dispatch_scout` in an infinite loop without proposing a plan.
- **Found via:** `evals/plan_phase_benchmark.py` running Prompt 3.
- **Root cause:** Placeholder `dispatch_scout` in graph history lacked content, and `dispatch_scout` remained bound on re-entry.
- **Fix:** In `src/core/orchestration/nodes/planner_node.py`, replaced the placeholder `dispatch_scout` ToolMessage with the actual `gather_brief`, and conditionally removed `dispatch_scout` from available tools on re-entry (leaving only `propose_plan`).
- **Effect:** Planner reliably transitions from Scout gather to `propose_plan` on turn 2.

### Scout mutation tool leakage
- **Symptom:** Scout attempted to call `create_gmail_draft` during gather recon, triggering an unexpected approval token pause on a gather turn.
- **Found via:** Prompt 1 scout trace in `evals/full_trace_benchmark.py`.
- **Root cause:** `_SCOUT_BLOCKED_TOOLS` blocked `send_email` but omitted Gmail drafting and file mutations.
- **Fix:** In `src/core/orchestration/nodes/scout_node.py`, added `create_gmail_draft`, `draft_gmail_reply`, `send_gmail_message`, `create_or_update_file`, and `create_repository` to `_SCOUT_BLOCKED_TOOLS`.
- **Effect:** Scout is guaranteed strictly read-only reconnaissance.

### Shell Commander virtual environment isolation
- **Symptom:** Bare terminal commands like `pytest test_narration_guard.py` failed with `command not found` or ran against system Python.
- **Found via:** Prompt 2 execution trace in `evals/full_trace_benchmark.py`.
- **Root cause:** Git Bash subshell on Windows inherited the base system environment without workspace `.venv/Scripts` on `PATH`.
- **Fix:** In `src/tools/os/shell_commander.py`, automatically prepended `abs_cwd / ".venv" / ("Scripts" if win32 else "bin")` to `PATH` and set `VIRTUAL_ENV`.
- **Effect:** Workspace CLI tools (`pytest`, `python`, etc.) resolve and execute against project virtualenv packages seamlessly.

### File tool stripping during plan turns
- **Symptom:** Planner could not select or plan file tools like `write_file` because they were filtered out.
- **Found via:** Prompt 2 & 3 plan generation inspection.
- **Root cause:** `is_supervisor_delegate_only` in `turn_selector.py` stripped file tools from all non-agent turns without distinguishing planning turns.
- **Fix:** In `src/infrastructure/tools/selection/turn_selector.py`, excluded planning turns (`if not planning_turn and is_supervisor_delegate_only(...)`).
- **Effect:** Planning turns retain full visibility of file creation tools.


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
