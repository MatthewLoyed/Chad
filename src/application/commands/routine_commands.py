"""Commands for routines UI: tool catalog, NL draft, skills browser."""

from dataclasses import dataclass
from typing import Any, Dict, List

from src.application.commands.base_command import Command
from src.core.routines.routine_builder import RoutineBuilder
from src.core.skill_system.skill_loader import get_skill_loader


def _tool_registry_from_facade(facade):
    assistant = getattr(facade, "_assistant", None)
    if assistant is None:
        return None
    reg = getattr(assistant, "tool_registry", None)
    if reg:
        return reg
    session = getattr(assistant, "session_manager", None)
    return getattr(session, "tool_registry", None) if session else None


@dataclass
class ListToolCatalogCommand(Command):
    """Return tool name, description, category, and parameter schema for the UI."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        registry = _tool_registry_from_facade(facade)
        if not registry:
            from src.infrastructure.tools import ToolRegistry
            registry = ToolRegistry()
            registry.discover_all()
        return registry.list_catalog_entries()


@dataclass
class DraftRoutineCommand(Command):
    """Draft using the same routed model/provider as the agent supervisor."""

    description: str

    async def execute(self, facade) -> Dict[str, Any]:
        registry = _tool_registry_from_facade(facade)
        if not registry:
            return {"success": False, "steps": [], "error": "Tool registry not ready."}
        catalog = registry.list_catalog_entries()
        assistant = getattr(facade, "_assistant", None)
        session = getattr(assistant, "session_manager", None)
        router = getattr(session, "router", None)
        if not router:
            return {"success": False, "steps": [], "error": "Supervisor model router not ready."}
        try:
            model = router.get_model_for_category("agent")
            adapter = getattr(model, "adapter", None)
            if adapter is None:
                return {"success": False, "steps": [], "error": "Supervisor model adapter not available."}
            return await RoutineBuilder(adapter=adapter).draft(self.description, catalog)
        except Exception:
            return {"success": False, "steps": [], "error": "Supervisor model unavailable. Check model settings."}


@dataclass
class ListSkillsCatalogCommand(Command):
    """Return SKILL.md catalog entries for the Skills workspace."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        loader = get_skill_loader()
        if not loader._initialized:
            await loader.initialize()
        return [
            {
                "name": e.name,
                "description": e.description,
                "version": e.version,
                "tags": e.tags,
                "subagent": e.subagent,
                "enabled": e.enabled,
            }
            for e in loader._catalog.values()
        ]


@dataclass
class GetSkillContentCommand(Command):
    """Load full SKILL.md body for preview in the UI."""

    skill_name: str

    async def execute(self, facade) -> Dict[str, Any]:
        loader = get_skill_loader()
        if not loader._initialized:
            await loader.initialize()
        try:
            content = await loader.load_skill_content(self.skill_name)
            return {"success": True, "name": self.skill_name, "content": content}
        except (ValueError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}
