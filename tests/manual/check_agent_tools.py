import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.tools.registry import ToolRegistry

async def list_agent_tools():
    print("Initializing ToolRegistry...")
    registry = ToolRegistry()
    registry.discover_all(sync_cloud=False)
    
    # Wait for MCP servers to be discovered (they are in background tasks)
    # Actually discover_all doesn't wait for MCP discovery tasks.
    # We might need to wait a bit.
    await asyncio.sleep(2)
    
    print("\nTools for domain 'code':")
    code_tools = registry.get_tools_by_domain("code")
    for t in code_tools:
        print(f"- {t['name']}: {t.get('description', '')[:100]}...")

    print("\nTools for domain 'system' (often used by code tasks):")
    sys_tools = registry.get_tools_by_domain("system")
    for t in sys_tools:
        print(f"- {t['name']}: {t.get('description', '')[:100]}...")

if __name__ == "__main__":
    asyncio.run(list_agent_tools())
