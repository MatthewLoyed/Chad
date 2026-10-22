from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger
from src.shared.plan_markers import strip_orchestration_markers

logger = setup_logger("TurnEventCapture")


class TurnEventCapture:
    """Captures the thinking/tool/speech event stream for a single turn.

    Used as a context manager: it subscribes to the relevant EventBus events on
    enter, accumulates a flat, UI-ready event list, and unsubscribes every
    handler on exit (the previous inline version leaked its ExecutionPlanEvent
    subscription). Call :meth:`consolidate` to get the merged, marker-stripped
    list for ``last_turn_thinking_events``.
    """

    def __init__(self, event_bus: Optional[Any]) -> None:
        self.event_bus = event_bus
        self.events: List[Dict[str, Any]] = []
        self._subscriptions: List[tuple] = []

    def __enter__(self) -> "TurnEventCapture":
        if not self.event_bus:
            return self

        from src.application.events import (
            ThinkingUpdate,
            ToolStarted,
            ToolCompleted,
            ModelLoading,
            ResponseFragment,
        )
        from src.application.events.events import ExecutionPlanEvent, TurnCompleteEvent, ToolsMenuEvent

        bindings = [
            (ThinkingUpdate, self._on_thinking_update),
            (ToolStarted, self._on_tool_start),
            (ToolCompleted, self._on_tool_complete),
            (ModelLoading, self._on_model_loading),
            (ResponseFragment, self._on_response_fragment),
            (TurnCompleteEvent, self._on_turn_complete),
            (ExecutionPlanEvent, self._on_execution_plan),
            (ToolsMenuEvent, self._on_tools_menu),
        ]
        for event_type, handler in bindings:
            self.event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self.event_bus:
            for event_type, handler in self._subscriptions:
                self.event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        return False

    # --- handlers -------------------------------------------------------------

    def _on_thinking_update(self, event) -> None:
        f_type = "reasoning" if event.thinking_type == "reasoning" else "thought"
        self.events.append({
            "type": f_type,
            "text": event.thinking_text,
            "append": event.append,
            "agent_name": event.agent_name,
            "parent_id": event.parent_id,
            "timestamp": int(time.time() * 1000),
        })

    def _on_tool_start(self, event) -> None:
        self.events.append({
            "type": "tool_start",
            "correlation_id": event.correlation_id,
            "name": event.tool_name,
            "params": event.args,
            "display_text": event.display_text or f"Executing {event.tool_name}...",
            "parent_id": event.parent_id,
            "timestamp": int(time.time() * 1000),
        })

    def _on_tool_complete(self, event) -> None:
        end_event = {
            "type": "tool_end",
            "correlation_id": event.correlation_id,
            "name": event.tool_name,
            "success": event.success,
            "result": event.result if event.success else event.error,
            "parent_id": event.parent_id,
            "timestamp": int(time.time() * 1000),
        }
        raw = event.result if isinstance(event.result, dict) else {}
        if raw.get("download_token"):
            end_event["download_token"] = raw.get("download_token")
        if raw.get("filename"):
            end_event["filename"] = raw.get("filename")
        from src.tools.media.show_youtube_video import youtube_display_payload
        from src.tools.maps.osm_tools import map_display_payload

        end_event.update(youtube_display_payload(raw))
        end_event.update(map_display_payload(raw))
        self.events.append(end_event)

    def _on_model_loading(self, event) -> None:
        self.events.append({
            "type": "model_loading",
            "name": event.model_name,
            "provider": event.provider,
            "timestamp": int(time.time() * 1000),
        })

    def _on_response_fragment(self, event) -> None:
        self.events.append({
            "type": "speech",
            "text": event.text,
            "append": True,
            "timestamp": int(time.time() * 1000),
        })

    def _on_turn_complete(self, event) -> None:
        self.events.append({
            "type": "turn_complete",
            "response": event.response,
            "timestamp": int(time.time() * 1000),
        })

    def _on_execution_plan(self, event) -> None:
        self.events.append({
            "type": "execution_plan",
            "plan": event.plan,
            "plan_id": event.plan_id,
            "goal": event.goal,
            "gather_brief": getattr(event, "gather_brief", None),
            "timestamp": int(time.time() * 1000),
        })

    def _on_tools_menu(self, event) -> None:
        tools = getattr(event, "tools", None) or []
        if not tools:
            return
        self.events.append({
            "type": "tool_menu",
            "tools": list(tools),
            "shortlist_size": getattr(event, "shortlist_size", None),
            "selection_path": getattr(event, "selection_path", None),
            "timestamp": int(time.time() * 1000),
        })

    # --- post-processing ------------------------------------------------------

    def consolidate(self) -> List[Dict[str, Any]]:
        """Merge consecutive speech blocks and strip orchestration markers."""
        return consolidate_thoughts(self.events)


def consolidate_thoughts(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge consecutive speech blocks and strip orchestration markers.

    Standalone so it can be unit-tested without an EventBus.
    """
    if not events:
        return []

    consolidated: List[Dict[str, Any]] = []
    current_speech: List[str] = []

    for event in events:
        if event.get("type") == "speech":
            current_speech.append(event.get("text", ""))
        else:
            if current_speech:
                full_text = "".join(current_speech)
                clean_full = strip_orchestration_markers(full_text)
                if clean_full:
                    consolidated.append({
                        "type": "speech",
                        "text": clean_full,
                        "timestamp": event.get("timestamp", int(time.time() * 1000)),
                    })
                current_speech = []

            # Sanitize text in other event types if applicable
            if event.get("type") in ["thought", "thinking", "reasoning"] and "text" in event:
                event = event.copy()
                event["text"] = strip_orchestration_markers(event["text"])

            consolidated.append(event)

    if current_speech:
        full_text = "".join(current_speech)
        clean_full = strip_orchestration_markers(full_text)
        if clean_full:
            consolidated.append({
                "type": "speech",
                "text": clean_full,
                "timestamp": int(time.time() * 1000),
            })

    return consolidated
