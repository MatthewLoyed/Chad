from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.shared.logger import setup_logger

logger = setup_logger("TurnToolSelector")

# Workflow approval utterances -- must NOT re-run the LLM tool selector (it returns []).
PLAN_APPROVAL_UTTERANCES = frozenset({
    "proceed", "continue", "go ahead", "go", "yes", "y", "ok", "okay",
    "approve", "approved", "start", "do it", "run it", "execute", "sounds good",
})


@dataclass
class ToolSelection:
    """Result of per-turn tool selection.

    ``TurnCoordinator`` unpacks these onto the per-turn state it exposes
    (``_current_turn_is_simple``, ``_last_tool_selection``, ``_last_bundled_skills``).
    """

    schemas: List[Any] = field(default_factory=list)
    is_simple: bool = False
    selection: Dict[str, Any] = field(default_factory=dict)
    bundled_skills: List[str] = field(default_factory=list)


class TurnToolSelector:
    """Resolves the tool menu for a single turn.

    Encapsulates the hybrid RAG + LLM selection path, the fixed voice-lane menu,
    plan-approval checkpoint reuse, and auto-loading a skill bundled with the
    selected tools.
    """

    _VOICE_TOOL_NAMES = ("escalate_to_agent", "hubspace_control")
    _CONTACT_TOOL_NAMES = ("escalate_to_agent",)
    _CHAT_TOOL_NAMES = (
        "web_search",
        "search_browser_history",
        "google_drive_search",
        "google_drive_list_folder",
        "google_drive_read_file",
        "get_calendar_events",
        "search_gmail",
        "read_gmail",
        "search_contacts",
        "get_financial_summary",
        "get_recent_transactions",
        "get_investment_portfolio",
        "get_recurring_subscriptions",
    )

    def __init__(self, tool_registry: Optional[Any]) -> None:
        self.tool_registry = tool_registry

    @classmethod
    def is_plan_approval(cls, text: str) -> bool:
        t = (text or "").strip().lower().rstrip(".!")
        return t in PLAN_APPROVAL_UTTERANCES

    async def select(
        self,
        user_input: str,
        category: str = "autonomous",
        session_id: Optional[str] = None,
        langgraph_engine: Optional[Any] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        knowledge_context: Optional[str] = None,
    ) -> ToolSelection:
        """Consolidated tool resolution with smart-selection caching."""
        if not self.tool_registry:
            return ToolSelection(schemas=[], is_simple=True)

        if category in ("chat", "conversation"):
            return self.chat_schemas()

        if category == "voice":
            return self.voice_schemas()

        if category == "contact":
            return self.contact_schemas()

        if category == "telegram_agent":
            return self.telegram_agent_schemas()

        # Reuse checkpoint tools on plan-approval resumes -- selector on "proceed" returns [].
        if session_id and langgraph_engine and self.is_plan_approval(user_input):
            reused = await langgraph_engine.get_checkpoint_tools(session_id)
            if reused:
                logger.info(
                    f"[ TOOL OPTIMIZATION ] Reusing {len(reused)} checkpoint tools "
                    f"(skipped selector for approval utterance)"
                )
                return ToolSelection(
                    schemas=reused,
                    is_simple=False,
                    selection={
                        "path": "checkpoint_reuse",
                        "llm_available": None,
                        "final_tool_names": [s.get("name") for s in reused if isinstance(s, dict)],
                    },
                )

        # 1. Try smart schema extraction.
        # IMPORTANT: get_schemas_for_turn makes a synchronous HTTP call to the LLM selector
        # (urllib.request.urlopen, blocking). Running it directly would freeze the async event
        # loop for up to 30s. Offload to a thread so other async tasks can continue.
        # copy_context() keeps LangSmith parent span (ChadTurn) attached in the worker thread.
        if hasattr(self.tool_registry, "get_schemas_for_turn"):
            ctx = contextvars.copy_context()

            def on_completions_start():
                if hasattr(self.tool_registry, "_event_bus") and self.tool_registry._event_bus:
                    from src.application.events.events import StatusUpdate
                    from src.core.orchestration.turn.status_labels import SELECTING_LABELS
                    import random
                    self.tool_registry._event_bus.publish(StatusUpdate(status=random.choice(SELECTING_LABELS)))

            result = await asyncio.to_thread(
                ctx.run,
                self.tool_registry.get_schemas_for_turn,
                user_input,
                history,
                category,
                on_completions_start,
                knowledge_context,
            )
            raw_schemas = result.get("schemas", [])
            is_simple = result.get("is_simple", False)
            selection = result.get("selection") or {}
            bundled_skills = result.get("bundled_skills") or []
            logger.info(
                f"[ TOOL OPTIMIZATION ] Selected {len(raw_schemas)} tools. "
                f"Simple: {is_simple}. Path: {selection.get('path')}"
            )
            return ToolSelection(raw_schemas, is_simple, selection, bundled_skills)

        # 2. Fallback to full registry listing.
        if hasattr(self.tool_registry, "get_all_schemas"):
            return ToolSelection(self.tool_registry.get_all_schemas(), is_simple=False)

        return ToolSelection(schemas=[], is_simple=True)

    def _get_schema_for_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve OpenAI schema for tool whether it's a live instance, stub, or dict."""
        if not self.tool_registry or not hasattr(self.tool_registry, "_tools"):
            return None
        tool = self.tool_registry._tools.get(name)
        if not tool:
            return None
        if hasattr(self.tool_registry, "_extract_schema"):
            try:
                schema = self.tool_registry._extract_schema(name, tool)
                if schema and isinstance(schema, dict):
                    return schema
            except Exception:
                pass
        if hasattr(tool, "get_schema"):
            return tool.get_schema()
        if isinstance(tool, dict) and "schema" in tool:
            return tool["schema"]
        return None

    def voice_schemas(self) -> ToolSelection:
        """Fixed voice-lane tools -- no hybrid RAG/9B selector."""
        schemas: List[Any] = []
        if not self.tool_registry:
            return ToolSelection(
                schemas=[],
                is_simple=True,
                selection={"path": "voice_fixed", "final_tool_names": []},
            )
        for name in self._VOICE_TOOL_NAMES:
            schema = self._get_schema_for_tool(name)
            if schema:
                schemas.append(schema)
        names = [s.get("name") for s in schemas if isinstance(s, dict)]
        logger.info(f"[ VOICE ] Fixed tool menu ({len(schemas)}): {names}")
        return ToolSelection(
            schemas=schemas,
            is_simple=True,
            selection={"path": "voice_fixed", "llm_available": False, "final_tool_names": names},
        )

    def contact_schemas(self) -> ToolSelection:
        """Fixed Telegram contact-desk tools — escalate only."""
        schemas: List[Any] = []
        if not self.tool_registry:
            return ToolSelection(
                schemas=[],
                is_simple=True,
                selection={"path": "contact_fixed", "final_tool_names": []},
            )
        for name in self._CONTACT_TOOL_NAMES:
            schema = self._get_schema_for_tool(name)
            if schema:
                schemas.append(schema)
        names = [s.get("name") for s in schemas if isinstance(s, dict)]
        logger.info(f"[ CONTACT ] Fixed tool menu ({len(schemas)}): {names}")
        return ToolSelection(
            schemas=schemas,
            is_simple=True,
            selection={"path": "contact_fixed", "llm_available": False, "final_tool_names": names},
        )

    def chat_schemas(self) -> ToolSelection:
        """Fixed read-only informational tools for chat mode."""
        schemas: List[Any] = []
        if not self.tool_registry:
            return ToolSelection(
                schemas=[],
                is_simple=True,
                selection={"path": "turn_mode_chat", "final_tool_names": []},
            )
        bound_names: List[str] = []
        for name in self._CHAT_TOOL_NAMES:
            schema = self._get_schema_for_tool(name)
            if schema:
                bound_names.append(name)
                schemas.append(schema)
        logger.info(f"[ CHAT ] Fixed read-only tool menu ({len(schemas)}): {bound_names}")
        return ToolSelection(
            schemas=schemas,
            is_simple=True,
            selection={"path": "turn_mode_chat", "llm_available": False, "final_tool_names": bound_names},
        )

    def telegram_agent_schemas(self) -> ToolSelection:
        """Fixed pocket-remote tools — no specialist delegates / GUI cards."""
        from src.remote.telegram.telegram_agent import TELEGRAM_AGENT_TOOL_NAMES

        schemas: List[Any] = []
        if not self.tool_registry:
            return ToolSelection(
                schemas=[],
                is_simple=False,
                selection={"path": "telegram_agent_fixed", "final_tool_names": []},
            )
        for name in TELEGRAM_AGENT_TOOL_NAMES:
            schema = self._get_schema_for_tool(name)
            if schema:
                schemas.append(schema)
        names = [s.get("name") for s in schemas if isinstance(s, dict)]
        logger.info(f"[ TELEGRAM ] Fixed tool menu ({len(schemas)}): {names}")
        return ToolSelection(
            schemas=schemas,
            is_simple=False,
            selection={
                "path": "telegram_agent_fixed",
                "llm_available": False,
                "final_tool_names": names,
            },
        )

    async def resolve_bundled_skill(
        self, bundled_skills: List[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Auto-load SKILL.md when a tool bundle declares a paired skill."""
        if not bundled_skills:
            return None, None

        # Deduplicate skills while preserving domain priority (generic utilities like google_workspace sorted last)
        unique_skills = []
        for s in bundled_skills:
            if s and s not in unique_skills:
                unique_skills.append(s)
        unique_skills.sort(key=lambda s: 1 if s == "google_workspace" else 0)

        try:
            from src.core.skill_system.skill_loader import get_skill_loader

            loader = get_skill_loader()
            if loader.is_empty:
                await loader.initialize()

            loaded_contents = []
            loaded_names = []
            for skill_name in unique_skills:
                try:
                    content = await loader.load_skill_content(skill_name)
                    loaded_contents.append(content)
                    loaded_names.append(skill_name)
                    logger.info(f"[ TURN ] Auto-loaded bundled skill: {skill_name}")
                except Exception as ex:
                    logger.warning(f"[ TURN ] Failed to auto-load bundled skill '{skill_name}': {ex}")

            if not loaded_contents:
                return None, None

            combined_content = "\n\n---\n\n".join(loaded_contents)
            combined_names = ", ".join(loaded_names)
            return combined_content, combined_names
        except Exception as e:
            logger.warning(f"[ TURN ] Failed to auto-load bundled skills {bundled_skills}: {e}")
            return None, None
