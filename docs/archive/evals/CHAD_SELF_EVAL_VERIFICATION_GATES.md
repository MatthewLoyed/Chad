# Self-eval driver verification gates

Phase 4D.1 follow-up: actual dev-process source/config audit and matching candidate
fetch reproduced the previously unexplained scores. At 0.50 both distracting Drive
documents are absent from the actual initial LFM input, with unchanged system/persona
text. First action changed; the run deliberately stopped at that boundary. Accept
the retrieval-removal gate only. Meet/code/routine validity and full task completion
remain unproven. See [runtime evidence](CHAD_RELEVANCE_CUTOFF_TEST.md). Phase 4D.2
reviews this saved evidence with the existing staged reviewer; optional repair is
not required for a valid identification/handoff.

Canonical-reference follow-up: the same saved task replay through isolated Qwen
produced 11 valid call/result references, no legacy index citations and no paired
name mismatches. Joined results require exact start-sequence alignment and unique
pending-call attribution. Ambiguous results stay unassigned. The model's unregistered
`round-1` label is flagged separately; whole-report trust does not follow from valid
tool citations. See the [isolated replay](CHAD_MEET_PRISM_EVAL.md).

Phase 4D has exercised an actual Meet/Gmail/Prism task and an explicitly pinned,
tool-free Qwen reviewer. The harness now preserves timed-out rounds and uses task
progress for idle detection. A context-packaging failure was corrected offline;
the reviewer completed with no tool calls. Its event/tool-index confusion fails
the evidence-reference trust boundary. Completion of inference is not acceptance
of diagnosis. See [real-task test](CHAD_MEET_PRISM_EVAL.md).

Phase 4B reusable regression/inbox evidence and limits are recorded in
[CHAD_REGRESSION_INBOX.md](CHAD_REGRESSION_INBOX.md). Independent grading found
three passing tasks and one failed task in the four-case dev-8788 sample, with
two separate symptoms. The model-review trust gate remains failed. Grader repair
and offline adjudication are preserved separately from the original raw capture;
an unrequested memory-save call is an attempt, not proof of persisted state.

## Identification-first priority and separate outcomes (2026-10-19)

Testing, bug identification, factual analysis and useful triage are the primary
deliverable. A human may use Codex or another cloud model to fix the result. Repair
scope/check policy and `ready_for_review` govern optional repair only; they must not
be prerequisites for reporting an observed failure. See the [four core phases and
optional repair phase](CHAD_SELF_EVAL_NEXT_STEPS.md).

`evals/chad_identification_probe.py` runs bounded full-Brain detection on local 8788,
with one positive control and two reset-fixture listing trials. It never invokes the
repair dispatcher. Raw local reports and privacy-conscious Markdown handoffs preserve
the distinction between detection and model analysis. `--self-review-input` performs
one prompted full-Brain review of an independently identified completed failure.

- `findings_identified`: an independent contract found at least one completed-turn
  failure. This is useful without a cause or fix.
- `no_failure_observed`: these completed trials did not expose a violation; it does
  not close historical bugs or prove general correctness.
- `inconclusive`: blocked/incomplete infrastructure or provenance drift; retained
  completed-trial findings must be read separately.
- `evidence_valid_review`: self-review obeyed its no-tools/investigate contract,
  passed schema/exact-reference validation and cited relevant failure evidence.
  Hypothesis prose remains unverified; this is not semantic certification.
- `review_needs_work`: malformed, irrelevant or out-of-scope model review. Its
  failure does not invalidate the operator-owned behavioral finding.

Initial proof: positive control passed; listing output contract failed once and
passed once; fixtures unchanged; no forbidden network-tool attempt recorded in the
new pair. One 138.1s prompted Chad review correctly identified the format error with
valid raw JSON and no calls. An additional hypothesis was limited by omitted tool
results/fixture evidence in the initial review prompt. That input gap was corrected
and tested; the Phase 4 section below records the subsequent enriched replay. Full evidence and
scope limits are in [the findings checkpoint](CHAD_SELF_EVAL_FINDINGS.md).

