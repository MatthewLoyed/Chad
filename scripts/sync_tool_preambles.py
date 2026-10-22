"""
Maintenance Script: Sync Tool Preambles to Supabase Vector Store
This script runs tool discovery and performs a cloud sync to generate/upload preambles 
and schemas to Supabase. This should be run passively as a maintenance script rather than on startup.

Usage:
  $env:PYTHONPATH="."
  .\.venv\Scripts\python scripts/sync_tool_preambles.py
"""

import sys
import asyncio
import time
import logging

# Ensure selector loop policy on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path
root_dir = str(Path(__file__).parents[1])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.shared.logger import setup_logger
logger = setup_logger("SyncToolPreamblesScript")

async def main():
    print("=" * 60)
    print("CHAD MAINTENANCE: SYNC TOOL PREAMBLES TO SUPABASE")
    print("=" * 60)
    
    start_time = time.time()
    
    logger.info("Instantiating ToolRegistry...")
    from src.infrastructure.tools import ToolRegistry
    registry = ToolRegistry()
    
    logger.info("Scanning and discovering all local tools and skills...")
    # Run local discovery (sync_cloud=False to prevent recursive sync calls)
    registry.discover_all(sync_cloud=False)
    
    logger.info(f"Discovered {registry.get_tool_count()} local tools/skills.")
    
    logger.info("Initializing MCPRegistry and discovering MCP tools...")
    from src.shared.config_manager import ConfigManager
    from src.core.tools.mcp_registry import MCPRegistry
    import os
    
    config = ConfigManager.load_config()
    mcp = MCPRegistry()
    
    # Register MCP servers from config
    mcp_config = config.get('mcp_servers', {})
    for name, cfg in mcp_config.items():
        env = cfg.get('env', {})
        resolved_env = {}
        for k, v in env.items():
            if isinstance(v, str):
                if v.startswith('${') and v.endswith('}'):
                    env_key = v[2:-1]
                    resolved_env[k] = os.getenv(env_key, '')
                elif v.startswith('%') and v.endswith('%'):
                    env_key = v[1:-1]
                    resolved_env[k] = os.getenv(env_key, '')
                else:
                    resolved_env[k] = os.path.expandvars(v)
            else:
                resolved_env[k] = v
        mcp.register(name, cfg['command'], resolved_env, cfg)
        
    try:
        logger.info("Connecting to MCP servers for discovery (this may take a moment)...")
        await registry.discover_mcp_servers(mcp)
        logger.info(f"Discovered MCP tools. Total tools in registry: {registry.get_tool_count()}")
    except Exception as e:
        logger.error(f"MCP discovery failed: {e}")
        
    logger.info("Initializing Supabase sync bridge...")
    from src.infrastructure.tools.cloud.supabase_bridge import SupabaseToolSyncBridge
    sync_bridge = SupabaseToolSyncBridge()
    
    logger.info("Starting cloud synchronization (Preamble generation & Vector Store upsert)...")
    sync_start = time.time()
    try:
        # Call the sync_all method of the bridge directly to synchronize tools
        await sync_bridge.sync_all(registry._tools)
        logger.info(f"[ SUCCESS ] Cloud sync completed successfully in {time.time() - sync_start:.2f}s!")
    except Exception as e:
        logger.error(f"[ FAILED ] Cloud sync failed: {e}", exc_info=True)
        sys.exit(1)
        
    print("\n" + "=" * 60)
    print(f"COMPLETED SUCCESSFULLY in {time.time() - start_time:.2f}s!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
