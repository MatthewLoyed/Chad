# Focused self-eval work plan — 2026-10-19

Start a new chat with the [current handoff](CHAD_SELF_EVAL_HANDOFF.md), including
runtime state, private evidence locations and the bounded Phase 4D.2 next step.

## Latest next action

Scout/planner prompt improvements have positive user feedback and recorded dev
planning checks. First verify the final direct-approval/stream-visibility repair in
a fresh dev GUI run, then reduce task specificity gradually and triage the first
failure. See the wrap-up checkpoint in `CHAD_SELF_EVAL_HANDOFF.md` and
`CHAD_DIRECT_TOOL_APPROVAL_RESUME.md`. Historical priorities below describe earlier
stages; further evaluator tuning is not the immediate next action.

## Goal and priority

Primary value: Chad's testing and self-evaluation should expose concrete or behavioral
errors, retain evidence, analyze what happened and produce trustworthy triage that a
human can hand to Codex or another capable cloud model. A useful, reproducible finding
is a successful deliverable even when Chad cannot fix it or prove its root cause.

The core path is **test → identify → analyze → triage/handoff**. Self-repair and
matched before/after proof remain useful optional extensions. Keep observed failures,
inconclusive infrastructure, suspected causes and repair readiness separate. No
automatic repair dispatch, merge or deployment is needed for identification success.

## Current checkpoint

- **Phase 4D.1 — retrieval reproduction and bounded repair: complete for this case.**
  Runtime identity/source audit ruled out stale Brain code. An undersized offline
  probe hid the live candidates; matching the real fetch reproduced scores exactly.
  The effective 0.50 automatic Drive cutoff removes both distractors in actual LFM
  input. Same-prompt first decision changed from source traversal to Draftout/Prism
  search plus a saved-routine attempt. Full task and referent correctness remain
  unproven. Client/formatter checks: 18 passed. See
  [measured result and recall tradeoff](CHAD_RELEVANCE_CUTOFF_TEST.md).
- **Phase 4D.2 — initial saved-decision triage complete.** See
  [context changes, Qwen limitations and Bonsai restart blocker](CHAD_PHASE4D2_TRIAGE.md).
  Enriched Qwen replay passed citations but remains semantically untrusted. Corrected
  the before-prompt snapshot pairing for future review; 16 focused tests pass.
  User subsequently instructed proceeding; dev-only restart succeeded and the
  original-prompt Bonsai test exposed source-code-only clarification choices.
  See [Bonsai task evidence](CHAD_BONSAI_TASK_TEST.md), including the separately
  clarified continuation, existing LFM browser pin and fixed eval resume defect.
  Prioritize acting-model artifact binding/execution before further reviewer tuning.
  32 focused tests pass; no persistent model preference change or production restart.
  Original procedure: feed the original
  task, canonical actions, retrieval audit and explicit first-decision stop boundary
  to the existing phased Qwen reviewer. Require it to distinguish verified retrieval
  repair from unproven task success and examine the saved-routine/tool-discovery
  mistake without inventing tool outcomes. No broad comparison or speculative repair.
- **Phase 5 — optional, deferred:** further product repairs and full task completion
  proof. Identification and a useful handoff remain the core finish line.

- Earlier failed checkpoint: a measured Drive cutoff candidate (0.22→0.42), strict unknown-score handling and
  removal of fabricated Drive fallback scores were tested. The actual 8788 replay
  still received the distracting documents and repeated the source-code confusion;
  do not call it fixed. The live retrieval/scoring discrepancy needs observation
  before further threshold tuning. That replay exposed two reasoning-guard bugs,
  fixed separately in `8f31ac6`. See [evidence](CHAD_RELEVANCE_CUTOFF_TEST.md).

- Reviewer staging is implemented as three ordered sections in one call: evidence
  eligibility → behavioral findings → triage. Focus is now the demonstrated main-model
  referent error: Meet join code became unrelated source code before tool execution.
  Read-only prompt inspection supports retrieval distraction as a hypothesis, not a
  proven cause. See [evidence and bounded candidate-fix controls](CHAD_MEET_CODE_REFERENT_TRIAGE.md).
  Product prompt edits/A-B and external-task replay have not been performed for this finding.

