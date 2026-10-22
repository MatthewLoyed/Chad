# Direct tool approval continuation

## Behavior

A GUI approval now resumes the exact checkpointed atomic call before any model inference. The pipeline bypasses semantic cache, mode routing, tool selection and history compaction. It retains the saved system context and loaded tools. The normal ToolNode/executor gate consumes the decision and appends the real ToolMessage before the supervisor continues its ReAct loop. Decline returns the normal declined result without executing the action. Already completed sibling calls are not replayed.

The tools wrapper records the original call/arguments in pending_approval, including calls originating inside specialists. Legacy tool-style handoffs preserve those fields. The event consumer no longer breaks on the first pending tool event: tool nodes and specialist adapters finish and checkpoint their pause through their existing END routes.

## UI fixes

- Approval follow-up preserves streamConversationId (falling back to the card or active chat). Desktop bridge continuation does not pass through frontend sendInput, so clearing this owner hid otherwise arriving thoughts/actions.
- Desktop and browser ignore a delayed final response for the previous turn without clearing the current emitter. Responses for another conversation also leave the active stream intact.

## Validation

Real in-memory LangGraph/checkpoint/event-stream regressions cover direct and nested specialist calls, exact arguments, execution before model invocation, no repeated siblings, decline, receipt visibility, and resumed thinking callbacks. Pipeline tests prove preexecution model stages are bypassed. UI tests cover stream ownership and stale completion isolation.

Focused approval suite: 48 passed. Broader pipeline/evidence/research suite: 57 passed (overlapping direct-resume tests). Frontend focused tests and TypeScript build check pass. No live GUI behavior run or external writes were performed for this patch. No Brain services were restarted; production 8767 was untouched.

Existing pauses made before this patch may lack the saved atomic call; use a fresh task run to exercise the new continuation contract.
