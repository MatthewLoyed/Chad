
import asyncio
import os
import sys

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from src.infrastructure.tools.registry import ToolRegistry
from src.shared.config_manager import ConfigManager
from src.core.tools.mcp_registry import MCPRegistry

async def restore_mcp_tools():
    # 1. Load config
    config = ConfigManager.load_config()
    registry = ToolRegistry()
    
    # 2. Discover local tools (Native + Skills)
    print("Discovering local tools...")
    registry.discover_all(config, sync_cloud=False)
    
    # 3. Discover MCP tools
    print("Starting MCP discovery...")
    mcp_reg = MCPRegistry()
    mcp_config = config.get('mcp_servers', {})
    
    if not mcp_config:
        print("No MCP servers found in config.yaml")
        return

    # Connect to ALL MCP servers to restore all tools
    for name, srv_config in mcp_config.items():
        print(f"Connecting to MCP server: {name}...")
        mcp_reg.register(name, srv_config['command'], srv_config.get('env', {}), srv_config)
        entry = mcp_reg.get(name)
        try:
            # list_tools() triggers connect() automatically
            tools = await entry.list_tools()
            print(f"  [ SUCCESS ] Found {len(tools)} tools from '{name}'")
            
            # Register each tool in the main registry
            for t in tools:
                registry.register_mcp_tool(t, entry, category='mcp')
        except Exception as e:
            print(f"  [ FAILED ] Could not connect to '{name}': {e}")

    # 4. Sync everything to Supabase
    print(f"\nFinal Registry: {len(registry.tools)} tools total.")
    print("Pushing to Supabase (this will restore Playwright, Brave, etc.)...")
    
    # We call the bridge directly to ensure it runs
    await registry._sync_bridge.sync_all(registry.tools)
    
    # 5. Cleanup
    await mcp_reg.cleanup()
    print("\n[ DONE ] MCP tools restored successfully.")

if __name__ == "__main__":
    asyncio.run(restore_mcp_tools())