- Isolated canonical-reference replay resolved the observed tool/event-index confusion:
  11/11 canonical citations valid, no paired name mismatches. Two ambiguous legacy
  parallel results remain explicitly unassigned. Qwen introduced an unregistered
  round label, so full reference acceptance and semantic trust remain unproven.
  No external task replay; see [measurement and evaluation staging](CHAD_MEET_PRISM_EVAL.md).

- Phase 4D real-task run and isolated Qwen review are complete. The 30-minute task
  backstop retained 110 calls/311 events and cancellation was confirmed. Corrected
  evidence packaging allowed tool-free Qwen 3.6 35B review to finish; its report
  confuses event/tool indices, so reviewer trust remains failed. See
  [real-task evidence and limits](CHAD_MEET_PRISM_EVAL.md). Next: improve evidence
  citation/uncertainty reliability using this saved capture, without repeating external
  actions. Product repair remains deferred.

- Phase 4C.2 intake mechanism is complete: offline operator-owned contracts, exact
  trace evidence, deduplicated local bug records and explicitly reviewed handoff
  fields. No inference required. See [usage and limits](CHAD_REAL_TASK_INTAKE.md).
  Phase 4D applies this to actual user-task failures; no real-task coverage is
  claimed from the existing synthetic integration sample. Self-repair stays deferred.

- Phase 4C.1 evidence audit is complete without new model calls: memory save reported
  one fact (incorrect transient fixture information); saved-routine call reported
  not_found and the task recovered. These are tool-reported outcomes, not independent
  storage verification. Inbox/handoffs enriched; no extra bug occurrence or repair.
  Phase 4C.2 real-task intake remains next; optional Phase 5 remains deferred.

- Phase 4B is complete: four reusable discovery regressions, an idempotent local bug
  inbox, and a focused handoff per verified symptom. Full dev 8788 sample: three
  passes and one failed task, producing formatting and unrequested-memory-attempt
  records. See [workflow and next Phase 4C](CHAD_REGRESSION_INBOX.md). Optional Phase
  5 repair stays deferred; the next priority is concrete task/scope coverage.

- Full dev Brain/WebSocket path works; in-process behavioral startup remains unproven.
- LFM handles the basic local contracts, but the directory task showed formatting and
  no-network scope failures. Its free-form diagnosis contradicted the trace.
- Direct Qwen35B and Gemma4-26B diagnoses were more faithful, but both used Markdown;
  those samples do not establish a production winner.
- Independent coding grader and baseline controls are committed.
- Structured triage gate implemented and validated: 103 tests, 12 subtests passed.
- No persistent model-role change. Production Brain remains out of scope.
- Follow-up control check succeeded: Gemma returned schema-valid, evidence-valid JSON
  with zero reported reasoning tokens using the effective endpoint controls. It
  recommended investigation; coding remains blocked.
- Explicit persona snapshot/capture support is implemented and covered offline,
  including ignored overlay-only patches. Full dev-Brain A/B remains unproven.
- Phase 4 is exercised: it exposed a real hidden-file omission and reviewer false
  negatives, cross-case attribution and protocol failures. The independent detector
  and handoff are useful; model review remains untrusted. No product repair.

## Phases and completion criteria

There are **four core phases**, followed by **one optional repair phase**. These are
milestones for a bounded proof, not an estimate of production readiness or elapsed time.
The previous repair-first milestones are superseded by this identification-first order.

### Phase 1 — Trustworthy evidence and controls — COMPLETE for this bounded proof

Full dev-Brain transport demonstrated; objective answer/scope/fixture checks and
evidence-linked triage gate available; effective Gemma JSON/reasoning controls and
explicit persona capture verified. Gate commit: `1f38bf45`. Controls/capture checkpoint:
`3a188cd`. Limits: live cache attestation and general self-diagnosis remain unproven.

### Phase 2 — Detect and reproduce concrete failures — INITIAL PASS COMPLETE

Run one bounded case against full dev Brain on 8788 using reset synthetic fixtures,
fresh conversations and an independently checked positive control. Preserve exact
prompt, response, indexed tool calls, fixture hashes, code/config/persona provenance,
budgets and residency changes. Separate answer, scope and fixture violations from
incomplete runs. Done when a runnable identification-only probe produces an honest
finding packet or explicitly records that a historical failure did not recur.
No repair policy or verified causal theory is required to report an observed error.
Result: the 8788 run reproduced the strict-output failure in one of two listing
trials, with a passing count/sum control. Both fixture checks passed; no forbidden
network-tool attempt was recorded in either new trial. See [findings](CHAD_SELF_EVAL_FINDINGS.md).

