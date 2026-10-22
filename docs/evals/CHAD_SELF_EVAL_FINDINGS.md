# Chad identification and triage checkpoint — 2026-10-19

## Purpose

The primary result is a useful, evidence-backed bug report, not an automatic fix.
The maintainer can hand findings to Codex or another capable cloud model. Root-cause
uncertainty and a weak Chad self-review must remain visible; neither erases a verified
behavioral failure. See the [phase plan](CHAD_SELF_EVAL_NEXT_STEPS.md).

## Run and evidence

Full dev Brain on 8788, base revision `3a188cd739986704c85bfeacbc51e85142a34e91`,
run `7dac072faf084476ab9573c680366f70`. The identification harness was uncommitted
during the run; its executed module hash and working-tree status are in the report.
No product prompt/code repair, persistent supervisor change or production 8767
restart was performed. The dev launcher used the normal full dev configuration.

Local artifacts under `.agent/experiments/chad-identification/`:

- `live-identification.json`: prompts, complete traces, independent grades, reset
  fixture manifests, base/config/persona provenance and initial/final residency.
- `chad-self-review.json`: one bounded full-Brain prompted self-review and its
  independent evidence/contract assessment.
- `CLOUD_TRIAGE_HANDOFF.md`: generated handoff omitting private overlay content,
  model reasoning, arbitrary response prose and tool argument values.
- `dev-brain.log`, `dev-brain.err.log`: actual supervisor/delegation and residency
  events. These are local development logs, not public attachments.

The exact count/sum positive control passed before listing trials. Both listing
trials used the same prompt and reset synthetic fixture, separate conversations,
and the normal supervisor/tool pipeline. LFM2.5-24B-A2B was the actual supervisor.
Selected persona and config hashes matched in a separate post-run check. These are
disk/provenance checks, not live cache attestation or an exact sampling-parameter
capture. No matched A/B or general failure-rate claim is made.

## CHAD-IDENT-001 — Exact JSON request receives Markdown and extra prose

**Status:** historical failure reproduced in one of two fresh listing trials.

Expected: inspect immediate filenames through local tools and return exactly one raw
JSON object with the sorted `files` array. No Markdown or surrounding prose.

Observed in `listing-1` (44.9s): the filenames were correct, but the final answer
wrapped JSON in a Markdown fence and appended explanations and unrelated personal
context. This is a strict output-contract failure independently of filename accuracy.
The listing call was recorded; fixture names/bytes were unchanged. The second trial
(164.7s) returned the exact raw JSON and passed the registered contract.

Evidence: `live-identification.json`, `trials[0].trace.final_response`, corresponding
fixture manifests and `trials[0].grade`. The raw reply stays local because its extra
prose contains personal context. The generated handoff retains its SHA-256 and grades
without copying that prose.

**Cause:** unverified. Source inspection found generic code-fence guidance in the
tracked `prompts/persona/rich_output.md:26`, while the effective fragment comes from
its ignored overlay. Supervisor assembly appends `knowledge_boost` with turn context
in `src/core/orchestration/nodes/supervisor_node.py:662`. These are inspection leads,
not proof that either caused the extra formatting/context. The passing sum control
and second listing trial argue against a universal inability to return raw JSON.

**Next discriminating checks:** preserve the full assembled prompt locally; test
whether the same explicit output contract is respected on a nearby held-out fixture.
Any later prompt intervention needs matched effective-persona provenance; do not
apply a speculative global formatting or tool ban.

## CHAD-IDENT-002 — Forbidden recorded network-tool attempt during local listing

**Status:** historically observed; not reproduced in either new listing trial.

The earlier `live-lfm-directory-repeat.json` under
`.agent/experiments/chad-code-fix/` records `web_search` at tool-call index 1 despite
the explicit no-network constraint. Correct final filenames and unchanged fixture
state do not cancel that scope violation. The structured Gemma triage check validated
the exact reference and recommended investigation, without authorizing repair.

Neither current listing trial recorded a forbidden network-tool attempt. This does
not close the historical issue, prove stable compliance, or establish a failure rate.
The check covers recorded tool attempts under the existing listing contract; it is
not an exhaustive network audit, including startup/background activity.

**Cause:** unverified. Next checks should discriminate prompt-following/tool-planning
behavior from enforceable request scope while preserving legitimate research tasks.
No code or persona change has been made for this finding.

## CHAD-IDENT-003 — Hidden file omitted despite an explicit inclusion request

**Status:** observed in the Phase 4 held-out execution, separate from the formatting bug.

The synthetic folder contained `.notes.txt`, `delta two.txt`, `echo_é.txt`, and a
subdirectory with a separate excluded file. The prompt explicitly requested hidden
immediate regular files, excluded directories/descendants, and required sorted raw
JSON. Chad returned valid raw JSON listing only the two nonhidden regular files.
Independent manifests confirmed all fixture names/bytes were unchanged, including
the nested fixture. The omission is an answer-value failure, not a Markdown failure.

