# Relevance cutoff candidate and reasoning-guard finding

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Runtime audit follow-up: source verified, probe defect identified

`GET http://localhost:8788/api/version` confirmed running and deployed Brain SHA
`632ff9ca`, with `stale.brain=false`. Passive wrappers inside the full dev process
also verified the dev-worktree client/formatter source hashes and effective 0.42
threshold. This was not a stale Brain. The desktop GUI separately reported an older
build; the eval connects directly to 8788 and does not use that GUI process.

The live exact executed prompt produced resume/outline scores **0.447389 / 0.436330**.
Both legitimately passed 0.42. The offline probe used raw `limit=5`, whereas the
automatic client expands five final items into 20 catalog candidates and 100 raw
memory hits. The probe inspected only 25 raw hits, excluding these candidates.
Re-running it with the actual fetch size reproduced both live scores exactly.
The previous claim that the exact executed prompt had no catalog matches was a
probe coverage error, not evidence of a different embedding model or stale source.

Automatic injection now uses a precision-first **0.50** cutoff, reusing the existing
gate. This sample does not support a cutoff that both rejects these distractors and
retains the weak `find Jordan resume` automatic match (0.4429). That weak suggestion
is intentionally omitted; explicit Drive tools are unchanged. The stronger named
outline control (0.5462) remains eligible. This is targeted calibration, not a general
semantic relevance solution. A client-level regression uses the real configured
threshold, checks the 100-hit fetch, rejects the two observed distractors and retains
the stronger control. Client/formatter checks: 18 passed; config YAML parsed.

An unchanged-prompt baseline on audited 8788 again delegated source-file discovery
as its first action. It was cancelled with server confirmation. Its log also proves
the reasoning guard fired at ~3,000 estimated tokens and issued its bounded retry.
An intermediate restart caught a YAML indentation error; it was corrected and the
dev server restarted before any subsequent behavioral test. Production 8767 was
never restarted.

### Verified full-dev result at 0.50

The unchanged executed prompt passed server/auth/disk preflight on refreshed full
Brain 8788 after MCP readiness. Runtime audit recorded the same raw candidates,
effective cutoff 0.50 and formatter result `None`. The actual initial outbound LFM
request contained no Drive document section; system/persona text was byte-for-byte
unchanged (19,996 characters). User/context text fell from 3,087 to 2,612 characters.

First decision: `web_search` for Draftout/Prism and `run_saved_routine` with
`routine_name=gmail_draft`. The replay intentionally stopped at this first decision;
server cancellation was confirmed. There was no first-step source-file traversal.
This is evidence of document removal and a changed first action, **not** proof of
Meet creation, correct code resolution, a valid saved routine, or end-to-end success.
The guard fired at ~3,000 estimated tokens and the retry emitted tools; 21,898 total
recorded reasoning characters across attempts remain within the two-attempt budget.

Other optional graph/activity/session context remains, including unrelated wedding
facts and old sessions. Removing Drive documents alone does not establish that all
context distraction is solved. Do not tune every source without separate evidence.
Next triage should compare the preserved actions against the original task and
separate tool-discovery/routine selection from referent correctness.

Private evidence: `runtime-audit.jsonl`, `runtime-fetch-limit-probe.json`,
`runtime-first-decision.json` (0.42 baseline), `verified-cutoff-first-decision.json`,
`verified-supervisor-messages.json`, and `dev-brain-verified.*.log`. Preserve earlier
failed captures. The initial raw-probe scripts are historical, superseded by the
client-level regression and runtime audit; do not use their smaller fetch as a gate.

## Existing relevance gates and candidate

The Drive context injector already applies `drive_catalog.relevance_threshold`,
previously 0.22. A read-only local retrieval probe for the original Meet/Gmail/Prism
request returned unrelated catalog hits at 0.376–0.396. Named-document controls
returned a resume at 0.443 and a service outline at 0.546. A candidate 0.42 cutoff
retained the named controls while removing those broad matches. This is a small
calibration sample, not a general relevance benchmark.

The implementation also closes two threshold bypasses: the shared formatter no
longer exempts zero/missing similarity when a positive threshold is requested, and
Drive keyword fallbacks no longer fabricate semantic scores of 0.45/0.50. Unknown
scores are omitted from automatic semantic context. Explicit Drive search tools
are unchanged. Nonfinite scores are rejected. Retrieval/client tests: 57 passed.

## Actual full-dev replay: candidate did not resolve confusion

Only dev Brain 8788 was refreshed; production 8767 was not touched. The replay used
the same original task plus the same addressed-draft-only constraint, with unchanged
selected persona inputs and the same LFM supervisor. It was bounded to the first
tool decision or terminal response, rather than replaying all external actions.
The initial startup preflight was blocked while the server was initializing; no
task was dispatched then. The subsequent replay ran after health and MCP readiness.

The actual initial outbound supervisor prompt still contained the two unrelated
recipient documents. This differs from the standalone probe's exact-executed-prompt
result, which returned no catalog rows even before applying the new cutoff. Thus
the standalone score probe does not fully reproduce the live retrieval path. The
remaining difference in query/source/scoring needs observation before further tuning;
do not claim that the cutoff removed the original distractors or proved causality.

The model emitted no tool calls. Its final response asked for a source-code file or
snippet, and claimed there was no code in Drive/email and no launchable app, despite
having made no tool calls in this replay. The initial interpretation remained wrong.
It generated 33,467 characters of recorded reasoning across attempts. The first
narration-without-action guard fired, followed by another generation.

Outcome: **candidate relevance change did not fix the demonstrated confusion in this
bounded live replay**. This is not a full external-task completion test. The current
candidate threshold is experimental, not a validated universal cutoff. No prompt
rewrite makes "code" mean "Meet code"; the original wording is preserved.

Private evidence is under `.agent/experiments/chad-relevance-cutoff/`:
`retrieval-before.json`, `cutoff-comparison.json`, `first-decision-after-live.json`,
`first-supervisor-messages.json`, and dev logs. Prompt dumps have time-only headers
across multiple dates; the audit must match the original user text/model and select
the latest applicable record, not the first matching clock time.

## Reasoning watchdog: independently confirmed defects and fix

The configured 3,000-token budget is a chars/4 estimate per generation, not an exact
provider-token or conversation-total cap. The implementation rounded every streamed
chunk down individually, undercounting token-sized deltas shorter than four chars.
It also disabled the entire nudge manager after *any* corrective retry, so the earlier
narration retry removed the reasoning watchdog.

The existing guard now accumulates characters before estimating tokens. It remains
active on a retried local generation. Narration correction remains one-shot; when
the retry itself exceeds the reasoning budget, the adapter closes the stream and
returns an explicit generation error instead of running unbounded or resending again.
Two bounded attempts may still total roughly 6K estimated tokens. The guard ceases
watching once a tool call is emitted, as before; it is not a whole-task token budget.

Tests exercise one-character deltas, normal successful retry, repeated runaway and
the narration→reasoning failure path. Guard/narration/usage checks: 25 passed.
Dev 8788 was refreshed to load the guard fix; no claim of another live reasoning
stress test is made. The guard fix and the relevance experiment are separate changes.

## Why Qwen said "wedding code"

Both review requests include the original task and draft-only constraint. The phrase
"wedding code" is absent from that task but appears in the actual execution evidence:
Chad searched for `wedding code script python` and similar phrases. Qwen reproduced
the drifted wording as if it described the request. Its ordered review now starts
with evidence eligibility and compares behavior to the original task, but improved
diagnosis from that prompt change has not yet been demonstrated by a fresh review.
