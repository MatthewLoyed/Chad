"""AG-UI-inspired wire payloads for Brain → Limb thinking events."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


ACTIVITY_TYPE_PLAN = "PLAN"
ACTIVITY_TYPE_TOOLS = "TOOLS"


def build_activity_plan_snapshot(
    *,
    message_id: str,
    plan: List[Dict[str, Any]],
    goal: Optional[str] = None,
    replace: bool = True,
    gather_brief: Optional[str] = None,
) -> Dict[str, Any]:
    """ActivitySnapshot-shaped plan event (AG-UI compatible)."""
    content: Dict[str, Any] = {
        "steps": plan,
        "goal": goal,
    }
    if gather_brief:
        content["gather_brief"] = gather_brief
    return {
        "type": "activity_snapshot",
        "message_id": message_id,
        "activity_type": ACTIVITY_TYPE_PLAN,
        "content": content,
        "replace": replace,
    }


def build_activity_tools_snapshot(
    *,
    message_id: str,
    tools: List[str],
    shortlist_size: Optional[int] = None,
    selection_path: Optional[str] = None,
    replace: bool = True,
) -> Dict[str, Any]:
    """ActivitySnapshot-shaped tool-menu event for the wait-theater UI."""
    content: Dict[str, Any] = {"tools": tools}
    if shortlist_size is not None:
        content["shortlist_size"] = shortlist_size
    if selection_path:
        content["selection_path"] = selection_path
    return {
        "type": "activity_snapshot",
        "message_id": message_id,
        "activity_type": ACTIVITY_TYPE_TOOLS,
        "content": content,
        "replace": replace,
    }


def build_run_started(*, run_id: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "run_started",
        "run_id": run_id,
        "thread_id": thread_id or run_id,
    }


def build_run_finished(
    *,
    run_id: str,
    response: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "run_finished",
        "run_id": run_id,
    }
    if response is not None:
        payload["response"] = response
    return payload
