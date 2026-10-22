# Chat handoff — 2026-10-19

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Wrap-up checkpoint — resume here

User reports Scout and planner are operating much better with the improved prompts.
Treat this as positive human behavioral feedback, alongside the two recorded Bonsai
planning runs; it is not a measured pass rate for vague requests. The user previously
reported successful completion with the detailed Meet/email/Prism prompt.

Current work includes scoped Scout prompts/tools, mandatory planner Scout decisions,
evidence handoff, progressive capability discovery, read-only app inspection,
specific grounded plans and grouping consecutive work for one specialist into a
complete delegation. Scout/browser inherit the active supervisor model. See
`CHAD_SCOUT_LIVE_CONTRACT_FIX.md` and `CHAD_PLANNER_SCOUT_DECISION.md`.

Latest repair: `CHAD_DIRECT_TOOL_APPROVAL_RESUME.md`. GUI approval directly replays
the saved atomic call before any model inference, then feeds its output to the
supervisor. Fixed lost stream ownership and stale completions hiding resumed activity.
48 approval/backend regressions, 57 broader regressions (overlap), 34 UI tests and
TypeScript pass. This final approval patch has not had a live GUI rerun.

Next: start a fresh dev GUI task, verify approve -> tool -> visible resumed ReAct
activity, then progressively shorten the task prompt and retain the first failure
for triage. Older paused cards may lack the new saved-call data. Do not restart
production 8767. No services restarted for the final approval patch. The test-owned
dev Brain/Hands processes used for the two Scout planning probes were stopped;
older runtime descriptions below are historical, not a current process inventory.

Private traces remain ignored under `.agent/experiments/`. Runtime entity-cache
changes and root-level browser snapshots/screenshots are excluded from the wrap-up
commit and left on disk.

## Intent and constraints

Prioritize testing, concrete bug identification, evidence, analysis and triage over
self-repair. Continue toward a useful diagnosis/handoff, not broad model comparisons
or speculative fixes. Behavioral tests use the full dev Brain on **8788** with its
normal tools/MCPs. **Never restart production 8767.** Preserve private raw evidence.
Read `CHAD_SELF_EVAL_NEXT_STEPS.md` and `CHAD_SELF_EVAL_VERIFICATION_GATES.md`.

## Completed and verified

- Latest: `CHAD_SCOUT_UI_AND_BUDGET_REPAIRS.md` records Scout lifecycle/report
  visibility fixes and budgets (general 30, UI 40, browser 60), with matching graph
  recursion. 74 backend and six frontend tests, TypeScript and frontend build pass.
  No live replay or manual Brain restart.

- `0d364d36`: live Drive retrieval reproduced and conservative automatic cutoff
  raised to 0.50. The earlier offline probe fetched only 25 raw candidates versus
  the real client's 100, hiding the two distractors. Matching the fetch reproduced
  live scores 0.447389 / 0.436330; the previous 0.42 cutoff necessarily admitted them.
- Same-prompt full-dev replay confirms both documents absent from the actual initial
  LFM prompt. System/persona text unchanged. First decision changed from source-file
  traversal to Draftout/Prism web search plus `run_saved_routine(gmail_draft)`.
  Intentionally stopped at first decision; cancellation confirmed. **Not a full task
  pass or proof that “code” now means Meet join code.** Client/formatter: 18 tests pass.
- Tradeoff: weak automatic `find Jordan resume` match (0.4429) is now omitted; stronger
  outline control (0.5462) retained. Explicit Drive search tools unchanged. Other
  graph/activity/session distractions remain; no general relevance fix is claimed.
- `8f31ac6`: reasoning guard accumulates tiny chunks correctly and remains active
  after corrective retries. Live logs now show it firing at ~3,000 estimated tokens.
  Budget is per generation, chars/4 estimate; two bounded attempts can total ~6K.
- Evidence review now uses canonical call/result IDs and flags invalid references.
  Isolated pinned Qwen replay had 11/11 valid tool citations, but invented a round
  label, so whole-report trust remains unproven. Reviewer protocol has three ordered
  sections in one call: evidence eligibility, behavioral findings, triage.
- Earlier harness fixes preserve partial timed-out traces and distinguish meaningful
  task inactivity from a hard wall timeout. Reuse these existing mechanisms.

