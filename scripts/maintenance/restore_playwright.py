
import asyncio
import os
import sys

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from src.infrastructure.tools.registry import ToolRegistry
from src.shared.config_manager import ConfigManager
from src.infrastructure.tools.discovery.mcp_scanner import MCPScanner
from src.infrastructure.tools.mcp.mcp_orchestrator import MCPOrchestrator

async def restore_playwright():
    config = ConfigManager.get_full_config()
    registry = ToolRegistry()
    
    # 1. Discover local tools
    registry.discover_all(config, sync_cloud=False)
    
    # 2. Discover MCP tools
    print("Starting MCP Orchestrator to discover Playwright tools...")
    mcp_orchestrator = MCPOrchestrator(registry, config)
    await mcp_orchestrator.start()
    
    # Give it a moment to connect and discover
    await asyncio.sleep(5)
    
    print(f"Discovered {len(registry.tools)} tools total.")
    
    # 3. Sync everything (including Playwright) to Supabase
    print("Syncing to Supabase...")
    await registry._sync_bridge.sync_all(registry.tools)
    
    # 4. Cleanup
    await mcp_orchestrator.stop()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(restore_playwright())
