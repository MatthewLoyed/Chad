# Design Spec: Simple Ask Mode (Total Bypass)

*   **Author:** Antigravity
*   **Date:** 2026-05-10
*   **Status:** Proposed

## Problem Statement
Users frequently want to use Chad for pure informational questions or casual chat. Currently, Chad routes these requests through the Supervisor node (the "Doing" loop) which incurs heavy latency due to loading skills, binding numerous tools, and potentially performing multiple reasoning iterations. While a cheap regex heuristic exists to catch trivial inputs, it fails for complex purely conversational queries.

## Goal
Implement a "Simple Ask Mode" that allows the user to explicitly instruct the backend to bypass the heavy ReAct supervisor and use the existing, high-speed `conversational_responder` directly.

## Proposed Solution (Approach 1: Total Bypass)
We will introduce an explicit toggle in the UI. When enabled, it overrides the turn category to `conversation`. The backend graph entry router will prioritize this explicit category signal over everything else, bypassing the full orchestration logic.

### 1. Frontend Changes (Web Client)
*   **File:** `src/clients/web/src/components/layout/ChatInput.tsx`
*   **UI Component:** Add a toggle next to the textarea input (visual indicator of "Ask Mode" vs "Action Mode").
*   **Event Logic:** When the toggle is active, inject `{ category: "conversation" }` into the WebSocket `USER_INPUT` message payload.

### 2. Backend Gateway Changes (Headless Brain)
*   **File:** `src/remote/server.py`
    *   Update `route_message` to pull `category` from the incoming payload: `msg.payload.get("category", "autonomous")`.
    *   Pass this category to `self.turn_coordinator.dispatch()`.
*   **File:** `src/core/orchestration/turn_coordinator.py`
    *   Update `dispatch()` signature to accept a `category: str = "autonomous"` parameter.
    *   Use this parameter instead of hardcoding `'autonomous'` when calling `self.orchestrator.process_request()`.

### 3. Graph Logic (Supervisor)
*   **File:** `src/core/orchestration/supervisor.py`
    *   Modify the `route_entry` function:
        ```python
        def route_entry(state: Dict[str, Any]) -> str:
            # 1. Prioritize explicit User Intent
            if state.get("category") == "conversation":
                logger.info("[ ROUTE ] User forced conversation category. Bypassing supervisor.")
                return "conversational"
            
            # 2. Fallback to original regex heuristic
            if is_conversational(state["messages"]):
                logger.info("[ ROUTE ] Cheap heuristic detected conversation. Fast-tracking.")
                return "conversational"
                
            return "supervisor"
        ```

## Verification Plan
1.  **Test Case 1 (Action Mode)**: Type "what is the weather?" with toggle OFF. Verify logs show Supervisor routing to tools or specialist.
2.  **Test Case 2 (Ask Mode)**: Type "what is the weather?" with toggle ON. Verify logs show it immediately hits `conversational_responder` and replies without tools.
3.  **Test Case 3 (Heuristic)**: Type "hi" with toggle OFF. Verify it still utilizes the regex heuristic to fast-track.

## Rollback Plan
This change has zero database schema dependencies. Rollback is trivial git revert of affected gateway and router files.
