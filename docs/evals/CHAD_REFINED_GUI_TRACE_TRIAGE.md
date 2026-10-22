# Refined GUI Meet / Prism trace — October 19, 2026

> Privacy: contact names in examples and task quotations use fictional aliases.
> Historical measurements are retained from the original runs.

## Outcome and provenance

The refined request improved execution enough to **start a Google Meet session**.
The browser agent then discarded that success, navigated away, generated another
room URL, exhausted its browser budget, and pursued Jordan through public search.
No Gmail draft, email send, Prism launch, desktop delegation, or `complete_step`
call appears before the user stopped the run. Prism was unattempted, not proven broken.

This was **LFM2.5-24B-A2B** in the planner, supervisor and browser roles, not Bonsai.
It predates the model-picker alias repair. One differently worded run does not
establish a controlled improvement over the original request.

- Conversation: `94f0b65e-d2c9-4883-a57f-8fb463c16bea`, dev GUI.
- Window: 15:15:42–15:25:30 America/Chicago; execution resumed after `proced` at
  15:17:40. Cancellation is explicit at 15:25:30.
- Ignored private packet: `.agent/experiments/chad-refined-gui-trace/`. Contains
  56 outbound prompts, 50 distinct tool-call IDs joined to results, a numbered
  Brain log window, copied browser artifacts, hashes, extraction and offline audit.
- `tools.json` indices below are evidence record IDs in first-observed order,
  **not execution ordering**. Their times are subsequent outbound prompt times.
  Execution timestamps below come from Brain logs. Delegations enter outer
  history after their inner calls.
- Prompt dump headers contain time only across runs. Selection required the exact
  refined request plus the matching log window. Schemas are logged as counts;
  not every parameter contract can be reconstructed from these dumps.
- Meeting codes, addresses and screenshots remain private. Root `snapshot1.yml`
  was reused by the second delegation; copied timestamped files preserve evidence.

Read-only saved-evidence analysis. No application repairs, task replay, server
restart, or production interaction occurred during this analysis.

## Decisive sequence

1. **15:19:50–52:** clicking “Start an instant meeting” changes `/home` to a
   room-code URL (record 16).
2. **15:19:54–56:** a scroll result contains the code heading, “Your meeting's
   ready,” share link, “Leave call,” and participant count one (18). The saved
   15:20:04 screenshot independently shows the live call and same share link.
3. **15:20:52:** the agent navigates home. Its **15:22:31** report says no meeting
   was created and calls that same room pre-existing (36).
4. **15:23:07–08:** another instant-meeting click generates a different room URL
   (43). The next snapshot is blocked at 31/30 browser actions (44). This proves
   another URL was generated; the second room's joined state was not inspected.
5. **15:24:14 / 15:24:32:** public searches seek Jordan's contact and Gmail lookup
   instructions (46–47). At **15:24:41** the supervisor delegates Gmail browsing
   despite the exhausted browser budget; navigation is blocked (48–49).
6. **15:25:30:** user cancellation interrupts generation; no final completion exists.

## Prioritized findings

### P1 — MCP block-type mismatch bypasses snapshot hydration

Confirmed in model inputs: navigation, clicks and filename snapshots often return
only `[Snapshot](...)` links (6–7, 9, 13–14, 16–17). Scroll and snapshots without
filenames return inline trees. This wastes calls and weakens action grounding.

Source: `src/core/tools/mcp_registry.py:268` converts MCP content to dictionaries
using `model_dump()`. In `src/infrastructure/tools/execution/executor_strategy.py:853`,
file hydration, pruning and Set-of-Mark rendering run only in the object-with-`.text`
branch. The dictionary-text branch at line 928 only truncates. Normal MCP blocks
therefore bypass those transformations. Silent `except Exception: pass` also hides
file hydration failures in the object branch; that is a separate observability gap.

Repair target: normalize block types before processing; make file/inline snapshots
equivalent and expose file-read failures. Verify dictionary and object fixtures
produce the same hydrated content and actionable refs.

### P1 — Created meeting is forgotten and falsely reported as pre-existing

The first specialist had the code in the URL and full textual proof of success,
then navigated away and reported the opposite. Lack of product knowledge alone
does not explain this failure: the refined request, ready dialog, share text and
call controls jointly establish what happened.

The parent receives only specialist prose (`src/core/orchestration/handoffs.py:1144`),
so the incorrect summary replaces stronger observation evidence at handoff. No
structured created-room artifact with originating call IDs accompanies it. An old
invitation in activity context could have contributed to the pre-existing-room
claim, but that is a hypothesis, not a demonstrated cause.

Repair target: preserve created artifacts and supporting call IDs across handoff;
check completion before another mutation. Offline replay should retain the first
room as a success, advance the plan, and forbid unnecessary new meeting creation.

### P1 — Terminal budget and partial outcome are lost at handoff

The executor correctly blocks calls over its configured budget. The specialist
gets a synthesis-only turn. Yet after the shared budget is exhausted, the parent
delegates more browser work.

`agents/specialist_graph.py:61` and `nodes/tool_node.py:178` handle `budget_exceeded`.
`handoffs.py:1144` reduces the result to prose; its output at line 1230 forwards
pending approvals but not the specialist's budget state. At line 1201, delegate
failure depends on `failure_signature(content)`, which recognizes `TOOL_FAILED[...]`,
not ordinary prose such as “The task did not complete.” Reports 36, 45 and 49 lack
that marker, so this path can label partial/failed work successful and reset the
handoff failure counter.

