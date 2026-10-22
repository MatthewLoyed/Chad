# Real-task failure intake — Phase 4C.2

`evals/chad_task_intake.py` turns an operator-authored contract and an existing task
capture into a local assessment, deduplicated inbox entry and optional reviewed
handoff. It runs offline: no inference, Brain connection, repair or publishing.
This makes a concrete failure actionable without requiring Chad to diagnose it.

## Use

Copy `docs/evals/examples/chad_task_intake.json` into a private experiment directory.
Choose a stable task ID and checks that actually follow from the original request.
The example is for a local inspection task; it is not a universal memory prohibition.
Supported checks:

- `response_json_equals`: strict raw JSON and exact values/types via `expected`.
- `required_tool` / `forbidden_tool`: exact tool name and optional exact parameter
  subset. File paths are compared literally; specify the captured intended spelling.
- `fixture_unchanged`: compare independently captured before/after manifests.

Provide either a capture with `trace`, optional fixture manifests and `run_id`, or
an existing run report plus `--trial ID`. Trace fields are `status`, `error`,
`final_response`, and indexed `tool_calls` with `name` and `params`.

```powershell
python -m evals.chad_task_intake PRIVATE_SPEC.json CAPTURE.json NEW_OUTPUT_DIR --trial hidden-listing
```

Use a fresh output directory; the source is read without modification. Local
`assessment.json` records contract/source hashes, evidence selectors and outcomes.
Sibling `real-task-inbox.json` groups failures by task ID, contract hash and check ID.
The same run/trial does not add another occurrence. A changed contract gets a
separate identity. Later passes do not silently close earlier issues.

Missing evidence, malformed tool records or incomplete turns make the contract
inconclusive and exclude it from the bug inbox. Individual check results are still
retained. The intake never executes model-generated grading code. A recovered tool
error does not fail a completed task unless an explicit contract check fails.

## Privacy and trust

Raw trace prose, memory content, tool arguments, paths and persona overlays are not
copied into a handoff. Write the four handoff fields separately: reproduction,
expected, observed, acceptance. Review those fields for private information, then
set `handoff.privacy_reviewed=true` to generate a local `handoff.md`; false or absent
means no handoff export. This flag is the operator's attestation, not an automatic
privacy scanner or a request for additional user approval. Nothing is sent anywhere.
The assessed status appears alongside operator-written observations; their prose
is not mechanically verified. Keep causes explicitly unverified.

Contracts and captures are operator inputs, not authenticated truth. Intake does
not attest the live persona cache, independently verify persistent storage, or
infer causality, intent, severity or general premature completion. Fixture checks
are only as complete as the supplied manifests. Successful tool calls and required
tool attempts remain separate claims. Offline import can inspect historical captures;
any new behavioral test still uses full dev Brain 8788, never a production restart.

## Validation and remaining work

The existing dev-8788 hidden-listing capture exercises the whole CLI path. The
no-save check fails, no-forget and fixture checks pass, and a repeated import adds
no duplicate occurrence. A separately reviewed synthetic-task brief exports without
raw private evidence. This is an integration check using an already known synthetic
failure, not a new real-world bug or an additional live reproduction.

Validation: 85 focused tests passed, including good/bad/incomplete captures, strict
JSON and types, missing tool evidence, contract identity, duplicate import, privacy
export and CLI integration. Local integration evidence is under
`.agent/experiments/chad-real-task-intake/`. No new model calls or production changes.

Phase 4C.2's intake mechanism is complete. The next bounded phase is **4D: apply it
to an actual user-task failure with an explicit expected outcome**, then promote
verified recurring symptoms into regression cases. Do not count this smoke test as
real-task coverage. Background monitoring and reliable model self-review remain
unproven. Phase 5 self-repair remains optional and deferred.