### Phase 4 controls and observed trust boundary

`evals/chad_discovery_validation.py` supplies a neutral review protocol with explicit
`no_failure`, `failure` and `inconclusive` verdicts. Known-good, formatting, forbidden
network-attempt and incomplete-execution controls are synthetic trace data; expected
grades are withheld from the reviewer. A separate actual full-Brain held-out listing
case tests hidden names, spaces, Unicode and exclusion of subdirectory contents.
Directory/child manifests remain independent state evidence, not response filenames.

Independent assessment checks every case, verdict, predicate and exact tool index/name,
reports false-positive and missed finding kinds, rejects case omissions/duplicates,
and preserves infrastructure errors separately. Valid-JSON value mismatch evidence
can refer directly to the captured response field (`response_value_mismatch`);
recopied expected answers and invented references cannot count as actual evidence.
Model hypothesis prose remains unverified. Synthetic guard tests are not model-skill
results. Safe handoff generation excludes arbitrary response prose and personal overlays.

The held-out execution omitted the explicitly requested hidden file. A read-only
tool control verified the existing hidden-file flag works. The batch review passed
only 2 of 5 complete case validations, with a cross-case network attribution error,
a missed network violation and rewritten expected-answer evidence. Isolated reviews
still failed strict protocol; one additionally called fenced JSON compliant. A final
field-reference review named the missing file but returned contradictory duplicate
verdicts and confused a recovered tool error with incomplete execution. All original
outputs were retained and rejected rather than normalized into passes.

The listing grader now rejects duplicate JSON keys and nonstandard constants, and
findings group formatting separately from valid-JSON answer-value mismatch. This
corrects detector false negatives/grouping; it does not repair Chad's behavior.
The bounded Phase 4 audit is complete with `discovery_needs_work`, not reviewer
certification or repair readiness. No product repair, broad model comparison or
production 8767 restart was performed. See the linked findings for exact evidence.

The older repair driver and its readiness decisions below remain separate. No
production 8767 restart or speculative product repair is part of this checkpoint.

The developer driver `evals/chad_on_chad_driver.py` now fails closed at stage
boundaries. These changes strengthen the existing loop rather than introduce a
second orchestrator. They have offline test coverage; a live behavioral cycle has
not yet validated the new gates.

## Current decisions

- `inconclusive`: infrastructure failure, unknown worker state, missing independent
  evidence, or a baseline that did not demonstrably fail.
- `needs_work`: terminal coding failure, no patch, failed replay acceptance, or
  failed regression acceptance.
- `ready_for_review`: an independently supplied verifier explicitly reports
  `baseline_passed=False`, `after_passed=True`, `regressions_passed=True`, and
  `passed=True`. This means ready for human review, not permission to merge.

The optional trusted Python `verifier(clone_path, case, before, after)` callback
must perform the real acceptance/regression checks and return those booleans plus
supporting evidence. It is operator code, not a model-provided command or response.
The CLI currently supplies no verifier, so it cannot label a patch ready for review.
The model judge remains advisory and cannot override failed or missing checks.

## First registered behavioral contract

`structured-answer-local` uses operator-owned `structured-answer-v1` in
`evals/chad_behavior_contracts.py`: count/sum supplied integers and return exact JSON.
Nearby regressions extract supplied fields and preserve an explicitly unknown count
as JSON null. JSON key sets, values and value types must match; Markdown wrappers,
extra fields, guessed values and booleans in place of integers fail. No external
accounts, fixture files or tool side effects are required.

The driver checks the main baseline before diagnosis: a pass returns `already_passes`
without coding. On failure, both regression baselines must pass before repair. The
main prompt and both regression prompts are replayed against the edited clone;
all returned traces and objective checks are persisted. These are a small response
contract, not broad reasoning, tool-use or system-integration coverage.

Offline validation includes synthetic complete cycles with matched baseline/replay
prompts, refusal to code on a passing baseline, and refusal to code after a regression
baseline infrastructure failure. Synthetic cycles validate orchestration, not model
competence. The combined coding/self-eval suite passes 74 tests and 12 subtests.

