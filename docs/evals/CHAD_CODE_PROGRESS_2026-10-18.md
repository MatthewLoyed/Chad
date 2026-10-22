# Chad Code: verified progress and next milestones

## Current implementation

Chad dispatches coding work through OpenHands; deterministic evaluation remains
outside the coding worker. The fixbugs CLI already captures and validates fixtures,
requires a source change, rejects protected-test edits, independently runs the tests,
persists evidence, and returns zero only for a resolved result. Other presets report
completion without claiming verified correctness.

The runner now rejects incomplete SDK conversations and unexecuted final tool calls.
It does not replay these through the MCP fallback. Tool-call mode can be selected
by exact model ID: LFM2.5 uses native calls, while Qwen retains the tested text parser.
The per-request LLM timeout is configurable (900 seconds by default), independently
of the overall job watchdog. The SDK's 300-second default cancelled long Qwen file
generations at approximately 12 tokens/second and retried without producing a file.

## Live evidence

- LFM native-tool smoke: source repaired, protected tests unchanged, six independent
  tests passed. Larger repair stalled on read-only exploration; this is not proof
  that LFM cannot edit code.
- Qwen bounded real-repository repair: misleading PASS label fixed, three regression
  tests passed independently and rejected the original code; five minutes, 11 actions.
- Qwen standalone-verifier stage with task tracking: generated helper and tests,
  reproduced failures, fixed output-stream classification and fixture errors. Reached
  the 32-iteration limit; the final files passed 12 worker tests but only 12/14
  independent cases. This attempt did not finish cleanly.
- Focused Qwen review follow-up: caught grader startup errors and retained partial
  byte/string output on timeout. All 15 worker tests and 14 independent cases passed;
  clean completion in 628 seconds. This establishes the requested fixes, not full
  production readiness or unrestricted autonomous repository repair.

Qwen experiments used Q4_K_M, temperature 0.6, and a user-loaded 54,272-token context.
Task tracking was enabled for the larger stages; subagents and condensation were off.
The successful small run changed several variables, so it does not isolate which
setting caused improvement. Raw requests, transcripts, model-generated files and
operator results remain in the ignored `.agent/experiments/chad-code-fix/` directory.
The experimental verifier is not copied into the dev implementation: retain one
canonical verifier rather than two competing implementations.

## Architecture decision and future aim

Retain a thin CLI orchestrator plus an independent grader. Before expanding live
testing, strengthen the existing implementation in these bounded milestones:

1. Run returned code in a fresh grading container with trusted grading inputs,
   no network or personal mounts, resource limits and an explicit timeout. Current
   fresh-directory/process grading executes returned Python on the host; it is
   import isolation, not a security sandbox.
2. Keep structured test counts, failures and errors rather than inferring correctness
   from transcript claims or matching words in test output. Save base revision,
   patch identity, effective model/settings, grader version and separate output.
3. Add baseline controls: original code must fail and a known correct fix must pass.
   Preserve tests that already pass as well as those expected to become passing.
4. Exercise the real CLI with a controlled worker result, proving both report and
   process exit behavior; then perform one bounded live fixbugs run.
5. Expand to a small real Chad issue with explicit regression tests. Test task
   tracking and token-threshold condensation separately; measure success and latency.
   Introduce subagents only through a separate bounded comparison.

Do not automatically commit, push or apply worker patches. Operator review and
independent verification remain required before integrating generated changes.

Sources checked on 2026-10-18:

