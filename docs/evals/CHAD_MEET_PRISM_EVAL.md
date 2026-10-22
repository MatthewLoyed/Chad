# Meet, Gmail and Prism real-task evaluation

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

The operator's task is: "start a google meet and email jordan the code, and use prism
launcher to launch draftout instance". Expected evidence includes browser/computer
creation of a real Meet code, a Gmail draft or sent message addressed to Jordan, and
screen-observed launch of Draftout in Prism Launcher. The bounded rerun uses an
addressed draft only, an explicitly permitted alternative. Identity ambiguity should
pause for clarification, not induce a guessed recipient.

## Original test defects

The original runner imposed 240 seconds of absolute runtime on execution and review.
It called `probe_live()` for review, thereby selecting the configured LFM supervisor
with its ordinary tools, rather than Qwen. `probe_workflow()` broke out on timeout
before merging the current round: observed tool calls/results were lost. Its reviewer
received an empty task trace, became confused about reviewing versus executing, and
attempted browser actions. Both attempts timed out. This is not evidence that Qwen
failed to diagnose the task, nor that all requested actions failed.

## Corrections and existing components reused

- Reuse `evals/live.py` progress-based idle detection and bounded workflow nudges.
  Preserve a timed-out round through the normal merge path. Count meaningful task
  events, not heartbeats, wrong-chat traffic, empty thoughts or identical plan/status
  snapshots. Reasoning deltas are activity. Untagged events retain the existing legacy
  acceptance rule; this is not cryptographic conversation attribution.
- The rerun allows 240 seconds without progress, with an 1,800-second absolute
  backstop per workflow round and at most three rounds. Existing continuation nudges
  occur after a turn ends; do not send a second competing request into an active turn.
  Silent tools without emitted progress can still reach the idle deadline.
- Reuse `open_eval_ws()` and the Brain's existing disconnect cancellation. On an
  interrupted task the local runner requires a new dev-log cancellation-completion
  line before starting review; if absent it records a harness block. This relies on
  exclusive eval ownership of the dev box. No new production cancellation API.
- `evals/chad_evidence_review.py` reuses `build_judge_adapter(explicit_model)` and the
  adapter's streaming, thinking controls, active-model guard and inference gate. It
  constructs no agent graph, tool registry or dispatcher. Tools are empty; an emitted
  tool request makes the review incomplete and cannot execute. Model selected:
  `qwen/qwen3.6-35b-a3b`, independently of the executor's LFM supervisor.
- Reuse `model_lifecycle.ensure_resident/release` for temporary evaluator residency,
  and restore the originally resident models afterward. No persistent model-role
  change. Review gets 240 seconds of streaming inactivity and a 1,200-second backstop.
- Reuse `coalesce_tool_results_for_judge`, `_truncate`, and the adapter's LiteLLM
  token-counter dependency to bound oversized evidence. Keep all call indices and
  result-event indices, with bounded argument/result excerpts and explicit omission
  warnings. The GPT-4 tokenizer is only a budgeting proxy: cap its estimate at 14K
  input tokens for the 32K Qwen load, reserving headroom for tokenizer differences
  and a 3K output allowance. Reject oversized bundles before inference. Original
  raw task traces stay local; reviewer messages explicitly record result coalescing.
  Missing screenshots and omitted detail are evidence limits, not proof of success.

The evaluator receives original task, expected behavior, indexed calls, tool results,
final response, plans and harness status. It has no source-discovery tools. A later
separate read-only investigation can use explicitly selected schemas/prompts/source;
that is not necessary to identify directly observed failures. Hypotheses remain
unverified. Model review output is preserved verbatim, not accepted as authoritative.

## Validation and artifacts

Focused checks cover timeout partial capture, activity versus idle, successful slow
reasoning, no-tool evaluation, pinned Qwen selection, generation cancellation and
rejection of emitted tool requests. Existing disconnect and completion-nudge tests
remain in the validation set. No behavior/model comparison sweep or product repair.

Local original and rerun artifacts are under
`.agent/experiments/chad-meet-prism-self-diagnosis/`. These are private captures, not
automatically cloud-shareable reports. Behavioral execution only targets full dev
Brain 8788. Production 8767 is never restarted or used by this test.

## Corrected execution and packaging follow-up

The corrected task reached its 1,800-second round backstop with 110 captured calls,
311 events, approximately 1 MB of tool results and 179K characters of reasoning.
The harness retained the partial round and confirmed disconnect cancellation from
the dev log before loading Qwen. Product behavior was not corrected mid-execution.