### Bounded live baseline observation (2026-10-19)

A real fresh-process Chad baseline was attempted with a 180-second cap. It returned
no completed trace before the deadline; acceptance was `passed=null` (inconclusive),
and no coding repair was dispatched. Logs reported missing MCP executables and
approximately 57 seconds of pre-model setup, including about 43 seconds of tool
preparation, for this supplied-data/no-tools-needed prompt. These observations
identify environment/efficiency work, not evidence of an incorrect task answer.
The full local diagnostics are saved in
`.agent/experiments/chad-code-fix/behavior-contract-baseline.json`.

Before extending this into an automatic live loop, resolve the eval startup path,
record matched model settings, and use explicit per-stage budgets. Do not label a
startup timeout a behavioral failure or ask Chad Code to fix the arithmetic prompt.

### Full dev Brain baseline succeeded (2026-10-19)

Following the maintainer's normal workflow, started the worktree Brain using
`scripts/prod/run_dev_brain.py` on port 8788 with dev environment/secret paths and
background daemons disabled. Production port 8767 was not touched. Used the existing
`evals.live.probe_live` WebSocket path with the exact same sum prompt.

Result: `{"count": 3, "total": 54}`, exact acceptance passed, live duration 68.6s.
Server logs confirmed supervisor invocation and completion; LM Studio confirmed the
LFM streamed response completed. Local evidence:
`.agent/experiments/chad-code-fix/live-brain-baseline.json` and dev Brain logs.
No repair was dispatched: this prompt already passes.

This establishes the full dev Brain as a working baseline transport on this machine.
It does not isolate why the in-process attempts timed out; environments and startup
state differed. Future behavioral self-evals should use the proven dev-server path
and match baseline/patched environments before claiming an A/B improvement.

Additional reproducible launcher fixes: process-local discovery of installed Node,
Git and Docker when desktop PATH omits them; the self-eval PowerShell launcher now
uses its own checkout and passes case arguments via environment rather than embedding
them in Python code. These fixes do not disable tools or alter product routing.

### LFM live behavior and triage findings (2026-10-19)

LFM2.5-24B-A2B is Chad's active supervisor; Qwen35B is the separate OpenHands coder.
The live extraction and unknown-value regressions passed in 24.0s and 21.2s.
No Qwen35B coding repair was dispatched during these tests.

A read-only local listing case used a temporary synthetic folder with random names
not disclosed in its prompt. The first turn called list_directory on the correct
folder and returned the correct names, but wrapped the JSON in Markdown (31.9s).
An identical prompt against the same fixture returned raw correct JSON (91.4s),
but attempted web_search despite the explicit no-network constraint, read both
files unnecessarily, and delegated JSON formatting to code_agent. That specialist
uses the product's Qwen2.5-Coder14B, not the OpenHands Qwen35B worker. Both fixtures
remained unchanged. Separate answer, scope, tool-evidence and fixture-state checks
prevent the correct final answer from concealing forbidden recorded network use.

The listing evaluator checks recorded tool attempts, not an exhaustive network
audit. The two trials establish observed failures, not stable failure rates or a
single proven root cause. File reading/delegation is an efficiency concern here;
the objectively forbidden action was the recorded network-tool attempt.

LFM then analyzed the supplied traces without tools (137.7s). Its diagnosis correctly
noted networking and formatting, but falsely claimed the second trace lacked file
reads and coding delegation, speculated that identical prompts came from different
workflows, and proposed mandatory file-content reads for a filename-listing task.
The operator rejected this diagnosis; it was not handed to the coder. This is a
triage-accuracy failure, not evidence that a patch solved the behavior.

Local artifacts: `live-lfm-nearby.json`, `live-lfm-directory.json`,
`live-lfm-directory-repeat.json`, `live-lfm-directory-independent-grades.json`, and
`live-lfm-directory-diagnosis.json` under `.agent/experiments/chad-code-fix/`.
Next priority: evidence-linked structured triage and contradiction rejection before
coding dispatch. Do not add the speculative diagnosis's read-file requirement or
weaken exact-output grading to hide these failures.

