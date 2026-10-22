# Latest long-prompt UI run — evidence and repairs

October 19, 2026. Conversation `5316b99e-fe02-442a-928b-c3cb909554b1`.
Planning 18:46–18:47; execution 18:49–19:00 America/Chicago. The user reports
successful outcomes in the GUI. This analysis preserves that observation while
distinguishing what the saved tools independently establish.

Private evidence: `.agent/experiments/chad-success-ui-triage/`. Hash-indexed
101 outbound prompts and 99 distinct tool-call IDs joined to results. Evidence
indices below refer to `tools.json` first-observed order, not execution order;
delegation summaries appear after their child calls. Model was Bonsai in the
supervisor/browser/UI roles; research/traversal retained their separate pins.

## What explains the UI friction

- First UI delegation targets a stale window handle. Later window discovery finds
  the actual Prism window; fresh selectors then succeed. Screenshot labels alone
  did not provide valid window identity.
- The supervisor repeats old annotation numbers as element IDs (26→37 and later
  delegations). The screenshot's display `index` and actual `id` differ. More
  fundamentally, the current MCP `ui_click`, `ui_type` and `ui_read` schemas do not
  accept `elementId` at all, despite generic server response hints suggesting it.
- Substring matching “Launch” can match the parent window or the release-news
  button. Record 75 actually clicks **Prism Launcher Release**, while report 78
  says the Launch button was clicked. Success of the input call is not proof of
  the intended target or game outcome.
- A “Low free memory” dialog blocks the launcher. The UI agent eventually finds
  and clicks Yes, restores focus and obtains new observations (60–67). Loading
  and warning dialogs are genuine app states, not proof the app is absent.
- Screenshot requests are blocked after three identical successful calls even
  though the UI changed. Records 70 and 74 are examples. This actively prevents
  verification and encourages the model to alter irrelevant arguments to get
  another observation.
- The effective local UI persona says “Max 3 tool calls per task,” contradicting
  runtime's larger local cap. Several reports invent a three-call cap despite
  more calls having run. The older overlay hides the newer generic baseline.

## Why the supervisor appeared to disbelieve the agents

Some reports contain real intermediate evidence; others overstate it. The parent
receives only prose, so it cannot reliably distinguish the two. It repeatedly
micro-delegates another launch rather than requesting one specific missing
postcondition. It then asks system and traversal specialists to find
`draftout.exe` (87, 97). A launcher instance name does not imply such an executable;
a Java/runtime process can have another name. Negative searches for that guessed
name cannot refute successful UI launch observations. The task escalates from UI
verification to an unnecessary whole-filesystem search.

The right repair is evidence-bearing reports and relevant verification, not
unconditional trust in every specialist summary.

## Additional confirmed approval defect

Record 13 is a pending send approval, **not a successful email send**. It includes
`tool_registry`, a live Python object injected internally. The result becomes a
Python repr rather than JSON; `_pending_approval` is no longer parsed by the tool
node. The graph advances and completes email steps (14–15) without a send result
or approval resolution in this capture. This was introduced by the prior preview
argument propagation and is repaired here. No email send was replayed to test it.

The browser also explicitly used “Create a meeting for later” (9), producing a
join link. That is not independent proof of an active joined call. These evidence
limits do not dispute what the user saw; they prevent the trace from being scored
as stronger proof than it contains.

## Implemented repairs

1. Approval previews exclude runtime objects, internal fields and non-JSON values.
   A compiled graph regression passes a real executor the same registry-object
   shape: approval stays valid JSON and halts before another model call or send.
2. Mutable desktop observations (`screenshot_control`, UI find/read and window
   discovery/state reads) can refresh after repeated success or failure. Window
   mutations retain repetition protection; loop hints and specialist caps remain.
3. Generic UI input operations no longer get `terminal=true` or the blanket
   “This action is definitive; report success” message. Their acknowledgement
   explicitly requires comparing the returned target and relevant postcondition.
4. UI handoffs now include bounded, parseable receipts from this delegation only:
   actual target names/types/IDs/enabled state, window state, success and call IDs.
   Older checkpointed work and image pixels are excluded. Both callable and
   graph-node handoff paths use this evidence.
5. Local-cap reports are retryable with a changed bounded approach; shared browser
   exhaustion remains terminal. Runtime synthesis reports the actual round count
   and distinguishes input success from verified outcome. Empty synthesis cannot
   claim the browser work was completed.
6. UI and supervisor guidance now distinguishes annotation indices from selectors,
   stale windows, misleading substring matches, loading dialogs and runtime process
   names. Remove the invented three-call prompt cap. Updated both tracked baselines
   and the matching ignored local overlays; backed up overlays privately and verified
   merged file resolution. Cached running graphs still need their normal prompt
   refresh/rebuild before the changed guidance is used.

## Verification and remaining work

Offline boundary and adjacent regressions cover the actual approval producer,
compiled pause behavior, screenshot refreshing, action acknowledgements and UI
handoff evidence: **98 tests passed**, and `git diff --check` passed. The effective
merged local UI/supervisor persona was also checked directly. No real meeting,
message, launch, search or UI action was repeated.
Production `:8767` was not restarted or otherwise controlled.

The external UI server still emits misleading element-ID hints and can return
surprising matches. This repair does not change that server's implementation or
prove its screenshot cache/coordinate correctness. Such behavior should be probed
separately with current handles and explicit selectors on dev `:8788` if needed.
Claim-to-step validation and automatic verification of active-call/game state remain
follow-up work. Keep the staged prompt-reduction plan after these harness repairs.
