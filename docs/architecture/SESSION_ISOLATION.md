# Session Isolation Contract

**Issue:** #269 · **Status:** enforced · **Owners:** Limb (web + desktop) + Brain coordinator

Chat sessions in Chad must be **hermetically isolated**. Anything that happens in
one conversation — a streaming answer, a finalized bubble, and *especially* an
interactive approval / clarifying-question card — must never appear in, or leak
onto, another conversation. Clicking between chats shows the *history* of the
selected chat and nothing live from any other chat.

This document is the single source of truth for how that guarantee is made and
where it is enforced. If you touch turn state, approvals, or chat switching, read
this first.

## The bug this prevents

The web Limb keeps live turn state in one process-global store
(`src/clients/web/src/store.ts`). Historically `pendingToolApproval` — the buffer
that holds a clarifying-question card until the answer bubble finalizes — was a
single global field with **no conversation binding** and was **not cleared on chat
switch**. A card raised in chat A therefore survived navigation and got stamped
onto the next AI bubble finalized in whatever chat the user had clicked into,
making the same "Chad has a question" popup appear across multiple sessions.
Server-side turn state was already bucketed by `(session_id, delivery)`; the
client was the half that never got scoped.

## The contract

> **Every piece of live turn state carries the `conversation_id` it belongs to,
> and every point that would apply it to the visible chat verifies that id
> against the conversation on screen before doing so. A mismatch is dropped and
> recorded — never silently applied.**

Three properties, defense-in-depth, each independently sufficient to stop the
leak; together they make it "physically impossible":

1. **Tag at the source.** The Brain stamps `conversation_id` onto the approval
   message, exactly as it already does for `ai_response`.
2. **Guard at every boundary.** Each transport (desktop Limb, browser socket) and
   the store drop an approval whose conversation doesn't match the in-flight /
   active chat.
3. **Tear down on switch.** Selecting another chat (or starting a new one) resets
   *all* live turn state, including any buffered approval, so nothing can carry
   across even if a guard were bypassed.

## Enforcement points (the full path)

| # | Layer | File | What it does |
|---|-------|------|--------------|
| 1 | Brain coordinator | `src/remote/limb_coordinator.py` → `_broadcast_turn_complete_event` | Copies the bucket's `conversation_id` into the `TOOL_APPROVAL_REQUEST` payload (never mutates the event's dict). |
| 2 | Desktop Limb | `src/remote/gui_limb.py` → `handle_message` (`TOOL_APPROVAL_REQUEST`) | Drops the card if `conversation_id != pending_conversation_id`; forwards camelCase `conversationId` to the React store. |
| 3 | Browser transport | `src/clients/web/src/transport/brainSocket.ts` (`TOOL_APPROVAL_REQUEST`) | Same mismatch guard against the in-flight conversation; forwards `conversationId`. |
| 4 | Store — the choke-point | `src/clients/web/src/session/sessionGuard.ts` | `guardConversation(incoming, active, context)` — the one definition of "does this belong to the active chat?". Mismatches are **recorded** (`getSessionViolations()`) and logged. |
| 5 | Store — attach | `src/clients/web/src/store.ts` → `addChatMessage`, `addChatMessageWithActions`, `attachToolApprovalToLastAIMessage` | Only stamp a buffered approval onto a bubble if its `conversationId` matches `activeConversationId`; drop stale cards. |
| 6 | Store — teardown | `src/clients/web/src/store.ts` → `resetLiveTurnState` | Clears `pendingToolApproval` + `streamTurn` + `activeTurn` + `streamingResponse` + `liveStatus`. |
| 7 | App wiring | `src/clients/web/src/App.tsx` → `handleChatSelect`, `handleNewChat` | Calls `resetLiveTurnState()` on every switch / new chat. |

`conversation_id == null` is a **legacy escape hatch**: an untagged event is
treated as a match so pre-stamping emitters don't regress. This should trend to
zero — new emitters MUST tag.

## The standing invariant (why regressions are caught instantly)

Guard rejections are not silent. `getSessionViolations()` records every dropped
cross-conversation apply, and the browser logs each as a `console.error`. Tests
assert **a correct flow produces zero violations**, so any future refactor that
reintroduces a global, unscoped approval fails CI immediately instead of
resurfacing as a mystery popup.

### Tests that mandate this

| Test | Covers |
|------|--------|
| `src/clients/web/src/session/sessionGuard.test.ts` | The guard predicate + violation recording + loud logging. |
| `src/clients/web/src/store.session-isolation.test.ts` | Store attach only to the owning chat; teardown clears everything; zero violations on a clean flow. |
| `tests/unit/test_gui_limb_approval_isolation.py` | Desktop Limb drops mismatched cards, stamps camelCase `conversationId`, keeps legacy untagged path. |
| `tests/unit/test_coordinator_approval_isolation.py` | Brain stamps `conversation_id` on the approval message (the keystone) without mutating the event. |

Run them:

```powershell
# Frontend (web Limb)
cd src/clients/web; npx vitest run src/session/sessionGuard.test.ts src/store.session-isolation.test.ts
# Brain + desktop Limb
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_gui_limb_approval_isolation.py tests/unit/test_coordinator_approval_isolation.py -q
```

## Rules for future work

- **Never add a globally-scoped piece of live turn state.** If it renders in the
  active chat and originates from a turn, it must carry `conversation_id` and pass
  through `guardConversation` before it is applied.
- **New Brain→Limb turn messages must include `conversation_id`** when the bucket
  has one (mirror `ai_response` / `tool_approval_request`).
- **New chat-switch entry points must call `resetLiveTurnState()`** before showing
  another conversation.
- **Extending isolation to a new event type?** Add a case that routes through
  `guardConversation` and a test asserting zero violations on the happy path plus
  one recorded violation on a deliberate mismatch.