## Runtime at handoff

**Latest public-grounding pilot:** after renewed user authorization, a narrower
full-dev test ran without restart or model-setting changes. Current dev runtime
uses LFM, not the earlier Bonsai override. See
[CHAD_PUBLIC_GROUNDING_PILOT.md](CHAD_PUBLIC_GROUNDING_PILOT.md): baseline first
decision searches Drive code and public Draftout/drone information; appended Meet/
Prism facts are verified in actual grounded input, but that arm ends with a reasoning-
guard generation error and no tools. Explicit source-code control retains file-code
intent but calls `run_saved_routine(code_agent)` without existence evidence.

Important confound: including reference text in the submitted user message changes
retrieval. Jordan's known email is present in baseline and absent in the grounded arm.
All three actual system hashes match. Do not call this a clean causal A/B or a
successful grounding fix. Next: original-task-only retrieval, then append public
facts after retrieval; preserve personal identity evidence. Private captures and
exact actual inputs: `.agent/experiments/chad-grounding-ab/`. Interrupted trials
have server cancellation confirmed; grounded error reached terminal response.
No automatic extraction/search pipeline or product repair was added.

**Latest entity-extraction experiment:** user requested trying concurrent Qwen2.5
extraction/selection, grounded in current engineering evidence. See
[CHAD_ENTITY_EXTRACTION_PROBE.md](CHAD_ENTITY_EXTRACTION_PROBE.md). Two bounded
16-trial variants: extraction+dependencies 0/16 complete-contract passes; simplified
extraction-only 4/16. Do not replace the existing alias/regex path or wire these
labels into automatic routing. The existing NotebookLM alias case regresses under
Qwen splitting. Concurrent requests on one resident instance were feasible: median
5.167s versus 7.617s serial in a simplified component workload, not full Brain.
Qwen selection-call median is 2.695s, so extraction still adds latency.

Saved graph-context resolution finds Jordan's address with provenance and no public
query. Private evidence and summary: `.agent/experiments/chad-entity-extraction/`.
No full Brain replay, reload, production change or automatic web lookup from model
output occurred. Four new probe-check tests pass. Next candidate is hybrid name
proposals + deterministic aliases + source-backed public grounding/local identity
resolution, with held-out tests before integration. Do not tune indefinitely on
the original eight development cases.

**Latest Phase 4D.2 update:** saved-decision triage and evaluator-context work are
recorded in [CHAD_PHASE4D2_TRIAGE.md](CHAD_PHASE4D2_TRIAGE.md). One enriched Qwen
review passed citations but failed semantic acceptance (cross-case sequencing,
invented tool names, weak retrieval analysis). Follow-up prompt guards are offline
tested only. 16 focused tests pass. A before-prompt provenance mismatch was found
and corrected in the packager; the completed replay retains the original mismatch.

**Bonsai follow-up:** the user explicitly instructed proceeding after the initial
restart rejection; the identity-checked dev restart then succeeded. The full-dev
original-prompt Bonsai run paused after 179.0s with source-code-only clarification
choices. No Meet/draft/Prism action occurred. The explicitly clarified continuation
then preserved the Meet-code dependency but hit its 600-second backstop during
LFM browser recipient lookup: 25 calls retained, cancellation confirmed, no verified
Meet/draft/launch. Browser specialist remains explicitly pinned to LFM; this
is Bonsai supervision, not an all-Bonsai pipeline. See
[live task findings](CHAD_BONSAI_TASK_TEST.md). No persistent model preferences
changed; production 8767 was untouched.

The continuation exposed and fixed an eval helper bug: resolving an approval token
must be followed by a same-conversation `user_input` to actually start a new turn.
The first idle attempt is `void_harness`. Corrected live continuation starts normally.
32 focused tests pass. Prioritize acting-model artifact binding and execution before
further Qwen review tuning.

Latest dev 8788 runtime: startup SHA `76ef6f35`, PID 10608, boot ID
`6e6cf96c-3cc1-4a1f-ad08-e59cbca67f74`, process-only Bonsai supervisor wrapper
`start_bonsai_dev_brain.py` delegating to the normal audited bootstrap. Browser
preference remains LFM. Latest logs are `dev-brain-bonsai.*.log`.

