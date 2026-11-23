# Browser agent: goal tracking and measured recovery

Status: runtime controller, container scrolling, exact-copy integration and GUI
progress implemented behind `orchestration.browser_controller_enabled: false`.
Regression checks pass; the 80-trial comparison is complete. Keep the flag off:
short tasks and stale-reference recovery regressed despite improved scrolling.
See `browser-agent-progress-results.md` for results and limitations.

## Objective

Make Chad's existing browser specialist complete bounded browser tasks with fewer
unproductive actions on the current local model. Keep Playwright, the supervisor,
approval handling, and exact-content transfer. Measure task success separately
from tool-call success.

For the Gemini example, the browser's job ends at a verified content reference:
correct conversation → correct completed response → full text captured. The
supervisor then creates and verifies the Google Doc through the native tool.

## 0. Record a reproducible baseline before changing behavior

- Add a benchmark that invokes the production specialist graph and tool pipeline.
  Reuse fixtures and recording ideas from `evals/browser_hybrid_probe.py` and
  `evals/browser_playwright_probe.py`; their standalone loops are not substitutes
  for measuring Chad's actual graph, prompts, sanitizer, and handoffs.
- Record model ID, quantization if available, loaded context size, generation
  settings, effective prompt hash and layer, tool schemas, code revision, browser
  state, limits, and model warm/cold status for every run. Save prompt snapshots
  with private live traces outside tracked files.
- Create local browser fixtures with known answers: chat search and last-response
  capture; a nested scrolling chat panel; lazy loading; an obstructing modal;
  stale refs after rerender; a missing target; and misleading clickable controls.
- Include a normal short task that should finish immediately, to catch extra
  planning overhead. A blocked task is successful only when honestly reported
  blocked; it does not count as a completed deliverable.
- Run the original behavior with the effective local overlay intact. Save this
  baseline before fixing overlays or enabling the new controller.

Deliverable: a production-path runner, fictional fixtures, and baseline traces.

## 1. Fix effective prompt delivery and exact-copy integration

Files: `src/infrastructure/repositories/persona_store.py`,
`src/core/intelligence/prompt_builder.py`, `prompts/persona/browser_agent.md`,
`config/persona/browser_agent.md`, `src/core/orchestration/handoffs.py`.

- Inspect the effective browser, supervisor-routing, and planner fragments.
  Existing overlays replace whole baseline fragments and can hide new rules.
- Put mandatory browser-controller protocol in a dedicated runtime instruction
  block, separate from user-customizable style/persona. Keep the ordinary overlay
  mechanism; do not silently change merge semantics or discard customizations.
- Update the local browser overlay where it conflicts with the new workflow.
  Keep personal overlays out of Git. Expose effective prompt provenance/hash in
  diagnostics so future prompt edits are observable.
- Cover captured-content references through the real SanitizedToolNode path.
  Tool results can have a `[TOOL_DATA]` prefix; the new reference extractor
  currently assumes plain JSON. Parse that known transport wrapper before JSON,
  retaining source text as untrusted data and validating stored references.
- Verify the capture tool is bound and `content_id` reaches the Docs schema.

Acceptance: baseline and overlay configurations both receive the required runtime
protocol; a sanitized capture result survives the specialist-to-supervisor handoff
without the model repeating its ID or body.

## 2. Add an explicit browser task contract and state

Files: `src/core/orchestration/handoffs.py`,
`src/core/orchestration/graph/state.py`; new browser-specific state module under
`src/core/orchestration/agents/`.

Task contract:
- original objective and browser-only scope;
- known destination and target identity, where available;
- a small ordered milestone list and the current milestone;
- required deliverable and observable completion conditions;
- constraints, action/time budgets, and allowed tool capabilities.

Runtime state:
- delegation/run ID, distinct from the long-lived conversation ID;
- verified milestones, unresolved conditions, and captured content references;
- current URL/tab/view and observation fingerprint;
- last action, expected change, observed result, and recovery attempts;
- compact factual memory, recent actions, stall counter, and total budget use.

Store facts as structured state. Keep captured bodies in the existing content
store. Reset per-delegation counters when a new task starts; preserve state across
approval pause/resume of the same task. Do not reuse stale specialist history as
the current task's progress.

Acceptance: a new delegation cannot inherit another task's completion state,
action budget, failed refs, or current milestone.

## 3. Replace browser-only action selection with a validated decision

Files: `src/core/orchestration/agents/specialist_graph.py`,
`src/core/orchestration/agents/factory.py`; new browser controller module.