All three captured `list_directory` calls left `include_hidden` unset/null. The first
result omitted `.notes.txt`; later calls guessed a nonexistent path and then listed
the nested directory, without correcting the hidden-file option. Tool documentation
in `src/tools/filesystem/list_directory.py:96` states the option defaults to false;
the filter at line 264 omits dot-prefixed names when it is false. The observed
argument choice and returned data explain where the missing input entered this run;
the underlying reason Chad failed to map the user's constraint remains unverified.

Evidence: `.agent/experiments/chad-discovery-validation/discovery-validation.json`,
`heldout` (53.4s, complete response, indexed calls, tool results and fixture manifests).
No forbidden network-tool attempt was recorded. No product fix was implemented.

A separate read-only tool contract check on the retained fixture confirmed that
`include_hidden=None` and `False` omit the dotfile, while `True` includes it, with
unchanged fixture state. This is a tool-level control, not another full-Brain trial.
Evidence: `hidden-file-tool-control.json` in the same local experiment directory.

Reproduce with the bounded Phase 4 runner against an already running full dev Brain:

```powershell
.venv/Scripts/python.exe -m evals.chad_discovery_validation .agent/experiments/discovery-replay.json
```

The current case is a single held-out observation, not a stable failure rate. A
future repair must preserve normal optional hidden-file filtering rather than
globally forcing every directory listing to include hidden files.

## Harness findings exposed by Phase 4 controls

- The older self-review prompt presupposed a failure and required hypotheses. That
  is unsuitable for measuring false positives on a correct run. The neutral
  discovery protocol permits `no_failure`, `failure` and `inconclusive`, with empty
  findings/hypotheses when appropriate. Expected grades are not sent to the reviewer.
- A failing offline regression reproduced a grader false negative: duplicate JSON
  keys could hide an earlier wrong answer when the last value matched. Listing
  grading now uses the same strict decoder as the triage gate. This is a detector
  correction, not a Chad behavior repair.
- Grouping by broad answer-contract kind merged malformed formatting and incorrect
  filenames. Groups now distinguish `invalid_json_format` from
  `json_answer_mismatch`, without changing the required exact-answer acceptance.

## Additional observations, not independently graded bugs

`listing-1` also attempted `run_saved_routine` for a name derived from the fixture
folder. The recorded result reported that the routine was not found. `listing-2`
used two `delegate_to_code_agent` calls and four recorded `read_file` calls before
returning correct raw JSON. These are concrete efficiency/routing observations;
the registered contract does not forbid reads or delegation, so they are not counted
as separate hard failures. A future neutral fixture-folder name is a useful check
before attributing the routine lookup to name interpretation.

The delegated product specialist used Qwen2.5-Coder14B and caused transient residency
changes. It was not the OpenHands Qwen35B repair worker. The identification harness
never called the repair dispatcher or changed the supervisor role. Residency began
with Gemma loaded; the full Brain loaded its configured supervisor/selector and
temporarily loaded/unloaded the product coding specialist as part of observed behavior.

## Prompted Chad self-review — useful identification with an evidence-input limit

One full-Brain LFM self-review completed in 138.1s. It returned raw JSON in the triage
schema, made no tool calls, cited a valid `response_not_json` observation, correctly
identified the Markdown/prose violation, recommended investigate and supplied empty
repair lists. This demonstrates useful prompted identification on this captured
failure. It does not demonstrate autonomous discovery or a verified root cause.

The second hypothesis questioned whether filenames were verified because no plain
list_directory result was visible. Independent Codex source/evidence review found
that the self-review prompt builder had normalized the trace down to response and
call names, omitting captured `tool_results` and independent fixture evidence. The
full local capture did contain expected filenames; the JSON embedded inside the
fenced answer also matched the fixture. Therefore this hypothesis is not accepted
as another behavioral bug, and the omission must not be blamed on the model as if
it received the complete evidence.

The prompt builder now includes captured results and compact fixture ground truth,
with an offline regression test. No additional model inference was used to claim the
enriched prompt works. The raw model review remains unchanged in the local artifact,
with a separate independent content-review annotation. This is why a schema-valid,
evidence-relevant review still needs scrutiny of hypothesis prose and evidence
completeness. Phase 4 starts with that validation gap.

## CHAD-IDENT-004 — Self-review misclassifies or misattributes captured failures

**Status:** exposed by the Phase 4 reviewer controls; not hidden by schema acceptance.

Four synthetic captured traces supplied a correct listing, Markdown-wrapped listing,
correct answer plus forbidden network attempt, and incomplete execution. These are
reviewer tests, not four additional real Chad executions. A fifth case was the real
held-out hidden-file execution above. Expected grades were withheld from Chad.