Earlier 4D.1 runtime: running/deployed `0d364d36`, PID 33232. PIDs are historical:
re-query before any action. Production was untouched. The desktop GUI separately
reported an older build; direct eval WebSocket tests do not use that GUI process.

The Bonsai process wrapper delegates through the ignored observation-only wrapper
`.agent/experiments/chad-relevance-cutoff/start_audited_dev_brain.py`, which delegates
to the normal full `scripts/prod/run_dev_brain.py` bootstrap. It logs retrieval source
hashes, effective thresholds, raw scores and formatter output without changing them.
Earlier 4D.1 logs: `dev-brain-committed.log` / `dev-brain-committed.err.log` in that folder.
Wrapper uses main worktree secrets/shared SQLite; never print secrets.

## Phase 4D.2 — saved-evidence triage (initial pass complete)

Latest successful-user-observed run: `CHAD_SUCCESS_UI_TRACE_REPAIRS.md` covers
18:46–19:00 conversation `5316b99e-fe02-442a-928b-c3cb909554b1`. Repairs: JSON-safe
approval previews (runtime registry object broke the pause), repeatable fresh desktop
observations, nonterminal input acknowledgements, UI handoff tool receipts, and
selector/runtime verification guidance. The local overlay had an obsolete 3-call
cap; corrected with backup. Preserve user-observed success separately from the
trace's missing send/active-call/game postconditions. No live replay this pass.

Newest follow-up: `CHAD_BONSAI_APPROVAL_REPAIRS.md`. The 15:40–15:46 Bonsai GUI run
surfaced stale approval checkpoints, mismatched approval-card tool identity, a draft
approval policy, planning-mode continuation, and a saved LFM browser pin. Repairs and
regressions are in the working tree. Browser now always inherits the supervisor.
Next behavioral objective is a staged reduction from the detailed prompt to the
original short request, with independently verified outcomes; no new live run yet.

Harness repair follow-up: `CHAD_GUI_HARNESS_REPAIRS.md`. MCP dictionary snapshot
hydration, visible generic text, screenshot text-channel containment, new-plan
statuses, domain-scoped budget propagation and observation reuse hints are repaired
in the working tree. 111 focused/adjacent tests pass, plus offline retention checks
against the saved successful Meet snapshot. No live replay was performed. Model
success retention, draft selection and local contact retrieval remain follow-ups;
native vision transport is not added by this repair.

Additional GUI evidence: `CHAD_REFINED_GUI_TRACE_TRIAGE.md` analyzes the refined
15:15–15:25 October 19 LFM run. It created a meeting, then misreported it as
pre-existing. Confirmed source issues include MCP dictionaries bypassing snapshot
hydration, base64 screenshots in text, terminal budget state lost at handoff, and
accepted `in_progress` status skipping Step 1. Pure-function status reproduction
and hashed private artifacts are in `.agent/experiments/chad-refined-gui-trace/`.
This analysis made no application-source changes or live replay. Read it before
another test.

The steps below were performed; consult the new triage report for measured results,
semantic failures and corrected input provenance. The requested Bonsai probe has
subsequently run; consult its separate task report before any further replay.

1. Use existing tool-free pinned `qwen/qwen3.6-35b-a3b` evaluator and canonical evidence
   packaging; do not run the task again merely to obtain analysis. Review the saved
   before/after decisions with the original request and explicit early-stop boundary.
2. Require separation of verified document removal, changed action, unresolved code
   referent, and questionable saved-routine/tool discovery. A first-decision capture
   cannot establish tool results or task completion. Check citations mechanically.
3. Produce a concrete bug handoff and bounded verification proposal. Phase 5 further
   repair/full-task completion is optional, not required for identification success.

Original task: “start a google meet and email jordan the code, and use prism launcher
to launch draftout instance”. Executed prompt adds an addressed Gmail **DRAFT ONLY**
constraint, clarify ambiguous Jordan rather than guess, no unrelated settings/files.
Keep that exact executed prompt for matched behavioral replays.

## Evidence and implementation map