### Phase 3 — Evidence-backed analysis and cloud-model handoff — INITIAL PASS COMPLETE

Have Chad analyze the captured failures; independently check factual references and
retain contradictions or inconclusive analysis as results. Produce concise bug records
with expected/actual behavior, exact evidence, reproduction steps, observed repetition,
unverified hypotheses and next discriminating checks. Keep model self-analysis distinct
from operator-owned detection. Done when the packet is useful to a human/Codex without
assuming a root cause, guessing scope or launching a coder. An investigate recommendation
can be a successful triage result; it is not repair authorization.
Result: one full-Brain Chad self-review completed in 138.1s with raw schema JSON,
no tool calls and a valid response_not_json observation. It correctly identified
formatting/prose, recommended investigate and requested no repair scope. Its second
hypothesis questioned filename verification; independent review found the original
review prompt had omitted captured tool results and fixture evidence. That input gap
was addressed in the harness and tested; the subsequent Phase 4 replay is recorded
below. The original raw review and its limitation are preserved; do not equate
mechanical evidence validity with verified hypothesis prose.

### Phase 4 — Validate repeatable discovery — BOUNDED AUDIT COMPLETE; REVIEWER GATE FAILED

Prove the same test → identify → analyze → handoff path on a small, explicit set of
nearby/held-out contracts and known-good/known-bad controls. Check false positives,
missed failures, unsupported factual claims, grouping and infrastructure handling.
Prioritize concrete/behavioral bug discovery and useful prompts over model rankings.
Done when the small discovery sample is reproducible with candid coverage limits.
No broad model comparison or unbounded autonomous search is part of this milestone.
First check: replay the saved failure through the enriched review prompt, then use
known-good/known-bad and held-out evidence to test false positives and claim fidelity.
Do not jump to repair because the first prompted self-review found a real error.

Results: the enriched replay completed (188.8s) without repeating the missing
filename-evidence concern, but tied routine-call prose to a JSON observation rather
than the routine event. A held-out full-Brain execution (53.4s) omitted an explicitly
requested hidden file while returning valid JSON. A direct read-only tool control
confirmed include_hidden=true returns it; null/false do not. No product repair.

The neutral batch review (198.2s) fully validated only the correct-run and incomplete-
run controls (2 of 5 cases). It attributed a network event to the wrong case, missed
the actual network case, and substituted expected-answer data for the captured
response in the held-out case. Raw JSON alone was not trustworthy triage.
Separate original-prompt checks did not establish reliable isolated review either:
the formatting case was falsely called compliant (57.9s, review itself Markdown);
the network-only case recognized the right call but flattened the finding schema
(134.1s, rejected). Useful content and mechanical acceptance remain separate.

The runnable `evals/chad_discovery_validation.py` preserves synthetic controls,
held-out execution, independent grades, reviewer claims, provenance and safe handoff.
It does not dispatch repair. Wrong-JSON values now use a captured-field predicate
rather than asking a model to recopy response text; the original rejected output
is preserved. A single targeted check of this interface is recorded separately.
That targeted check (168.1s) named the missing hidden file but emitted contradictory
verdicts, duplicate JSON keys and an unsupported incomplete-turn finding for a
recovered intermediate tool error. It was rejected. This is useful content with
untrustworthy protocol/decision output, not successful machine triage. No further
inference was used to chase a green result. Original captures and separate content
adjudication are retained in `.agent/experiments/chad-discovery-validation/`.

The bounded audit has finished its intended replay, held-out execution, controlled
review, isolation checks and targeted evidence-interface check. It demonstrates
the value of independent detection and exposes the current self-review limit; it
does not certify autonomous general discovery or a reliable local reviewer.

### Phase 5 — Optional repair and before/after proof — DEFERRED

Only after useful identification, optionally hand an independently reproduced issue
to Codex/cloud or a local coder. Any candidate patch still needs scope/check policy,
matched before/after full dev-Brain replay, reset fixtures, nearby legitimate research/
coding regressions and human review. Keep original traces and effective persona deltas.
Skipping this phase does not make the core self-eval result incomplete.