The first pinned Qwen request exceeded context (backend reported 58,690 tokens for
a 32,768-token load). A soft tool-results cap alone did not bound all JSON escaping,
call arguments and remaining evidence. This is a harness packaging failure. The
source execution was preserved and not repeated. The follow-up uses the indexed,
budgeted excerpts described above; the original failed review remains in run.json.
Review retry is analysis-only and makes no external task actions.

## Final result

The corrected evidence review completed in 125.03 seconds using adapter model
`openai/qwen/qwen3.6-35b-a3b`, finish_reason=stop, and zero emitted tool calls. Input
budget proxy: 13,768 tokens. LFM and the 3B selector were restored afterward.
Final validation: 203 passed, one Windows symlink-privilege test skipped.

Verbatim model report:
`.agent/experiments/chad-meet-prism-self-diagnosis/corrected-run-01/QWEN_DIAGNOSIS_CORRECTED.md`.
Its full review request/response and model residency evidence are in adjacent
`qwen-review-corrected.json`. Original failed review and execution remain in run.json.

The report identifies missing saved routines and blocked browser attempts, but its
citations confuse event indices with tool-call indices: five of its six labelled
tool indices exceed the 110-call trace. All six correspond to event positions of
the named tools. `review-reference-check.json` records this mechanical check only.
Outcome and causal prose have not been independently accepted; absent evidence and
truncated excerpts do not establish that an action never occurred. Its recommendation
to increase the task timeout is model advice, not an authorized or verified repair.
The diagnosis is preserved without correction or another model attempt.

Phase 4D's real-task execution and isolated Qwen triage have now been exercised.
The evaluator reliability gate remains failed. The next useful work is citation and
uncertainty reliability against the saved capture, rather than another external-task
rerun or a speculative product repair. Task-created snapshot*.yml files remain local
and are deliberately excluded from commits.

## Isolated canonical-reference replay

The evaluator now receives canonical call IDs with matched results inside the same
row, rather than two numeric index systems. Legacy start events must exactly match
the captured call sequence. Only a single unambiguous pending same-name call can
receive a result; overlapping same-name calls retain separately labelled ambiguous
results. No FIFO/LIFO guess. This trace has 107 matched calls, two ambiguous calls,
and one call without a completion. No event positions are exposed for citation.

One analysis-only replay used the same captured task, Qwen model, temperature,
reasoning controls and output budget. It completed in 121.72 seconds with zero tools.
All 11 canonical citations (seven distinct IDs) exist, with no malformed IDs, legacy
numeric indices or explicitly paired tool-name mismatches. This resolves the observed
call/event confusion for this sample. It is not a statistical reliability result or
semantic approval. The model also invented `[round-1]` as a harness reference; the
post-run check flags it as unregistered, so overall reference acceptance remains false.
The runtime check initially covered only call/result IDs; a separately saved final
measurement includes harness-label validation without modifying the raw review.

Verbatim replay: `corrected-run-01/QWEN_DIAGNOSIS_CANONICAL.md`, backed by
`qwen-review-canonical.json` and `canonical-reference-measurement.json` in the same
private experiment folder. No Meet/Gmail/Prism execution was repeated. Original model
residency was restored. Focused validation: 52 passed.

### Evaluation staging

Implemented after the isolated citation replay: the reviewer now explicitly emits
three ordered sections in one call: evidence eligibility, behavioral findings, and
triage. Incomplete capture does not erase independently evidenced behavior errors;
unsupported outcomes remain unverified. It is also told to compare action dependencies
and delegation objectives to the original request, rather than attributing everything
to downstream tool failures. This prompt change is covered offline; a fresh model
review of the staged prompt has not been claimed.

The main-model investigation found an upstream artifact-reference substitution:
"the code" from a newly created Meet became source code from unrelated files.
See [concrete evidence and candidate fix](CHAD_MEET_CODE_REFERENT_TRIAGE.md).

The original Qwen evaluation was one combined call: outcomes, observed behavioral
failures, harness limitations, causal hypotheses and triage. Deterministic preflight,
capture attribution, context budgeting and reference checks surround that call.
The replay deliberately changed reference packaging only, not the analysis stages.

The implemented ordering first assesses capture
sufficiency/harness limitations, then behavioral findings supported by eligible
evidence, then hypotheses and handoff checks. A broken capture should be marked
inconclusive rather than turned into a behavioral diagnosis. Separate model calls
are only warranted when more read-only evidence is needed; three mandatory passes
would add cost without repairing evidence-reference ambiguity.