### Quick Qwen3.6-35B diagnosis comparison

Qwen3.6-35B-A3B Q4_K_M received the exact saved diagnosis user prompt and trace
evidence through LM Studio directly (temperature 0.6, 2,200-token cap, 54,272 context).
It completed naturally after 134.3s generation plus 30.4s loading. It correctly
identified the Markdown wrapper, forbidden web-search attempt and unnecessary reads/
delegation; it did not repeat LFM's denial of recorded calls or invented workflow
divergence. It labeled causal explanations as hypotheses and proposed constrained
format/tool-use changes rather than mandatory file reads. Its 0.9 confidence is
self-reported, not calibrated, and its strict list_directory-only proposal still
needs review before applying it as a general tool policy.

It also returned a Markdown-fenced diagnosis despite the concise-JSON request.
This is promising evidence for using Qwen as a triage candidate, not proof of a
production-ready diagnosis role. This direct-model invocation omitted Chad's
supervisor persona/tools, so it is not a matched full-harness model comparison.
No repair was dispatched and no supervisor configuration changed. LFM was restored
afterward. Full result: `.agent/experiments/chad-code-fix/qwen-direct-diagnosis.json`.

### Quick Gemma4-26B-A4B diagnosis comparison

Used downloaded `google/gemma-4-26b-a4b-qat` Q4_0 directly, same saved user prompt,
temperature 0.6, 32K loaded context. First attempt exhausted the matched 2,200-token
budget: 2,197 reasoning tokens, empty final content, finish_reason=length, 60.8s
generation plus 20.4s load. This is an incomplete answer, not a factual verdict.

A separate 4,400-token-cap follow-up completed naturally in 41.7s generation plus
20.1s load. Gemma accurately identified Markdown wrapping and forbidden web_search,
did not deny recorded calls or invent a requirement to read file contents, and
proposed constraint enforcement with normal research/coding regression checks.
It still wrapped the requested JSON in Markdown and used uncalibrated "High"
confidence for a suspected model/planning cause that the traces do not establish.

This is promising triage evidence alongside Qwen, not a matched speed/quality
ranking: caps and loaded context differed, runs are stochastic, and both direct
tests omitted Chad's supervisor persona/tools. The second run's shorter generation
cannot be attributed solely to increasing the cap. No patch or model-role change
was made; LFM was restored. Artifacts: `gemma-direct-diagnosis.json` and
`gemma-direct-diagnosis-expanded.json` in the ignored experiment folder.

## Stage and evidence handling

The baseline must run from a clean checkout at the requested revision. The driver
checks again after diagnosis and provisions a uniquely named clone at that revision.
Trace infrastructure errors and empty diagnoses stop repair dispatch. Unknown,
running, timed-out or unsuccessful job checks stop replay and retain the clone,
because a worker may still be writing. Finished jobs require exit zero and a real
patch before replay. Replay process failure is an infrastructure error even when
stdout contains a trace marker.

Every run has a unique ID, stage, decision and persisted report, including failures
before provisioning. Full returned traces remain in the report; model prompts use
bounded excerpts and diagnosis labels that explicitly. Tracked binary diffs and
untracked file contents are captured before cleanup so the proposed repair survives
clone removal. Untracked symlinks/path escapes are rejected. If patch capture fails,
the clone is retained. Cleanup errors are recorded and do not suppress the report.
Provisioning secret/environment grants are excluded from clone metadata in reports.
Trace content can still contain task data; reports remain local development artifacts.

## Remaining work before a reliable multi-prompt loop

1. Define one trusted case-specific acceptance verifier and nearby regression cases,
   including resettable input fixtures and matched effective model/settings.
2. Check baseline acceptance before paying for diagnosis/repair. Current readiness
   evaluation occurs after replay; it prevents a false readiness claim but does not
   yet avoid every unnecessary repair attempt.
3. Validate structured triage: the initial evidence/scope gate below is implemented.
   Case-specific policy, live structured-model validation and causal reproduction
   remain necessary; schema-valid output is not proof of a sound diagnosis.
