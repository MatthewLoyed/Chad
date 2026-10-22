"""
Executor-agnostic human-in-the-loop approval gate.

This is the single place that decides whether a tool call should pause for a
human accept/decline (or choice/questions) decision, and that owns the
register -> resolve -> consume handshake against `approval_store`. It is shared
by every executor (Local, MCP, Skill) so the `requires_approval` primitive
behaves identically no matter how a tool is dispatched.

Two ways a call gets gated:
  1. A native BaseTool opts in explicitly (`wants_approval()` / `requires_approval`).
  2. A risk-based policy in `config/tool_policies.yaml` matches the tool name
     (covers MCP and skill tools, which have no Python class to set the flag).

Resume mechanics: when paused, `evaluate_approval_gate` records the request and
returns a `_pending_approval` result dict. The UI surfaces it (ApprovalCard),
the user's decision is recorded via `approval_store.resolve`, and the harness
replays the exact checkpointed tool call before invoking the model — at which point this gate sees the
resolved decision and either proceeds (returns a GateOutcome with the decision)
or short-circuits with "User declined."
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from src.shared.logger import setup_logger

logger = setup_logger("ApprovalGate")

_DECLINE_DECISIONS = ("declined", "decline", "rejected", "no")


def _preview_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Approval payloads cross JSON/checkpoint/UI boundaries; exclude runtime objects."""
    import json
    preview = {}
    for key, value in (params or {}).items():
        if key.startswith("_") or key == "tool_registry":
            continue
        try:
            preview[key] = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError, OverflowError):
            continue
    return preview


@dataclass
class GateOutcome:
    """
    Result of consulting the approval gate for one tool call.

    - `short_circuit` not None  -> the executor must return it immediately
      (either a pending-approval pause or a user-declined result).
    - `short_circuit` None and `decision` set -> the call was approved; the
      executor may proceed and (for native tools) inject the decision/token.
    - `short_circuit` None and `decision` None -> no gate applies; proceed.
    """

    short_circuit: Optional[Dict[str, Any]] = None
    decision: Optional[str] = None
    token: Optional[str] = None


def _default_request(tool_name: str) -> Dict[str, Any]:
    return {"prompt": f"Allow {tool_name} to proceed?", "options": None}


def evaluate_approval_gate(
    tool_name: str,
    *,
    decision_type: str = "accept_decline",
    build_request: Optional[Callable[[], Dict[str, Any]]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> GateOutcome:
    """
    Consult the approval store for `tool_name` in the current conversation.

    Args:
        tool_name: Registry name of the tool being executed.
        decision_type: "accept_decline" | "choice" | "questions".
        build_request: Optional zero-arg builder returning the human-facing
            request ({"prompt", "options", "title", "questions"}). Defaults to
            a generic accept/decline prompt. Only called when a fresh request
            must be minted (not on the resolved re-invocation).

    Returns:
        GateOutcome — see the dataclass docstring.
    """
    from src.core.orchestration import approval_store
    from src.core.tools import failure
    from src.core.orchestration.context import (
        pending_approval_var,
        session_id_var,
        turn_category_var,
    )

    # Telegram pocket remote: allowlisted user already authenticated the bot.
    # GUI ApprovalCards cannot be tapped from Telegram — do not pause write tools.
    if turn_category_var.get() == "telegram_agent":
        if tool_name == "manage_user_memory":
            return GateOutcome(short_circuit=failure.failure(
                "Memory changes require approval in the desktop app. No memory was changed.",
                tool_name=tool_name,
                failure_class=failure.BLOCKED,
                next_actions=["Ask the user to review this memory change in the desktop app.", "Do not retry this mutation from Telegram."],
            ))
        logger.info(
            f"[ APPROVAL ] Skipping GUI approval for '{tool_name}' on telegram_agent lane"
        )
        return GateOutcome(short_circuit=None, decision="approved", token=None)

    conversation_id = session_id_var.get()
    existing = approval_store.find_pending(tool_name, conversation_id)

    if existing is None:
        request = (build_request or (lambda: _default_request(tool_name)))()
        token = approval_store.mint_token()
        payload = {
            "token": token,
            "tool_name": tool_name,
            "conversation_id": conversation_id,
            "decision_type": decision_type,
            "prompt": request.get("prompt"),
            "options": request.get("options"),
            "title": request.get("title"),
            "questions": request.get("questions"),
            "params": _preview_params(params),
        }
        approval_store.register(token, payload)
        pending_approval_var.set(payload)
        logger.info(f"[ APPROVAL ] '{tool_name}' paused for human decision (token={token})")
        return GateOutcome(short_circuit=_pending_result(payload))

    token, entry = existing
    if entry["payload"].get("params", {}) != _preview_params(params):
        return GateOutcome(short_circuit=failure.failure(
            "Tool arguments changed after the approval request.",
            tool_name=tool_name,
            failure_class=failure.BLOCKED,
            next_actions=["Resume the original reviewed call before requesting another change.", "Do not reuse this approval for different arguments."],
        ))
    if not entry["resolved"]:
        pending_approval_var.set(entry["payload"])
        return GateOutcome(short_circuit=_pending_result(entry["payload"]))

    decision = entry["decision"]
    approval_store.consume(token)
    if decision in _DECLINE_DECISIONS:
        logger.info(f"[ APPROVAL ] '{tool_name}' declined by user (token={token})")
        return GateOutcome(
            short_circuit={"success": True, "result": "User declined.", "_approval_declined": True}
        )

    logger.info(f"[ APPROVAL ] '{tool_name}' approved by user (token={token}, decision={decision!r})")
    return GateOutcome(decision=decision, token=token)


def _pending_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the executor result that pauses the turn and feeds the UI card."""
    return {
        "success": True,
        "result": payload["prompt"],
        "_pending_approval": True,
        "approval_token": payload["token"],
        "decision_type": payload["decision_type"],
        "options": payload.get("options"),
        "title": payload.get("title"),
        "questions": payload.get("questions"),
        "tool_name": payload.get("tool_name"),
        "params": payload.get("params"),
    }
