# Planner Scout decision gate — 2026-10-19

Before planning, the harness exposes only dispatch_scout(brief) and skip_scout(reason).
A dispatch requires a nonempty gather brief; skipping requires a nonempty reason.
Only one decision call is accepted per response: early proposals, clarification calls
and mixed batches are rejected. After an explicit skip, propose_plan and permitted
clarification tools unlock. A Scout return goes directly to planning without another
dispatch. Decision state is carried through graph retries and initialized for new
turn input alongside the Scout counter/request, avoiding stale previous-turn bypasses.

No-output and round-cap fallbacks cannot create a plan while a decision is still
missing. They raise an explicit error instead. skip_scout is excluded from execution
plan steps. Baseline/local planner guidance and dispatch tool descriptions now request
only execution-relevant missing information: unfamiliar entities/terms, personal
contact details, file/app/browser state, sources and gaps. Old local planner guidance
was backed up under .agent/experiments/chad-planner-scout-decision/.

Verification uses isolated planner/tool stubs, not live model inference. Tests cover
dispatch, explicit skip, unavailable early proposal, mixed-batch rejection, empty
reasons, exhausted/no-output decisions, return from Scout and forbidden plan steps.
No live task replay or manual Brain restart; production 8767 remains untouched.

Validation: 49 focused/adjacent backend tests passed across the final runs, including
the compiled plan/approval/execution flow with an explicit Scout skip. The original
fixture assumed immediate proposal; it was updated to exercise the required decision.
