"""
Scout-only probe — one planner brief in, gather_brief (last Scout message) out.

No planner, no synth. Uses the real secondary Scout model + Drive tools.

Usage (from worktree, with LM Studio + Google OAuth config present):

    $env:PYTHONPATH="."; $env:CHAD_MAIN_ROOT="C:\\Users\\ExampleUser\\Projects\\Chad"
    & "$env:CHAD_MAIN_ROOT\\.venv\\Scripts\\python.exe" -m evals.scout_probe
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage


EXAMPLE_BRIEF = (
    "Find and read the Google Drive document 'Alex & Sam wedding roadmap' "
    "(or similar title). Report back: file id/name, wedding date, venue, colors, "
    "registry notes, and any obvious gaps. Facts only — do not write a plan."
)


async def _run() -> int:
    from src.shared.config_manager import ConfigManager
    from src.application.events import EventBus
    from src.application.system_factory import SystemFactory
    from src.core.intelligence.lc_wrapper import ChatChad
    from src.core.orchestration.nodes.scout_node import make_scout_node
    from src.core.orchestration.supervisor import _build_scout_model

    ConfigManager.load_config()
    config = ConfigManager.get_config()
    bus = EventBus()
    container = SystemFactory.build_core_engine(config, event_bus=bus)
    try:
        await SystemFactory.warmup_async_infrastructure(container, config)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] warmup: {type(exc).__name__}: {exc}", file=sys.stderr)

    if container.adapter is None:
        print("No AI adapter — is a local/cloud model configured?", file=sys.stderr)
        return 2

    main_model = ChatChad(adapter=container.adapter)
    scout_model = _build_scout_model(main_model)
    tool_registry = container.tool_registry

    schemas = tool_registry.get_schemas_for_turn(
        "find wedding roadmap on google drive",
        category="planning",
    )
    rag_tools: List[Dict[str, Any]] = list(schemas.get("schemas") or [])
    # Always seed Drive read tools for this probe even if selector is thin.
    for name in (
        "google_drive_search",
        "google_drive_read_file",
        "google_drive_list_folder",
        "search_for_tool",
    ):
        if any((s.get("name") == name) for s in rag_tools):
            continue
        lt = tool_registry.get_langchain_tool_by_name(name)
        if lt:
            rag_tools.append(
                {"name": name, "description": (getattr(lt, "description", None) or "")[:200]}
            )
    print(f"[tools bound for scout menu seed: {len(rag_tools)}]")

    scout = make_scout_node(scout_model, tool_registry, handoff_tools=[])
    state = {
        "messages": [
            HumanMessage(
                content="PLAN MODE: build a local wedding website from my Drive roadmap."
            )
        ],
        "scout_gather_request": EXAMPLE_BRIEF,
        "tools": rag_tools,
        "turn_id": "scout_probe",
        "session_id": "scout_probe",
    }
    print("=== PLANNER BRIEF ===")
    print(EXAMPLE_BRIEF)
    print("\n=== RUNNING SCOUT ===\n")
    result = await scout(state, config={})
    brief = (result or {}).get("gather_brief") or ""
    print("=== GATHER_BRIEF (verbatim last Scout message) ===")
    print(brief if brief else "(empty)")
    print(f"\n[{len(brief)} chars]")
    return 0 if brief.strip() else 1


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