Keep other specialists on their existing loop. Behind an initial feature flag,
give the browser specialist a short structured decision schema containing:
- previous action assessment: success / failed / uncertain, with observed evidence;
- current milestone and a brief factual memory update;
- next immediate subgoal and expected observable change;
- one action with schema-validated arguments, or a terminal result.

Use native structured tool calling with a browser decision envelope. Dispatch its
selected action through the existing registered tool execution and approval path.
Preserve correlation IDs, streaming, sanitizer behavior, and pending-approval
propagation. Do not create an unrestricted tool-name/code execution escape hatch.
Do not add a second planner or judge model call to every action by default.

Expose a small phase-appropriate set of existing tools: navigate, inspect/find,
click/type/key, scroll, wait, capture, finish. Preserve access to specialist
capabilities such as uploads when the contract requires them. Validate availability
and arguments against the real registered schemas rather than duplicating them.

Reject malformed decisions with a bounded corrective retry. Enforce one action in
code. A narration-only response is not implicit completion.

Acceptance: each executed action has an explicit subgoal and expectation; malformed
or multiple-action decisions cannot run silently; approval tests still pass.

## 4. Add bounded context and observation-based progress detection

Files: browser controller/state modules,
`src/infrastructure/tools/execution/snapshot_pruner.py`,
`src/core/orchestration/nodes/tool_node.py`.

Build each model input from the contract, current milestone, verified facts,
current observation, and a small recent-action window. Replace superseded snapshots
in the model input; retain complete traces outside that input. Preserve coherent
tool-call/result pairs where required by the provider. Never compact captured text
by rewriting it.

Separate three concepts:
1. tool execution succeeded;
2. the observed browser state changed;
3. a task milestone gained supporting evidence.

Fingerprint observations using URL/tab, relevant visible content and control
identity, scroll-container position, and target evidence. Normalize transient refs,
timestamps, animations, and other noise. A changed fingerprint alone is not proof
of progress, and useful scrolling can reveal content without changing the URL.

Detect repeated states, A→B→A cycles, repeated equivalent targets, and scroll
oscillation without useful new evidence. Allow bounded exploration and waits for
genuinely loading content. Start with configurable thresholds: recovery after
three unproductive action-observation cycles, at most two distinct recovery
attempts, then return blocked/incomplete. Tune these against baseline traces.

Recovery must name a changed approach: search within the app, inspect another
control, change the scroll container, dismiss an observed obstruction, or reacquire
stale refs. A fresh snapshot by itself does not reset the stall counter.

Acceptance: successful no-op clicks and alternating scrolls trigger recovery;
useful scrolling and legitimate loading do not terminate prematurely.

## 5. Make scrolling container-aware

Files: `src/tools/web/browser_scroll.py`; browser observation/controller modules.

- Accept an observed scroll-container target, retaining window scrolling as a
  fallback. Inspect scrollable ancestors when the target is inside a panel.
- Return container identity, before/after scroll positions, boundary state, and a
  fresh observation of useful visible content.
- Base stationary detection on the actual container and content changes. Scope
  counters to the current delegation and container; distinguish a real boundary
  from lazy loading, the wrong panel, or an expired target.
- Reuse snapshots already returned by actions. Take a new one only when the
  observation is missing, stale, or insufficient.
- Track logical action counts separately from underlying MCP calls, so wrappers
  and snapshot reads cannot silently bypass or double-charge the task budget.

Acceptance: a fixed-height chat panel scrolls while the window remains stationary;
reaching its boundary terminates useless scrolling without hiding newly loaded text.

## 6. Require evidence for terminal results and expose progress in the GUI

Files: browser controller, `src/core/orchestration/handoffs.py`,
`src/remote/stream_events.py`, frontend stream protocol/reducer and tool UI.

Terminal result: completed / blocked / incomplete, with verified milestones,
unmet conditions, evidence references, and the blocker or final deliverable.

Use deterministic checks where the result is machine-checkable: captured content
exists in the correct scope; requested fields are present; a fixture's actual state
matches its goal. For general web outcomes, require a relevant fresh observation
and explicit supported assessment; do not pretend there is a universal deterministic
validator for arbitrary websites.

The supervisor must not mark the larger task completed merely because the browser
returned. In the sermon workflow, only the native Docs tool can confirm the final
document matches the captured text.

Show concise milestone status, current subgoal, recovery reason, and terminal
outcome in the GUI using typed events. Do not stream private chain-of-thought.
Keep approval controls and manual Stop working throughout.