- [SWE-bench evaluation](https://www.swebench.com/SWE-bench/guides/evaluation/):
  separate patch generation, containerized grading and result artifacts.
- [OpenHands verification stack](https://www.openhands.dev/blog/20260506-the-verification-stack):
  trajectory checks, review and executable tests serve different roles.

## Offline validation at this checkpoint

### Container grading milestone

Implemented the first grading boundary: the fixbugs CLI selects a disposable Docker
grader with a pinned locally present image, no pulls, no network, non-root user,
read-only root and trusted inputs, dropped capabilities, CPU/memory/process limits,
and forced container removal after timeout. No worker workspace, credentials, Docker
socket or host project is mounted. Structured test counts determine the outcome;
missing/inconsistent results fail closed. Reports include grader/image identity and
source/test SHA-256 hashes. The local subprocess path remains for offline tests.

This is a sandbox boundary, not protection against every adversarial Python program
tampering with its in-process unittest execution. Stronger hostile-submission grading
would require additional instrumentation; current scope is ordinary coding-agent
mistakes and accidental environment contamination.

Live Docker controls verified failing baseline, passing known fix, import error and
empty suite. Container grading requires the documented pinned image to be available;
absence produces verification_error rather than falling back to host execution.
Real CLI controls with controlled worker results verified no-op/nonzero and
correct-fix/zero report behavior. A real timeout left no grading containers behind.
At this milestone, 43 offline tests and 10 subtests pass.
Remaining milestones: automate task-specific baseline preflight, complete model/run
provenance, and run one bounded live worker-to-grader evaluation.

### Live worker-to-container validation completed

Job `bb40d4a6` used the real dispatcher/checker and fixbugs CLI, with Qwen3.6-35B-A3B
Q4_K_M already loaded at 54,272 context, temperature 0.6, 18-iteration/900-second caps,
and skills/MCP/task tracking/subagents/condensation disabled for this small task.
Model residency was checked without reloading the user's model. Settings overrides
were process-local; no persistent model configuration changed.

Qwen fixed the five seeded mathlib bugs, ran its seven tests, and finished cleanly.
The protected test bytes were unchanged. The independent restricted grading container
reported seven tests, zero failures, zero errors, `resolved`; the CLI exited zero.
Source/test hashes and image identity are recorded in
`evals/runs/chad_code/bb40d4a6.json` with transcript and verification logs beside it
(local ignored artifacts). This completes the first bounded live worker-to-grader
milestone, not a guarantee of general repository-fix reliability. The tests cover
the specified fixture contract rather than every possible mathlib edge case.

Next: automate baseline controls and provenance, then select a small real Chad
issue with an explicit failing regression and bounded acceptance criteria.

### Automated controls and provenance completed

The fixbugs CLI now runs two independent grading containers before dispatch:
the supplied baseline must produce real failing/erroring tests and the trusted
known fix must resolve the same suite. A grader startup failure or already-passing
baseline blocks model dispatch. Failed controls save a separate preflight JSON
report. Oracle source stays in the operator process and separate grading container;
it is not written into the worker workspace or task prompt.

Normal reports include both control results plus task hash, repository revision
and dirty state, worker image identity, and an explicit allowlist of effective
worker environment settings. API keys and arbitrary environment variables are
excluded. This records the harness repository revision; fixture source/test hashes
identify the actual grading inputs. Model loaded-context residency is not yet
recorded by this helper.

Validation: 47 offline tests and 12 subtests passed; real Docker baseline/oracle
controls passed, and actual CLI paths with controlled worker results preserved
no-op/nonzero and fixed/zero behavior through the new preflight gate. Effective
temperature 0.6 and worker model were independently recovered from the previous
live worker container. No new model inference was needed for these checks.

Candidate first real Chad issue: the probe's `--model` flag changes the module
default, while dispatch reads the configured model first, so an explicit CLI
override can silently run the configured model instead. Acceptance: an offline
regression exercises actual CLI dispatch configuration, proves explicit override
wins, preserves the configured default when omitted, and reports the effective
dispatched model. Give Qwen this bounded issue in an isolated checkout, independently
test the result, and apply only after review. This is a candidate, not a completed fix.

### First real issue: model override repaired with operator completion

The candidate was reproduced through actual CLI main: an explicit model was ignored
by dispatch while falsely reported as selected; omitted overrides could also report
the module default instead of the configured model. Temporary configuration also
leaked into subsequent calls.

Qwen ran in an isolated checkout of `f93c4ac9`, temperature 0.6, 54,272 context,
18-iteration/900-second cap, without extra skills, planning tools or subagents.
It identified the correct root cause and drafted a configuration override, but
exhausted the iteration limit after approximately 525 seconds, without tests or
completed cleanup/report changes. Its patch was not applied. This is a partial
attempt, not an autonomous issue-resolution success. Full traces remain under
`.agent/experiments/chad-code-fix/qwen-model-issue-03797155/`.

Operator implementation now overrides the configuration consulted by dispatch,
preflight and model residency, preserves the no-codegraph option, reports the
dispatcher-returned model, and restores temporary configuration in `finally`.
Independent tests failed before the fix and pass afterward, covering explicit then
default invocations and dispatch exceptions. Validation: 49 tests and 12 subtests
passed. No model reload, production Brain change, or deployment was required.

Next experiment should reduce exploration overhead using exact relevant snippets
and a stricter first-edit milestone; retain independent acceptance tests and an
explicit cap. Do not infer general autonomy from the earlier seeded-library pass.

`python -m pytest -q tests/unit/test_chad_code_probe_outcome.py tests/unit/test_chad_code_verification.py tests/unit/test_chad_run_validation.py tests/unit/test_chad_headless_completion.py tests/unit/test_chad_code_model_mode.py tests/unit/test_chad_on_chad_driver.py`

Result: 39 tests and 10 subtests passed. No production Brain was started or stopped.
