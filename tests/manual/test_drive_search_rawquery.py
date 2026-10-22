"""Quick check: Drive search rawQuery behavior."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from src.shared.config_manager import ConfigManager
from src.core.tools.mcp_registry import MCPRegistry


async def call(entry, q: str, raw=None):
    args = {"query": q}
    if raw is not None:
        args["rawQuery"] = raw
    r = await entry.call_tool("search", args)
    text = r[0] if isinstance(r, list) and r else r
    if isinstance(text, dict):
        text = text.get("text", text)
    print(f"  {args!r} -> {str(text)[:120]}")


async def main():
    cfg = ConfigManager.load_config()
    gd = cfg["mcp_servers"]["google-drive"]
    reg = MCPRegistry()
    env = {k: os.path.expandvars(str(v)) for k, v in gd.get("env", {}).items()}
    reg.register("google-drive", gd["command"], env, {k: v for k, v in gd.items() if k not in ("command", "env")})
    entry = reg.get("google-drive")
    try:
        print("rawQuery=true, plain text (broken):")
        await call(entry, "wedding", True)
        print("rawQuery=false, plain text:")
        await call(entry, "wedding", False)
        print("no rawQuery, plain text:")
        await call(entry, "wedding")
        print("rawQuery=true, structured query:")
        await call(entry, "name contains 'wedding'", True)
    finally:
        await entry.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