4. Run one dev-only behavioral case through these gates with human patch review.
   Then add repetitions, held-out prompts, failure grouping and a user-facing A/B report.
5. Make baseline and replay environments symmetric, including state reset and
   explicit model-residency coordination. Current baseline is in-process and replay
   is a fresh process; this is not yet a controlled statistical A/B experiment.

Do not automatically deploy or merge based on model judgment or one successful replay.
This remains a maintainer-only tool under `evals/SELF_EVAL_DO_NOT_SHIP.md`.

## Structured triage gate implemented

`evals/chad_triage_gate.py` validates an exact raw-JSON schema before the driver
provisions a clone or calls Chad Code. Observations must be mechanically checkable:
an exact tool name at a zero-based tool-call index, the exact captured response, or
a captured response that fails strict JSON parsing. Unknown facts, extra prose,
unsupported absence claims, missing references and mismatched event names fail
closed. Duplicate JSON keys and nonstandard constants are rejected.

Causes are explicitly unverified hypotheses referencing valid observation IDs.
Confidence scores cannot authorize anything. A repair recommendation must match an
operator-owned failure signal and provide allowed repository-relative files, every
required acceptance/regression check ID, and concrete operator definitions of those
checks. Missing policy returns no coding authorization. An investigate recommendation
can be valid without authorizing repair. Scope validation checks proposed scope;
it does not sandbox the coder or replace subsequent patch review.

The driver saves the gate result and blocks dispatch on rejection. An accepted
handoff is generated from verified observations, clearly labeled hypotheses and
operator policy, rather than raw model diagnosis prose. The coder is told to
reproduce before editing and report a blocker if the cause is wrong. Existing
candidates without explicit triage policy intentionally cannot auto-dispatch.
No case-specific repair policy has been enabled for the live listing failure yet.

Tests exercise exact evidence, contradicted/missing references, unsupported claim
types, irrelevant true facts, traversal/unapproved paths, missing regression checks,
missing check definitions, infrastructure failures, and driver non-dispatch paths.
All three saved free-form diagnoses were rejected for the old format; this is a
schema/scope check, not a semantic ranking of the models.

Limits: this validates references and scope, not free-text causal truth. A hypothesis
can still be wrong; a cited fact may be causally irrelevant even when it matches a
policy signal. Independently executed reproduction, replay/regression checks and
review remain the authority. The live dev-Brain transport is still operator-driven;
this change does not switch Gemma/Qwen into Chad's supervisor role or make the old
in-process full-cycle path a proven live A/B workflow.

Next bounded milestone: define a listing-case operator policy and check definitions,
request this schema from Gemma, and validate its actual output. Only then consider
a reproduction-first Qwen coding handoff. No new model inference or repair dispatch
was performed while implementing this gate.

### Structured Gemma validation, morning 2026-10-19

The gate was committed (`1f38bf45`) with 103 tests and 12 subtests passing.
Gemma4-26B-A4B received the actual schema, exact saved listing trace and explicit
operator policy. No source code was supplied; the prompt required investigate when
source inspection was necessary. At 4,400 tokens it returned empty final content,
finish_reason=length (94.3s generation). A single targeted retry requested minimal
JSON, `chat_template_kwargs.enable_thinking=False`, and 1,800 tokens. It again
returned empty content, finish_reason=length (36.3s), with 1,797 reasoning tokens.
The requested reasoning control was therefore not effective for this attempt;
do not claim thinking was disabled. Both gate results rejected dispatch.

Artifacts: `gemma-structured-triage.json` and `gemma-structured-triage-retry.json` in
the local ignored experiment directory. No Brain, coding worker or repair was
started. LM Studio initially had no loaded model; Gemma remains loaded after this
check, without changing persistent supervisor configuration.

Source inspection independently confirmed `config/persona` overlays win per ID
(`prompt_registry.py` / `persona_store.py`). Git-based patch capture currently omits
ignored overlay edits. This must be fixed through explicit overlay snapshots and
capture before a prompt-only repair can have attributable before/after evidence.
The sample policy's tracked template paths alone are not an approved live repair
scope while an overlay is active. See the focused next-steps plan for blockers.