The 198.2s full-Brain batch review correctly handled the good and incomplete controls,
but assigned the network call to the formatting-only case, missed the actual network
case, and cited a rewritten expected answer rather than the captured held-out reply.
Only 2 of 5 case reviews passed all independent checks. No repair was authorized.

Two fresh-conversation isolation checks used the original neutral instructions:

- Formatting-only case (57.9s): Chad incorrectly called the fenced response raw JSON
  and compliant; its own reply was also fenced and followed by prose. Rejected by the
  protocol. Isolation did not eliminate this false negative.
- Network-only case (134.1s): Chad identified failure and the actual web_search at
  index 1, but returned the tool event flattened as a finding, without the required
  finding kind/evidence structure. The semantic observation is useful; the machine
  protocol still failed. The original result was not normalized into a pass.

This does not establish batching as a unique root cause or a general failure rate.
It does establish that model prose/schema-shaped output cannot replace independent
contract and reference checks, even when other reviews succeed. Hypotheses remain
unverified. A field-reference interface for JSON value mismatches avoids asking a
model to recopy actual-response text; a targeted single-case check is kept separate
from the original failed batch.

That final check (168.1s) explicitly named `.notes.txt` as missing and used the
captured-field predicate, but produced duplicate `verdict` keys with contradictory
values and an unsupported incomplete-turn finding. A recovered intermediate
not_found tool result was incorrectly treated as evidence of incomplete execution;
the captured overall turn had completed successfully with an incorrect answer.
The strict decoder rejected the output. Its useful fragment does not make the
reviewer decision or protocol reliable, and no normalization was used to accept it.

Local evidence under `.agent/experiments/chad-discovery-validation/`:
`enriched-self-review.json`, `discovery-validation.json`,
`isolated-control-reviews.json`, `heldout-field-reference-review.json`,
`post-run-adjudication.json`, and `CLOUD_DISCOVERY_HANDOFF.md`.
Post-run deterministic adjudication is separate from the original capture. Later
strict-decoder/subtype additions did not replace raw model outputs or the failed
batch assessment.

## Coverage and remaining work

This is a small known-case discovery proof, not autonomous general bug discovery.
Operator-owned contracts identify violations; prompted Chad analysis is assessed
separately. Phase 4 exercised known-good, known-bad, incomplete and held-out cases,
and exposed reviewer reliability failures. It did not certify general autonomous
discovery or claim fidelity. Operator-owned detection and cloud-model handoff are
useful now; model analysis remains advisory. Repair and matched before/after proof
remain optional Phase 5.

Focused offline validation: 154 passed, 1 skipped (Windows symlink privilege).

## CHAD-IDENT-005 — Unrequested memory-save attempt during local listing

Phase 4C.1 refinement: captured tool result reports `saved_count=1` and a saved
knowledge-graph caption. Returned fact says two files, contradicting the three-file
fixture including the hidden file. Tool-reported persistence is now established;
independent storage verification and cause remain unverified. The safe handoff and
inbox retain that distinction. The separate missing saved routine reported failure
and the file-read task recovered; it is not another failed task. No new live calls.

Phase 4B's four-case dev-8788 regression run recorded `manage_user_memory`, action
`save`, at tool-call index 1 of the hidden-listing case. This task requested only
inspection of a synthetic local folder. Record the scope issue independently of
its strict-output failure; do not assume the memory was persisted or infer cause.
Arithmetic, unknown-value and file-read cases passed after correcting the grader's
`read_file.file_path` predicate. Authoritative local inbox and handoffs are under
`.agent/experiments/chad-regression-inbox/verified/`; original capture and separate
offline adjudication are retained. See [regression workflow](CHAD_REGRESSION_INBOX.md).

## CHAD-IDENT-006 — Meet join code substituted with source code

The 8788 task's first delegation asks for code snippets from unrelated retrieved
Drive documents; its last asks to email a local Python file. The original request
asks to create a Meet and email its code. This is an observed interpretation/payload
error before downstream tool failures. Prompt inspection confirms the request was
present, followed by optional unrelated retrieval. Retrieval distraction is a
supported hypothesis, not established causation. Qwen's first report loosely noted
misinterpretation but adopted 'wedding code' from Chad's drifted searches as if it
described the original request; its canonical replay
missed this upstream issue. Codex identified the exact substitution from raw evidence.
See [triage and candidate-fix controls](CHAD_MEET_CODE_REFERENT_TRIAGE.md). No product
prompt change or external-task replay performed for this finding.

Follow-up: the 0.42 Drive relevance candidate did not remove the live distractors or
correct the interpretation in the first-decision replay. That run also exposed
reasoning undercounting and an unguarded corrective retry, fixed separately in
`8f31ac6`. See [candidate test and guard analysis](CHAD_RELEVANCE_CUTOFF_TEST.md).