## 7. Compare performance, then decide whether to enable it by default

Run baseline and candidate in isolated profiles with identical fixture state,
model/settings, task limits, and background-autonomy settings. Alternate execution
order and repeat each fixture at least five times; report run-to-run variation.
Add a smaller held-out fixture set after tuning. Restore persisted session state
between runs and use the same warm-model policy for both variants.

Report separately:
- verified deliverable completion and correct blocked outcomes;
- false-success claims and exact-copy mismatches;
- logical actions, raw MCP calls, and unproductive action cycles;
- elapsed time and model input/output tokens per completed task;
- recovery frequency and successful recovery rate;
- peak input-context size and premature-stop rate.

Proposed promotion criteria, subject to baseline results:
- zero false-success claims or exact-copy mismatches in the fixture suite;
- higher verified completion on the stall-prone tasks, with no material regression
  on the short tasks or approval/Stop behavior;
- at least 30% fewer unproductive actions on the designated looping fixtures;
- context stays within the configured browser-input budget;
- disclose latency tradeoffs rather than calling fewer actions a universal win.

Use ablations if the full candidate improves: prompt delivery only; then contract
and context; then progress/recovery and container scrolling. This distinguishes
which change helped. Keep the local model constant first; compare specialized
browser models only as a later, separate experiment.

Finally run the real Gemini → Google Docs workflow on dev as a supervised smoke
test. Use the intended signed-in profile and selected conversation, verify the
copied response, and record only private traces. Do not count that changing live
website as the sole performance benchmark.

## Suggested implementation checkpoints

1. Baseline runner and prompt/sanitizer integration fixes.
2. Task contract, decision schema, context builder, and progress/recovery controller.
3. Container-aware scrolling and terminal evidence validation.
4. GUI progress events, regression checks, A/B evaluation, and results review.

Use a feature flag to retain the original browser loop for comparisons and rollback.
Do not change unrelated specialists or enable the new controller by default until
the comparison has been reviewed.

## Implementation record (2026-10-20)

- `browser_controller.py`, `browser_state.py` and `browser_protocol.py` implement
  the browser-only graph, task state, compact model input, action schemas,
  evidence checks and bounded recovery. `specialist_graph.py` selects this graph
  only when the feature flag is enabled.
- The existing ToolNode, permission envelopes, executor checks and cancellation
  path remain the execution boundary. Action/time limits include stalled model
  and tool calls; human approval time is excluded. After the last allowed action,
  only evidence-based finishing is available.
- Container scrolling returns geometry and boundaries plus a fresh snapshot.
  A short bounded exploration allowance handles long paragraphs without treating
  movement as completion. Verified exact-text captures end capture work directly.
- Delegations preserve resolved source evidence and active specialist guidance.
  Checkpoint history is excluded from current model input. Capture extraction
  understands the sanitizer wrapper and ignores earlier delegations' references.
- Typed progress events reach the actual chat renderer and replace status for
  the same browser run. Only public milestone/subgoal/recovery information streams.
- Local browser overlay conflicts were removed after saving the original private
  overlay and effective prompt under ignored artifacts. Baseline comparison uses
  the saved original prompt; the candidate gets the mandatory runtime protocol.
- Regression checks cover real graph dispatch, schema rejection, bounded context,
  exact-copy storage/handoff, approval/resume, long approval waits, Stop, timeouts,
  and actual nested/lazy panels. The frontend suite and production build pass.
- `evals/browser_progress_benchmark.py` runs the repeated comparison;
  `evals/browser_progress_report.py` summarizes the frozen trials. Development
  smoke traces are excluded from the final comparison. The new controller stays
  disabled while completion regressions and latency are assessed.

The user supplied the intended Gemini conversation for the live capture test.
Docs creation is pending at their request. Native Google authorization was restored
with refresh_google_auth; the tool now tries automatic refresh before sign-in.
The initial live test exposed JSON being severed by the shared output limiter;
browser results now clip strings inside the envelope and preserve metadata.
Fictional fixtures and mocked Docs verification do not substitute for a live copy.

After the user clarified that a saved last message is the whole success condition,
a bounded Gemini last-response workflow was added. It uses the observed down arrow
and fixed DOM inspection through the existing tool executor. Two live captures
succeeded; a separate fresh read verified exact equality of the 5,702-character
last response. General model decisions now require only action and arguments.
The stale-hint scanner was corrected to require tool-error evidence, and real
frame-prefixed browser references are supported. The general controller remains
behind its feature flag while broader performance is assessed.