## Keep scope under control

- No broad model comparisons in this workstream; prioritize concrete bug discovery.
- No new live repair loop while diagnosis/check policy is incomplete.
- Use bounded attempts with saved evidence; do not increase budgets indefinitely.
- Start the worktree Brain only for live behavior replay, on 8788; never restart 8767.
- Check actual initial residency rather than assuming an empty machine. Record changes;
  avoid silently selecting a permanent supervisor or evicting a user's active task.
- Update this plan as milestones finish; use the detailed verification-gates document
  for evidence and design limitations.
- Checkpoint commits and progress reports are intermediate work, not implicit stops.
  Continue the next authorized bounded check without waiting for another "continue".
  Finish the planned validation and handoff; do not inflate inference budgets to
  manufacture a green reviewer result.

## Milestone 2 blockers and bounded follow-up

1. Gemma's structured-schema request consumed its output budget as reasoning without
   returning content. A bounded retry requesting `enable_thinking=False` still reported
   almost all tokens as reasoning; the setting was not effective for that attempt.
   This was a completion/control failure, not proof that Gemma's factual reasoning
   was wrong. Follow-up: the OpenAI-compatible endpoint rejects `reasoning_effort=off`
   and accepts `none` (native API terminology differs). A tiny schema smoke check
   with `none` completed with zero reported reasoning tokens. Then one saved-trace
   attempt using `response_format=json_schema`, `reasoning_effort=none`, temperature
   0.6 and an unchanged 1,800-token cap completed in 6.781s: 217 completion tokens,
   zero reasoning tokens, finish_reason=stop, valid exact web_search reference.
   Recommendation was investigate; no coding dispatch. This verifies the direct
   transport for this sample, not the full supervisor or a causal diagnosis.
   A conflicting-prompt control also passed: despite a request for NOT_JSON, the
   endpoint emitted the schema-only random marker object, with zero reasoning
   tokens (3.547s). The marker existed only in the transport schema.
2. `PromptRegistry` resolves ignored `config/persona/<id>.md` overlays before tracked
   `prompts/persona/<id>.md`. Existing patch capture uses git diff and nonignored new
   files, so it cannot capture an ignored overlay-only repair. Before any prompt A/B,
   snapshot the explicitly allowlisted effective overlays, validate hashes and capture
   their changes independently. Implemented in `evals/chad_persona_capture.py` and
   integrated into the driver through explicit `persona_capture_ids`. Prompt-policy
   paths require capture IDs; a tracked-template-only scope shadowed by an overlay
   is rejected. Local reports retain selected files' original bytes, hashes and
   effective scope/body hashes; other overlay files contribute only hashes.
   Drift, clone mismatch and unallowlisted overlay changes fail closed. Capture
   failures retain the clone. Do not broadly commit or expose personal overlays.

Local evidence: `gemma-control-smoke.json` (rejected off),
`gemma-control-smoke-none.json`, `gemma-structured-controlled.json`, and
`gemma-control-conflicting-schema.json`, `persona-capture-baseline.json` under
`.agent/experiments/chad-code-fix/`. Focused offline validation: 91 passed,
1 skipped (Windows symlink privilege unavailable).
Both selected active overlays (`rich_output`, `supervisor_routing`) matched the
local persona resolver's effective text/scope hashes. No personal prompt text was
added to tracked files. At that controls checkpoint, Gemma was already loaded and
remained at 32K context, without a load/unload or persistent setting change. Later
full-Brain phases used the configured LFM supervisor and recorded actual residency.

Current outcome: the four core phases have produced runnable detection and useful
handoff evidence, while Phase 4 has exposed unreliable model review. Do not call
the reviewer trusted or automatically accept its claims. Independent contract
checks and per-case evidence remain authoritative; optional repair stays deferred.
Independently inspect effective sources when it would discriminate causes;
do not make source speculation a prerequisite for reporting an observed failure.
The saved check's historical tracked-template policy does not authorize active-overlay
edits. Do not dispatch Qwen from the investigate result. Behavioral tests use full
dev Brain on 8788; the legacy in-process cycle remains an unproven live A/B path.
