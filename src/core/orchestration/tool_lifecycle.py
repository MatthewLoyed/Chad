"""Shared ToolStarted / ToolCompleted emission for in-process tool loops.

Planner and Scout (and any future limb) that call tools outside ToolCoordinator
must publish the same EventBus lifecycle so GUI + evals see every tool.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from src.core.orchestration.context import current_turn_delivery, parent_correlation_id_var

logger = logging.getLogger("ChadSupervisor")


def _event_bus():
    from src.application.events.event_bus import EventBus

    return EventBus.get_instance()


def wire_tool_result_fields(raw: Any) -> Dict[str, Any]:
    """Normalize a tool return value for WS / eval capture.

    Always preserves full bodies under ``content`` / ``gather_brief`` / ``brief`` /
    ``steps`` when present — UI may still show a short ``result`` string.
    """
    if not isinstance(raw, dict):
        return {"result": raw, "success": True, "error": None}
    out: Dict[str, Any] = {
        "success": bool(raw.get("success", True)),
        "error": raw.get("error"),
        "result": raw.get("result", raw),
    }
    for key in (
        "content",
        "gather_brief",
        "brief",
        "steps",
        "gather_brief_chars",
        "tool_count",
        "_propose_plan",
        "_dispatch_scout",
        "_pending_approval",
    ):
        if key in raw:
            out[key] = raw[key]
    return out


def publish_tool_started(
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    correlation_id: Optional[str] = None,
    display_text: Optional[str] = None,
    parent_id: Optional[str] = None,
    event_bus: Any = None,
) -> str:
    """Publish ToolStarted; returns correlation_id used."""
    from src.application.events import ToolStarted

    bus = event_bus or _event_bus()
    cid = correlation_id or f"{tool_name}_{uuid.uuid4().hex[:10]}"
    pid = parent_id if parent_id is not None else parent_correlation_id_var.get()
    if not bus:
        return cid
    bus.publish(
        ToolStarted(
            tool_name=tool_name,
            args=dict(args or {}),
            correlation_id=cid,
            display_text=display_text,
            parent_id=pid,
            delivery=current_turn_delivery(),
        )
    )
    return cid


def publish_tool_completed(
    tool_name: str,
    result: Any,
    *,
    success: Optional[bool] = None,
    correlation_id: Optional[str] = None,
    display_text: Optional[str] = None,
    parent_id: Optional[str] = None,
    event_bus: Any = None,
) -> None:
    """Publish ToolCompleted with full payload (via wire_tool_result_fields)."""
    from src.application.events import ToolCompleted

    bus = event_bus or _event_bus()
    if not bus:
        return
    fields = wire_tool_result_fields(result)
    ok = fields["success"] if success is None else bool(success)
    pid = parent_id if parent_id is not None else parent_correlation_id_var.get()
    bus.publish(
        ToolCompleted(
            tool_name=tool_name,
            result=fields,
            success=ok,
            error=fields.get("error"),
            correlation_id=correlation_id,
            display_text=display_text,
            parent_id=pid,
            delivery=current_turn_delivery(),
        )
    )


async def run_with_tool_lifecycle(
    tool_name: str,
    args: Optional[Dict[str, Any]],
    runner: Callable[[], Awaitable[Any]],
    *,
    correlation_id: Optional[str] = None,
    display_text: Optional[str] = None,
    parent_id: Optional[str] = None,
    event_bus: Any = None,
) -> Any:
    """ToolStarted → await runner → ToolCompleted (success or error payload)."""
    bus = event_bus or _event_bus()
    cid = publish_tool_started(
        tool_name,
        args,
        correlation_id=correlation_id,
        display_text=display_text,
        parent_id=parent_id,
        event_bus=bus,
    )
    try:
        raw = await runner()
    except Exception as exc:
        logger.warning("[ TOOL_LIFECYCLE ] %s failed: %s", tool_name, exc)
        raw = {"success": False, "error": str(exc)}
        publish_tool_completed(
            tool_name,
            raw,
            success=False,
            correlation_id=cid,
            display_text=display_text,
            parent_id=parent_id,
            event_bus=bus,
        )
        raise
    publish_tool_completed(
        tool_name,
        raw,
        correlation_id=cid,
        display_text=display_text,
        parent_id=parent_id,
        event_bus=bus,
    )
    return raw