Distinguish the first delegation's local step cap from the later conversation-wide
browser kill-switch. A local cap can permit another bounded delegation; an exhausted
shared browser budget cannot. Forward domain-scoped status, remaining budget and
partial artifacts. Verify a terminal browser budget blocks further browser
delegation while independent desktop work remains runnable.

### P1 — Invalid plan status skips an unfinished step

`propose_plan` accepts Step 1 as `in_progress` (3). Supervisor prompts show that
unfinished status and zero of five complete, but declare Step 2 active throughout.

`src/tools/system/propose_plan.py:60` allows arbitrary status strings and returns
them unchanged. `_normalize_plan_steps` in `nodes/planner_node.py:74` only defaults
an absent status. `_next_actionable_step` in `nodes/plan_parser.py:98` recognizes
`pending`/`running`; binding in `nodes/supervisor_node.py:290` also excludes
`in_progress`. The private audit reproduces this with the two pure source functions:
normalization preserves the invalid status and the next actionable step is `2`.
No Brain or external actions are involved in that reproduction.

Repair target: new plans start pending; validate statuses at the boundary and share
one contract across planner, executor and UI. Invalid/alias/null statuses must not
silently skip work or strand the plan.

### P2 — Draft intent and selected capabilities disagree

The initial shortlist includes `create_gmail_draft`, but selection chooses
`send_email`. The plan names `send_email` on its “draft the email” step. On `proced`,
the shortlist again includes draft/contact/Gmail tools, but selection changes to
`open_app` and unrelated `google_drive_create_doc` (logs 15:17:47–52). The final
selector menu lacks drafting. No send occurred; the email step was never reached,
and dynamic discovery remained possible.

Repair target: retain draft intent through plan validation and continuation; preserve
capabilities required by the accepted plan. Active-step binding cannot ensure correct
email behavior when the plan names the wrong tool. Verify a draft case never invokes
send and does not acquire unrelated Drive-document work on approval/resume.

### P2 — Private person resolution uses the wrong source

No Jordan email is delivered in observed personal context. Relationship facts about
Jordan are present, along with unrelated activity-email snippets. The system context
still says “No memories relevant to the prompt were found,” a stale negative claim
alongside relevant relationship context. Those relationship facts do not establish
the email address.

No local memory/contact/Gmail lookup is attempted. Instead the supervisor searches
the public web, then asks browser delegation to inspect Gmail account headers,
which do not establish the intended recipient's identity. No wrong address was sent.

This proves missing fact delivery and wrong resolver choice, not that persona lacks
the address or that a specific cutoff removed it. Next bounded investigation:
follow the known local person/email fact through matching, retrieval, formatting
and actual prompt assembly. Keep public-product grounding separate from private
contact resolution; require provenance for the recipient address.

### P2 — Depth and repetition feedback undermine fresh observations

- Depth-five snapshots omit nested “New meeting” controls; depth-three collapses
  most of the main area/dialog (23, 25, 28, 33). Full-depth snapshots expose them
  (40, 42). A partial tree cannot establish that a button does not exist.
- Repetition hints claim the result is already available even after page state
  changes (18, 25, 33). `src/core/tools/repetition.py:174` uses tool+arguments without
  a page/version identity. Identical arguments can produce necessary fresh state;
  relative scrolls also have cumulative effects.
- One stale ref is reused after navigation (30); the executor's stale-ref guidance
  then prompts a refresh. Preserve that working recovery.
- `[PAGE SURFACE: unknown]` is expected for Meet under the inbox/detail-oriented
  classifier (`page_surface.py:106`), not evidence of failed page access.

Repair target: disclose partial-tree coverage, retain actionable descendants and
make repetition advice page-state aware. A snapshot after a real transition must
be allowed to refresh evidence without a misleading reuse hint.

### P2 — Screenshots become truncated base64 text

One screenshot fails because required `scale` is omitted (10). This error does
**not** establish that `fullPage` was unsupported. Two corrected calls succeed
(11, 19), but their model inputs contain JSON with base64 image data cut by a
truncation marker. Across 56 prompt blocks there are zero multimodal content arrays.

`MCPExecutor` keeps image dictionaries inside its ordinary result envelope;
`nodes/tool_node.py:81` clips the serialized string, leaving incomplete JSON.
The adapter's content-block conversion (`litellm_adapter.py:1165`) expects a different
input shape. Screenshot success therefore did not yield a model-readable image.

Repair target: genuine multimodal delivery to a capable model, or deliberate
OCR/vision-assist text. Verify the final outbound payload, not just capture success;
never spend text context on raw base64.

## Repair order and verification boundary

1. Normalize MCP observation processing and screenshot transport with offline fixtures.
2. Enforce plan statuses/draft intent; preserve terminal browser status and partial
   artifacts across delegation.
3. Replay the saved success sequence offline: creation must remain a success after
   summary, without a navigation reset or another creation attempt.
4. Trace local Jordan resolution independently; make observation feedback state-aware.
5. Only then run a bounded full-dev `:8788` replay with effective supervisor **and
   specialist** model IDs recorded. Require a verified meeting artifact, addressed
   Gmail draft and evidence of the requested Prism instance launch. Never restart
   production `:8767`.

Increasing the browser budget or rewriting the user prompt again would obscure
the concrete defects. Qwen review improvements and Bonsai comparisons remain useful,
but neither repairs missing observations or dropped state by itself.
