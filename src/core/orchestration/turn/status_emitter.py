"""Transient turn narration → EventBus.

Everything the user sees *before* the first token: rotating phase labels, the
resolved tool-menu chips, and the wait-narrator lines. All of it is cosmetic and
strictly non-fatal — a failure here must never take down a turn, so every publish
is wrapped and swallowed.

Split out of ``TurnCoordinator`` because it shares nothing with turn execution
but an ``event_bus`` handle: it reads no turn state and writes none, which is
what makes it safe to call from anywhere in the pipeline.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.shared.logger import setup_logger
from src.core.orchestration.turn.status_labels import PHASE_LABELS

if TYPE_CHECKING:
    from src.application.events.event_bus import EventBus

logger = setup_logger("TurnStatusEmitter")


class TurnStatusEmitter:
    """Publishes transient per-turn UI status to the EventBus."""

    def __init__(self, event_bus: Optional["EventBus"] = None, tool_registry: Any = None):
        self.event_bus = event_bus
        self.tool_registry = tool_registry

    def status(self, label: str, delivery: Optional[str] = None) -> None:
        """Publish a transient phase label (e.g. "Selecting tools…", "Loading gemma…")
        to the live chat working indicator. Cheap and non-fatal — keeps the pre-first-token
        gap from looking frozen. Carries the turn_id so a voice turn can correlate the
        label back to its pending future and speak it as narration."""
        if not self.event_bus:
            return
        try:
            from src.application.events.events import StatusUpdate
            from src.core.orchestration.context import turn_id_var
            self.event_bus.publish(StatusUpdate(
                status=label,
                delivery=delivery,
                client_turn_id=turn_id_var.get(),
            ))
        except Exception:
            pass

    def phase(self, phase: str, delivery: Optional[str] = None) -> None:
        """Emit a random label for a named pre-model pipeline step."""
        labels = PHASE_LABELS.get(phase)
        if labels:
            self.status(random.choice(labels), delivery)

    def tools_menu(
        self, selection: Optional[Dict[str, Any]], delivery: Optional[str] = None
    ) -> None:
        """Publish the resolved per-turn tool menu for the wait-theater chip carousel.

        ``selection`` is the coordinator's last tool-selection dict. Taking it as an
        argument (rather than reading it off the coordinator) is what lets this class
        stay stateless.
        """
        if not self.event_bus:
            return
        sel = selection or {}
        # Display only extra (query-selected) tools on the GUI, omitting permanent core tools
        extra_tools = sel.get("extra_tool_names")
        if extra_tools is None:
            tool_names = sel.get("final_tool_names") or []
            core_names = set(self.tool_registry._core_tool_names) if (self.tool_registry and hasattr(self.tool_registry, "_core_tool_names")) else {
                "ask_user_question", "search_for_tool", "list_skills", "load_skill",
                "call_mcp_tool", "manage_connectors", "run_saved_routine",
                "manage_user_memory", "web_search",
            }
            extra_tools = [t for t in tool_names if t not in core_names]
        if not extra_tools:
            return
        try:
            from src.application.events.events import ToolsMenuEvent
            from src.core.orchestration.context import turn_id_var, session_id_var
            self.event_bus.publish(ToolsMenuEvent(
                tools=list(extra_tools),
                shortlist_size=sel.get("shortlist_size"),
                selection_path=sel.get("path"),
                turn_id=turn_id_var.get() or session_id_var.get(),
                delivery=delivery,
            ))
        except Exception:
            pass

    async def wait_narrator(self, prompt: str, delivery: Optional[str] = None) -> None:
        """Generate query-tailored wait lines when a fast local model is already resident."""
        try:
            from src.core.wait_theater.narrator import try_generate_narrator_lines
            from src.application.events.events import WaitNarratorEvent
            from src.core.orchestration.context import turn_id_var, session_id_var

            lines = await try_generate_narrator_lines(prompt, tool_registry=self.tool_registry)
            if not lines or not self.event_bus:
                return
            self.event_bus.publish(WaitNarratorEvent(
                lines=lines,
                turn_id=turn_id_var.get() or session_id_var.get(),
                delivery=delivery,
            ))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


__all__ = ["TurnStatusEmitter"]
