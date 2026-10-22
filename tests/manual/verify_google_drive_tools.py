"""Verify native Google Drive tools register and MCP overlap is hidden."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

NATIVE_TOOLS = [
    "google_drive_list_folder",
    "google_drive_search",
    "google_drive_read_file",
    "google_drive_create_doc",
    "google_drive_edit_doc",
]

EXCLUDED_MCP = {
    "search",
    "listFolder",
    "readGoogleDoc",
    "getGoogleDocContent",
    "downloadFile",
    "createGoogleDoc",
    "updateGoogleDoc",
    "insertText",
    "findAndReplaceInDoc",
}


async def main() -> int:
    from src.infrastructure.tools import ToolRegistry
    from src.shared.config_manager import ConfigManager
    from src.core.tools.mcp_registry import MCPRegistry

    reg = ToolRegistry()
    reg.discover_all()

    ok = True
    print("=== Native Google Drive tools ===")
    for name in NATIVE_TOOLS:
        tool = reg.tools.get(name)
        src = reg._tool_sources.get(name)
        registered = tool is not None and hasattr(tool, "execute")
        print(f"  {name}: registered={registered}, source={src}")
        if not registered or src != "local":
            ok = False

    cfg = ConfigManager.load_config()
    mcp_registry = MCPRegistry()
    gd = cfg.get("mcp_servers", {}).get("google-drive")
    if gd:
        env = {k: os.path.expandvars(str(v)) for k, v in gd.get("env", {}).items()}
        extra = {k: v for k, v in gd.items() if k not in ("command", "env")}
        mcp_registry.register("google-drive", gd["command"], env, extra)
        await reg.discover_mcp_servers(mcp_registry, sync_cloud=False)

    print("\n=== MCP overlap check ===")
    leaked = [n for n in EXCLUDED_MCP if reg._tool_sources.get(n) == "mcp"]
    print(f"  Excluded MCP tools leaked: {leaked or 'none'}")
    if leaked:
        ok = False

    niche = reg._tool_sources.get("addComment") == "mcp"
    print(f"  Niche MCP tool addComment available: {niche}")
    if not niche:
        ok = False

    print("\nOverall:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