### Effective transport controls and persona capture, follow-up 2026-10-19

The installed LM Studio OpenAI-compatible endpoint accepts
`reasoning_effort="none"`; `"off"` returns HTTP 400 with its accepted value list.
The native `/api/v1/chat` interface advertises `off/on`, so those values must not
be copied between APIs. The previous `chat_template_kwargs` retry did not verify
effective control. No persistent template or supervisor settings were changed.

Added `evals/chad_structured_triage_probe.py` and a real JSON Schema transport
definition alongside the triage gate. The probe requires an already-loaded instance,
sends `response_format=json_schema` plus `reasoning_effort=none`, uses a fixed
1,800-token cap and 120-second request timeout, records exact request/response,
latency and residency, and does not load models, retry, repair output or dispatch.
It requires nonempty final content, normal stop, no tool calls, zero reported
reasoning tokens, empty reasoning content, and successful independent gate validation.
Unknown reasoning usage does not establish successful reasoning control.

A tiny schema smoke check returned exact JSON with zero reasoning tokens. One
subsequent saved-listing-trace triage request completed in 6.781s with 217 completion
tokens, zero reasoning tokens and finish_reason=stop. It cited web_search exactly
at tool-call index 1 and recommended investigate. The gate was valid and withheld
dispatch. Its proposed repair lists omitted required checks and referred only to a
tracked template; this investigate result is not an approved repair brief or a
verified cause. Schema completion here does not establish broader triage quality,
full-Brain behavior, or a speed comparison against the previous stochastic runs.
A separate conflicting-prompt control requested literal NOT_JSON while the
transport schema required a random marker known only to that schema. The endpoint
returned exactly the schema marker object, stopped normally, and reported zero
reasoning tokens (3.547s). This provides observed schema-enforcement evidence beyond
merely prompting the model to format JSON.

`evals/chad_persona_capture.py` snapshots explicitly selected IDs, preserving raw
baseline/overlay bytes and SHA-256 values plus effective source, scope and body hash.
It follows empty-overlay fallback and scope inheritance. Unselected overlay contents
are not saved; an inventory of file hashes detects their changes. Path links/escapes,
invalid IDs, unsupported layer/overlay redirection, tampered snapshots, source drift,
clone mismatch and out-of-allowlist overlay edits fail closed. The current supported
capture environment is the default merged layer with checkout-local overlays.

The driver requires explicit `persona_capture_ids` for persona repair scopes,
checks that the policy covers the effective overlay when a template is shadowed,
verifies source/clone inputs before dispatch, and includes independent persona deltas
in patch capture and its hash. An ignored overlay-only edit now counts as a real
patch, including additions, deletions and empty-file fallback. Capture failure retains
the clone and report. Selected prompt bytes remain in local development reports;
they are not suitable for public upload. Provisioning's existing full persona copy
is unchanged; this capture allowlist is not a coder filesystem sandbox.

Local resolver checks for rich_output and supervisor_routing matched snapshot
scope/body hashes; both effective sources were ignored config/persona overlays.
These are disk-input checks, not attestation of a running PromptRegistry's cache.
Behavioral A/B still requires a freshly warmed full dev Brain on 8788 and matched
environments. No behavior replay, coding worker, repair, Brain restart or production
8767 action occurred during this milestone. Gemma remained the only loaded model
at 32K context throughout the successful check.

Evidence under `.agent/experiments/chad-code-fix/`: `gemma-control-smoke.json`,
`gemma-control-smoke-none.json`, `gemma-structured-controlled.json`, and
`gemma-control-conflicting-schema.json`, `persona-capture-baseline.json`.
Focused offline validation: 91 passed, 1 skipped
(Windows symlink privilege unavailable), including real git ignored-overlay capture
and driver non-dispatch/retention cases, plus a real provisioned-clone hash match.

API references: [structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output)
and [native reasoning controls](https://lmstudio.ai/docs/developer/rest/chat).
Endpoint-specific accepted values were verified against the installed server.
