# Chad discovery regression suite and bug inbox

This is a developer-run identification workflow, not an enabled background monitor.
The primary deliverable is a reproducible bug and a focused cloud-model handoff.
Model-written analysis is not needed to establish these deterministic failures.

## Phase 4B — Reusable regression and handoff workflow — COMPLETE

`evals/chad_regression_inbox.py` runs four fresh-conversation cases sequentially:
supplied arithmetic, unknown information represented as null, hidden immediate-file
listing, and reading a specified file before answering. Expected values are operator
owned. Checks distinguish output format, exact values, required tools, recorded
network attempts, unrequested memory-write attempts and fixture changes. Missing
required tool evidence is a contract failure, not proof of the model's intent or a
general detector for premature completion.

Run from the repository with the existing authenticated eval environment, full dev
Brain already running, and the configured models already resident:

```powershell
python -m evals.chad_regression_inbox .agent/experiments/chad-regression-inbox/run-02
```

The module pins and validates local 8788, checks server/auth/disk preflight, caps each
turn at 240 seconds with a 270-second outer timeout, and stops after infrastructure
failure or fixture mutation. It does not start/restart a Brain, change model roles,
or dispatch repairs. Use a new output directory for each run. Load auth through the
existing eval bootstrap; do not place tokens in commands or tracked files.

`run.json` preserves prompts, raw traces, fixture manifests, selected active persona
inputs, config hash and code provenance. Treat it as private local evidence.
Sibling `bug-inbox.json` groups by versioned case and symptom, retaining distinct
run/case occurrences. Reimport is idempotent. Passing later does not close an issue.
Incomplete turns do not become behavioral bugs; provenance drift rejects inbox
ingestion. Each bug gets a handoff containing the catalog task, expected answer,
violation, reproduction command and acceptance criteria. Raw model prose, personal
memory content and overlay text are excluded from handoffs.

These are recorded-tool and immediate-fixture checks, not a sandbox or exhaustive
filesystem, network or persistent-state audit. Required calls establish attempted
access, not proof that every tool result was correct. The inbox is local, not a
remote issue tracker. Provenance fields are operator evidence, not authentication
against a hostile report author. Historical pre-catalog findings stay in the findings
document; they are not automatically imported or declared closed.

## First bounded run — 2026-10-19

Full dev Brain 8788 completed four cases: arithmetic passed (25.3s), unknown passed
(19.1s), hidden listing failed (36.2s), file read passed (43.1s). Config and selected
persona inputs stayed unchanged. No model-role change or repair dispatch.

The listing response broke the exact raw-JSON contract. Separately, indexed tool
call 1 was `manage_user_memory` with action `save`, despite this task only asking
to inspect a synthetic local fixture. This establishes an unrequested memory-write
attempt; persistence success and cause remain unverified. Records:
`CHAD-REG-09ee27be73` (format), `CHAD-REG-e783ae9596` (memory attempt).

Local authoritative artifacts are under
`.agent/experiments/chad-regression-inbox/verified/`, backed by
`run-01/adjudicated.json` and untouched `run-01/run.json`.
The initial read predicate mistakenly checked `path`; the real schema uses
`file_path`. Source inspection and a unit test corrected it. Offline adjudication
removed that false finding and added the observed memory attempt without another
model call. Earlier generated derivatives are explicitly marked superseded.
`adjudicate()` preserves the capture and refuses tasks differing from the versioned
catalog. No model output was normalized into a passing response.

## Phase 4C.1 — Audit captured side effects — COMPLETE

Offline inspection of the original capture found that the memory tool reported
`saved_count=1`, `skipped_count=0`, and a knowledge-graph save caption. Its returned
fact described only two files, while the independent fixture includes three with
the hidden file. This is evidence of a tool-reported save of incorrect transient
fixture information, not an independent database audit. `ManageUserMemory._save`
increments this count after `client.ingest` reports success. The tool's documented
purpose is durable user facts; synthetic directory contents do not fit that purpose.
Root cause remains unverified; inspect selection policy and the memory-write gate
before deciding where a repair belongs. No memory cleanup or product repair ran.

The file-read task's extra `run_saved_routine` call reported `success=False` and
`failure_class=not_found`. Its final requested answer was correct. Keep that recovered
tool error as an advisory observation; it does not turn the task into a failure or
prove the routine ran any steps.

The regression grader now retains safe tool-reported outcome summaries separately
from findings. Flattened result headers must match captured call names and order;
ambiguous or missing results yield no inferred outcome. Summaries omit raw memory
content. This remains text-result attribution, not structured event attestation.
The memory-write detector also covers the real schema's `forget` action. Tests cover
wrong/duplicate saved counts, ambiguous attribution, recovered errors and forgetting.

The untouched capture remains `run-01/run.json`; current offline assessment is
`run-01/phase4c-adjudicated.json`. `verified/bug-inbox.json` and handoffs are enriched
with the reported outcome without adding duplicate occurrences. Previous assessment
and inbox retained. No live model calls were needed. Focused validation: 75 passed.

## Phase 4C.2 — Concrete real-task intake mechanism — COMPLETE

Implemented an offline operator-owned contract/import path with deduplicated findings
and separately privacy-reviewed handoff text. See [real-task intake](CHAD_REAL_TASK_INTAKE.md)
for supported contracts, command, trust limits and integration evidence. Phase 4D
applies it to actual user-task failures; the synthetic smoke test is not real-task
coverage. Keep uncertain causes separate and avoid another model comparison.

The core detection/inbox/handoff workflow is available now. General self-review
reliability remains a failed gate; autonomous background discovery is unproven.
Phase 5, autonomous repair, remains optional and deferred.

Validation: 162 focused tests passed; one Windows symlink-privilege test skipped.
Production 8767 was not restarted or used for behavioral testing.
