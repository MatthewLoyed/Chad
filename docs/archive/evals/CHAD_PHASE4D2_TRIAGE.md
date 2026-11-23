# Phase 4D.2 — evaluator context and saved-decision triage

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

2026-10-19. Identification-first review from `76ef6f35`; no product repair or
external task replay. Production 8767 was untouched.

## Evaluator input changes

`evals/chad_evidence_review.py` now supplies a source-grounded architecture overview:
automatic graph/activity/Drive/session injection; optional context versus user
instructions; Drive locations versus bodies; per-turn selection/core tools/bundles
versus the supervisor's chosen calls; specialist tools/model pins; saved workflows
versus generic capabilities; and disconnect/capture limits. Architecture describes
possible mechanisms, not attestation of a particular run.

Optional `review_context` carries private per-case evidence within the same token
budget. `evals/chad_decision_review.py` packages the saved before/after requests,
system hashes, executed request, source hashes, explicit call ranges and stop
boundaries. Short traces can retain up to 4,000 characters per parameter/result
excerpt; larger traces fall back through the existing bounded caps. Oversized
context fails closed rather than silently dropping supplied evidence. No tools or
execution dispatcher are available to the reviewer.

The explicit CLI requires Qwen already resident; load through `model_lifecycle`,
never heavy-model JIT. Use `--package-only` for an offline check. Keep outputs private:

```powershell
.venv/Scripts/python.exe -m evals.chad_decision_review --evidence-dir .agent/experiments/chad-relevance-cutoff --output .agent/experiments/chad-relevance-cutoff/new-review-package.json --package-only
```

## Independent behavioral triage

- **Before, call-0000:** traversal asks for local DraftOut/code files and something
  to email Jordan, including home-directory exploration. This evidences diversion to
  existing file content rather than binding email to the newly created Meet artifact.
  It does not require a tool result to identify the wrong delegated objective.
- **After, call-0001:** Draftout/Prism web search replaces initial file traversal.
  This is changed intent, not evidence of app launch or correct Meet-code resolution.
- **After, call-0002:** `run_saved_routine(routine_name=gmail_draft)` has no supplied
  existence/discovery evidence. Its schema describes running an existing user-named
  workflow. Treat this as a questionable capability/routine guess requiring triage;
  no result was captured, so do not report not_found or successful draft creation.
- **Retrieval:** runtime audit and actual prompts establish removal of two unrelated
  Drive locations at 0.50. Graph/activity/session context remains. This does not
  establish general relevance quality or prove retrieval caused the earlier error.
- **Outcome boundary:** all three canonical calls have `not_captured` results.
  Both executions intentionally stopped at the first decision with confirmed
  cancellation. Meet creation, recipient verification, draft payload, launch and
  absence of side effects remain unverified. These cases are separate executions.

Useful handoff: preserve dependency/artifact identity before delegation; investigate
why a workflow name was substituted for Gmail capability discovery; obtain actual
bound schemas and tool results before assigning fault to selector or executor.
No generic Meet/Prism tool names are established by these saved snapshots.

## Qwen replay and operator adjudication

One tool-free pinned `qwen/qwen3.6-35b-a3b` replay completed in 109.06s, normal stop,
3,811 prompt / 1,478 completion tokens, zero reported reasoning tokens. Mechanical
reference check passed: four citations, three distinct calls, no invalid IDs or
paired tool-name mismatches. Raw request/response retained privately.

It recognized the file-code interpretation drift, explained saved-routine semantics
and retained missing-result/early-stop uncertainty. Semantic acceptance remains
**failed**:

- Its comparison narrated the before traversal and after routine as if successive
  actions in one run. Canonical IDs alone do not prevent cross-case attribution.
- It did not substantively analyze the verified Drive removal despite receiving the
  architecture overview, actual context and operator audit. More context alone has
  not established informed causal triage.
- It suggested checking named Meet/Prism APIs absent from the supplied schemas.
  A needed capability must not become an invented tool name.
- It treated a retrieved contact assertion as supporting unambiguous identification,
  stronger than independent identity verification warrants.
- It recommended a full rerun as minimal reproduction when saved action intent
  already supports identification; execution/state evidence is a separate check.

After this replay the prompt explicitly prohibits narrating separate cases as one
sequence, requires assessing retrieval evidence, distinguishes retrieved contacts
from verified identity, and forbids invented API names. Those follow-up instructions
have offline coverage only; no second inference was used to chase a passing review.

### Prompt provenance correction

The replay received `first-supervisor-messages.json` for the before context, an older
0.42 run's snapshot, together with `runtime-first-decision.json` from the later
runtime audit. The submitted context disclosed lack of request correlation IDs but
did not identify this earlier-run pairing explicitly. Do not call it exact pairing.

Read-only dump inspection recovered `runtime-supervisor-messages.json` at 13:21:01
within the runtime capture's 13:20:33–13:22:33 window. It is the latest matching
initial LFM request in that window in the retained dump. System text is identical;
the four differing lines are in upcoming Calendar context. The retrieved Drive
block is unchanged. Dump headers are time-only, so window/order matching is still
weaker than a request correlation ID. Raw earlier evidence is preserved.

The packager now requires the corrected runtime snapshot. The corrected offline
package is saved separately and was not the input to the completed Qwen replay.

## Initial Bonsai preparation — later superseded by live test

The following records the initial blocked attempt. The user subsequently instructed
running Bonsai, the identity-checked dev restart succeeded, and the original task
was tested. See [Bonsai task evidence and resume fix](CHAD_BONSAI_TASK_TEST.md).

The user requested Bonsai for the next task test. Private runner files are ready:
`start_bonsai_dev_brain.py` applies process-only configuration overrides before
normal audited full-dev startup; `replay_bonsai.py` runs the exact historical
draft-only prompt through normal 8788 tools/MCPs. One interactive workflow round,
600-second wall backstop, existing inactivity capture, no canned clarification
answers, persona/provenance capture, and disconnect cancellation verification.
Adapter-entry observations retain tool schemas so selection can be distinguished
from chosen actions. They do not attest downstream adapter transformations.

**Blocked:** automatic approval review rejected the identity-checked command to
stop PID 33232 on dev 8788 and launch the Bonsai wrapper, reporting only "blocked
by policy." No stop/start occurred. No persistent model preferences changed.
The existing dev Brain remains the historical LFM process. Alternative model-set
APIs inspected persist preferences; they were not used. No process-stop workaround
was attempted. A permitted dev restart is required before this behavioral test.

Qwen residency was explicitly released; initial residency restoration is recorded
separately. Re-query runtime/listener identity before resuming; historical PIDs are
not authorization to stop a future process. Never restart production 8767.

## Verification and private artifacts

16 focused tests pass: evidence packaging/reference integrity, attribution,
long-objective retention, context budgeting, cancellation/prompt mismatch rejection,
and result coalescing. No claim of behavioral improvement from offline tests.

Under `.agent/experiments/chad-relevance-cutoff/`:
`phase4d2-qwen-review.json`, `phase4d2-package.json` (original input),
`phase4d2-package-corrected.json`, `runtime-supervisor-messages.json`,
`phase4d2-residency-restored.json`, and the prepared Bonsai scripts.
Raw prompts, contact/document data, model responses and browser snapshots remain
private; this tracked handoff intentionally omits their identifying content.