- `CHAD_RELEVANCE_CUTOFF_TEST.md`: detailed measured results and limitations.
- `CHAD_MEET_PRISM_EVAL.md`, `CHAD_MEET_CODE_REFERENT_TRIAGE.md`: original task and
  reviewer findings. Qwen received the original prompt. “Wedding code” occurred in
  Chad's drifted searches; do not describe it as wholly invented by the evaluator.
- Private `.agent/experiments/chad-meet-prism-self-diagnosis/corrected-run-01/run.json`:
  full original failure, 110 calls/311 events, 30-minute backstop; cancellation confirmed.
- Private `.agent/experiments/chad-relevance-cutoff/`: `runtime-audit.jsonl`,
  `runtime-fetch-limit-probe.json`, `runtime-first-decision.json` (0.42 baseline),
  `verified-cutoff-first-decision.json`, `verified-supervisor-messages.json`.
- `evals/chad_evidence_review.py`: canonical joins/reference validation;
  `evals/live.py`: collection/inactivity/partial timeout capture.
- `src/core/intelligence/drive_catalog_client.py`, `knowledge_context_format.py`:
  existing semantic gate, strict unknown/nonfinite-score handling, no fabricated
  fallback confidence. `tests/unit/test_drive_context_cutoff.py` exercises real fetch.

Prompt dump headers contain time only across multiple dates. Select the latest
matching model/original user text, never the first matching clock time. Audit helper
`audit_verified_prompt.py` does this. Preserve older failed evidence; do not overwrite.

Tracked tree was clean after the implementation commit. Untracked `snapshot1.yml`
through `snapshot8.yml` are private browser artifacts; preserve and do not commit.


### Scout input isolation follow-up

See `docs/evals/CHAD_SCOUT_CONTEXT_ISOLATION.md`: Scout now starts with an independent
core search/read tool set and Scout-only persona, brief and original request; no
composite supervisor context. Discovery filtering and memory mutation guards were
repaired. Thirty focused tests pass; no live replay or manual Brain restart.


### Required planner Scout decision

See `docs/evals/CHAD_PLANNER_SCOUT_DECISION.md`. Tool binding and execution now
require a nonempty dispatch or explicit skip before planning; early proposals,
mixed batches and degraded fallback paths cannot bypass the decision. Keep local
planner overlays aligned with baseline policy. No live replay or manual restart.


### Scout evidence survives planning and approval

`docs/evals/CHAD_SCOUT_EVIDENCE_HANDOFF.md` records checkpointed findings, mandatory
coverage review, one targeted follow-up, supervisor/delegate evidence injection and
read-only operation classification. Checkpoints must preserve evidence across
approval; cached child system prompts still need current facts on each task message.
Tool-call provenance is checked, but semantic correctness remains a model/eval concern.
No live task replay or manual Brain restart.


### check_app_exists restored as read-only

The protocol/partition declaration survived but the callable tool and daemon handler
were missing. Restored both using a shared non-launching app resolver, with explicit
shortcut/alias limits and Scout discovery visibility. 44 focused tests pass; a local
Prism lookup verified its executable without launching. No services restarted.
See `docs/evals/CHAD_SCOUT_EVIDENCE_HANDOFF.md`.


### Scout discovery/report contract — live verified

See `docs/evals/CHAD_SCOUT_LIVE_CONTRACT_FIX.md`: explicit outgoing report schema,
source IDs in results, actionable validation, bounded report retries, exact tool
discovery/category recovery and refreshed loaded-tool context. 77 focused tests
pass; two real Bonsai planning runs on dev 8788 reached approval with zero report
rejections. Run 1 exercised app discovery and actual read-only Hands inspection;
run 2 returned matching personal memory and an official Meet entry link. These are
Scout/planning checks, not completed meeting/email/Prism actions. Production untouched.


### Direct tool approval continuation and visible resumed stream

Approval now replays the saved atomic call before model inference, bypassing routing,
selection and compaction. The pause checkpoints normally instead of cancelling the
stream mid-tool. Fixed desktop approval clearing stream ownership, plus stale final
responses closing the next turn's emitter. Real graph tests cover direct/specialist
calls, exact arguments, decline, receipts and sibling isolation; UI tests cover
visibility/session isolation. Production untouched; no live GUI rerun for this patch.
See `docs/evals/CHAD_DIRECT_TOOL_APPROVAL_RESUME.md`.
