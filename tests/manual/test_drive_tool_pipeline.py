"""
End-to-end pipeline diagnostic for 9B tool selection → LangGraph supervisor binding.

Usage:
    python tests/manual/test_drive_tool_pipeline.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

QUERY = "search my google drive for wedding folder and list contents inside"
EXPECTED_MCP = {"search", "listFolder"}
CORE_ALWAYS = {"ask_user_question", "search_for_tool", "list_skills", "call_mcp_tool"}


def _register_mock_drive_mcp(registry) -> None:
    """Register Google Drive MCP tools if server isn't reachable."""
    if registry._tool_sources.get("search") == "mcp":
        return
    mock_server = MagicMock()
    mock_server.name = "google-drive"
    mock_server.config = {"description": "Google Drive MCP"}
    for name, desc in [
        ("search", "Search for files in Google Drive"),
        ("listFolder", "List contents of a folder (defaults to root)"),
        ("downloadFile", "Download a Google Drive file to a local path"),
        ("addComment", "Add a comment to a document"),
    ]:
        registry.register_mcp_tool(
            tool_metadata={
                "name": name,
                "description": desc,
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            server=mock_server,
            category="google-drive",
        )


async def discover_mcp(registry) -> None:
    from src.shared.config_manager import ConfigManager
    from src.core.tools.mcp_registry import MCPRegistry

    cfg = ConfigManager.load_config()
    mcp_registry = MCPRegistry()
    for name, server_cfg in cfg.get("mcp_servers", {}).items():
        env = {k: os.path.expandvars(str(v)) for k, v in server_cfg.get("env", {}).items()}
        extra = {k: v for k, v in server_cfg.items() if k not in ("command", "env")}
        mcp_registry.register(name, server_cfg["command"], env, extra)

    try:
        await registry.discover_mcp_servers(mcp_registry)
        registry.mcp_registry = mcp_registry
        print(f"[ MCP ] Discovered {sum(1 for s in registry._tool_sources.values() if s == 'mcp')} MCP tools")
    except Exception as e:
        print(f"[ MCP ] Live discovery failed ({e}); using mock Drive tools")
        _register_mock_drive_mcp(registry)


def schema_names(schemas: List[Dict[str, Any]]) -> List[str]:
    return [s.get("name") or s.get("function", {}).get("name", "?") for s in schemas]


def simulate_supervisor_bind(registry, schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mirror supervisor_node.py tool resolution."""
    rag_tool_names: List[str] = []
    for s in schemas:
        if "function" in s and "name" in s["function"]:
            rag_tool_names.append(s["function"]["name"])
        elif "name" in s:
            rag_tool_names.append(s["name"])

    direct: List[str] = []
    dropped: List[str] = []
    for name in rag_tool_names:
        t = registry.get_langchain_tool_by_name(name)
        if t:
            direct.append(name)
        else:
            dropped.append(name)

    return {
        "state_tool_names": rag_tool_names,
        "bound_to_gemma": direct,
        "dropped_offline": dropped,
    }


async def main() -> int:
    from src.infrastructure.tools import ToolRegistry

    print("=" * 70)
    print("DRIVE QUERY PIPELINE TEST")
    print(f"Query: {QUERY!r}")
    print("=" * 70)

    registry = ToolRegistry()
    registry.discover_all()
    await discover_mcp(registry)

    selector = registry._llm_selector
    nine_b_up = selector.is_available()
    print(f"\n[ 9B ] LM Studio reachable: {nine_b_up}")

    nine_b_picks: List[str] | None = None
    if nine_b_up:
        nine_b_picks = selector.select(QUERY, registry._tools)
        print(f"[ 9B ] Raw selection: {nine_b_picks}")
        mcp_picked = [n for n in (nine_b_picks or []) if registry._tool_sources.get(n) == "mcp"]
        print(f"[ 9B ] MCP in selection: {mcp_picked}")
    else:
        print("[ 9B ] UNAVAILABLE — pipeline falls back to RAG (MCP tools stripped unless pinned)")

        turn = registry.get_schemas_for_turn(QUERY)
        schemas = turn["schemas"]
        selection = turn.get("selection", {})
        if selection:
            print(f"\n[ SELECTION ] path={selection.get('path')} llm_available={selection.get('llm_available')}")
            if selection.get("llm_unavailable_reason"):
                print(f"[ SELECTION ] unavailable_reason={selection.get('llm_unavailable_reason')}")
            print(f"[ SELECTION ] llm_selected={selection.get('llm_selected')}")
            print(f"[ SELECTION ] final={selection.get('final_tool_names')}")
            print(f"[ SELECTION ] mcp_in_final={selection.get('mcp_tools_in_final')}")
    final_names = set(schema_names(schemas))
    print(f"\n[ REGISTRY ] get_schemas_for_turn -> {len(schemas)} tools:")
    for n in schema_names(schemas):
        src = registry._tool_sources.get(n, "native")
        tag = "MCP" if src == "mcp" else "native"
        print(f"  - {n} ({tag})")

    bind = simulate_supervisor_bind(registry, schemas)
    print(f"\n[ SUPERVISOR ] Tools in state: {bind['state_tool_names']}")
    print(f"[ SUPERVISOR ] Bound to Gemma (bind_tools): {bind['bound_to_gemma']}")
    if bind["dropped_offline"]:
        print(f"[ SUPERVISOR ] DROPPED (not bindable): {bind['dropped_offline']}")

    # Assertions / diagnosis
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    ok = True

    if not nine_b_up:
        print("FAIL: 9B selector offline — Gemma won't get query-selected MCP tools")
        ok = False

    missing_mcp = EXPECTED_MCP - final_names
    if missing_mcp:
        print(f"FAIL: Expected MCP tools missing from registry turn output: {sorted(missing_mcp)}")
        ok = False
    else:
        print(f"PASS: Expected MCP tools in turn schemas: {sorted(EXPECTED_MCP & final_names)}")

    missing_bind = EXPECTED_MCP - set(bind["bound_to_gemma"])
    if missing_bind:
        print(f"FAIL: Expected MCP tools not bound to Gemma: {sorted(missing_bind)}")
        ok = False
    else:
        print("PASS: Expected MCP tools bind successfully for LangGraph")

    unexpected_always = final_names & {"addComment"}
    if unexpected_always:
        print(f"WARN: Obscure MCP tools leaked into turn: {unexpected_always}")

    pinned = set()  # pins removed — selector drives MCP exposure

    if ok:
        print("\nOverall: PASS")
        return 0
    print("\nOverall: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
