# Scout evidence, coverage and execution handoff — 2026-10-19

## Implemented flow

Planner first dispatches Scout or explicitly skips it. Scout has an internal
finish_scout_report(items) tool: each item has question, verified/unresolved/
unavailable status, finding and source. Tool-based verified items must reference
a returned observation through source_call_id. An exact supplied fact may cite
its matching supplied-context source. Failed calls and tool discovery do not
establish verification. These checks establish provenance, not semantic entailment
of a model's claim by the underlying result.

A checkpointed scout_evidence record retains the brief, itemized findings, tool
call receipts and failure/unstructured status. A plain-text exit remains visible
but is not silently converted to verified facts. At most two rounds are retained.
The planner must call review_scout before proposal: assess requested-fact coverage
and remaining gaps, proceed with a reason, or request one targeted follow-up.
A third pass cannot run. Early proposals and mixed calls cannot bypass either gate.
Essential clarification becomes available after review; unresolved facts are not
filled with guesses. Model review is not a deterministic completeness proof.

Approval-turn input omits evidence fields so it cannot overwrite the plan's
checkpointed facts. New planning episodes reset prior evidence; failure/validation
replans retain it. The supervisor receives the evidence beside current turn data.
Both specialist handoff paths attach only task-matching evidence to the current
HumanMessage, including when a child already has a cached system prompt. The
planner sees one evidence block rather than duplicated report text.

Scout gets a bounded selection of matching lines from supplied turn context,
personal core profile and persona:user, each labeled with its source. This avoids
copying whole personas or unrelated retrieval context. Matching is lexical and can
miss relevant facts; memory/contact discovery remains available.

Unknown tools are denied unless classified as approved native retrieval or declared
read-only in registered metadata. Mixed memory actions remain search/status only;
generic MCP execution, desktop mutations, saved routines and delegation cannot
bypass the boundary. Discovery responses expose only permitted tools, so their
messages no longer falsely say blocked matches were loaded. Read-only metadata is
a trusted registration contract, not an OS sandbox or independent code audit.

## Verification and next user test

Focused tests exercise checkpoint preservation, actual supervisor context injection,
both delegate paths with cached prompts, structured/unstructured reports, provenance
validation, coverage rejection/proceed/follow-up, the two-pass bound, episode reset,
unknown tool rejection and existing compiled planning/approval/execution behavior.
No live model/task replay or manual Brain restart was performed; production 8767
was not touched. The user will perform the next behavioral run on dev 8788.

Inspect Scout's report and planner coverage review, then check that the executing
supervisor/delegate uses the same contact/file/URL facts after approval. UI/browser
state is volatile and should be verified at execution time, while durable contact
facts should not trigger repeated broad searches. Keep raw contact and meeting
information in ignored private evidence, not tracked docs.

Final validation: 76 focused/adjacent backend tests passed; git diff --check passed.
Existing Pydantic deprecation and optional Nomic acceleration warnings remain.


## Report-phase correction

The 20:21 dev trace showed a system-only initial Scout request followed immediately
by the old fallback instruction, "No more tool calls", encoded as a user message
with zero tools bound. This was a harness instruction, not a user constraint; it
contradicted the newly required finish_scout_report tool. The fallback was reachable
without exhausting the research budget when the first response was empty.

Scout now starts with an actual user-role gather message. A tool-free first response
gets one labeled gather retry with research tools still bound. The report phase is
explicitly labeled HARNESS, retains only finish_scout_report and validates its
sources. No outside tool executes during that phase. Empty/budget paths make at
most one report attempt. The role was shortened, core-profile facts are read without
the prompt-builder's instructional wrapper, and empty-memory sentinel lines are
excluded from relevant known context. Existing plain-text exits remain unstructured.
No live replay or manual Brain restart was performed for this correction.


## Discovery escalation leak

A saved Scout prompt showed search_for_tool(query="launch prism launcher app",
category="system") returning only escalate_to_agent after read-only filtering.
open_app is an OS launch tool, not a read-only installation inspector; filtering
it is intentional. The system category also narrowed retrieval away from os.
Escalation leaked because its non-side-effecting metadata was mistaken for an
observation contract. Control/transition tools are now explicitly denied even
when marked retry-safe/read-only. Scout discovery feedback and role guidance now
say to search for inspection operations, omit unconfirmed categories and never
use public web search to infer this machine's capabilities or installed apps.
This correction does not add a read-only installed-app resolver; unavailable
inspection should remain a gap for execution rather than a guessed verification.


## Read-only app lookup restored

check_app_exists is now a native os tool, with readOnlyHint and non-side-effecting
metadata, the existing LOCAL partition and Scout's audited retrieval allowlist.
It is surfaced in the installed-app capability summary. The tool dispatches the
existing CHECK_APP_EXISTS daemon action; it cannot dispatch launch. App lookup and
launch share the extracted config/PATH/Start Menu/direct-path resolver. Only the
LAUNCH_APP action executes the result.

Lookup inspects shortcut targets when possible and reports missing targets or
unverified shortcuts explicitly. Known web/URI aliases are not reported as locally
installed apps. Results never claim startup was tested. Daemon-offline errors are
not converted into app absence. No match means only the checked locations were
exhausted, not proof that no portable copy exists anywhere on disk.

Validation: 44 focused backend tests pass, covering automatic file discovery, tool
and daemon boundaries, no-spawn checks, config/PATH/shortcut/direct resolution,
broken links, virtual aliases, offline handling, Scout visibility, and mocked launch
compatibility. A direct local read-only call through the new daemon handler found
Prism Launcher via Start Menu and verified its target file, with launch_tested=false.
No app was launched. No running dev Brain/standalone Hands process was found during
the final availability check; no services were started or restarted. The restored
tool/handler are loaded when the dev stack next starts. Production was untouched.
