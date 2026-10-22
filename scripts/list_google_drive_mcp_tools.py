"""One-off: list tools from the configured google-drive MCP server."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from src.shared.config_manager import ConfigManager
from src.core.tools.mcp_registry import MCPRegistry


async def main() -> None:
    cfg = ConfigManager.load_config()
    gd = cfg.get("mcp_servers", {}).get("google-drive")
    if not gd:
        print("No google-drive in mcp_servers")
        return

    reg = MCPRegistry()
    env = {k: os.path.expandvars(str(v)) for k, v in gd.get("env", {}).items()}
    server_cfg = {k: v for k, v in gd.items() if k not in ("command", "env")}
    reg.register("google-drive", gd["command"], env, server_cfg)

    entry = reg.get("google-drive")
    try:
        tools = await entry.list_tools()
        print("GOOGLE_DRIVE_MCP_TOOLS")
        for t in sorted(tools, key=lambda x: x.get("name", "")):
            desc = (t.get("description") or "").replace("\n", " ")
            print(f"  {t.get('name')}: {desc[:100]}")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await entry.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
