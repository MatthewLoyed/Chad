"""
Scratch Script: Test exact line that hangs inside initialize_assistant
"""
import sys
import asyncio
import time
import logging

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path
root_dir = str(Path(__file__).parents[1])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestSteps")

async def main():
    print("=" * 60)
    print("STEP-BY-STEP BREAKDOWN OF ASSISTANT INITIALIZATION")
    print("=" * 60)
    
    # 1. Imports
    print("\n--- [1] Importing modules ---")
    start = time.time()
    
    print("Importing SessionManager...", flush=True)
    from src.core.orchestration.session_manager import SessionManager
    
    print("Importing HeartbeatService...", flush=True)
    from src.core.monitoring.heartbeat_service import HeartbeatService
    
    print("Importing CronScheduler...", flush=True)
    from src.core.scheduling.cron_scheduler import CronScheduler
    
    print("Importing TaskRepository...", flush=True)
    from src.infrastructure.repositories.task_repository import TaskRepository
    
    print("Importing TurnCoordinator...", flush=True)
    from src.core.orchestration.turn_coordinator import TurnCoordinator
    
    print("Importing ModelRouter...", flush=True)
    from src.core.intelligence.model_router import ModelRouter
    
    print("Importing RemoteToolExecutor...", flush=True)
    from src.core.tools.remote_executor import RemoteToolExecutor
    
    print("Importing LocalToolExecutor...", flush=True)
    from src.core.tools.tool_executor import LocalToolExecutor
    
    print("Importing HybridToolExecutor...", flush=True)
    from src.core.tools.hybrid_executor import HybridToolExecutor
    
    print(f"Imports done in {time.time() - start:.2f}s", flush=True)

    # 2. Skill Loader
    print("\n--- [2] Skill Loader ---")
    start = time.time()
    from src.core.skill_system.skill_loader import get_skill_loader
    skill_loader = get_skill_loader()
    print("get_skill_loader completed.")
    
    print("Calling skill_loader.initialize()...")
    try:
        await asyncio.wait_for(skill_loader.initialize(), timeout=5.0)
        print(f"skill_loader.initialize() completed in {time.time() - start:.2f}s!")
    except Exception as e:
        print(f"skill_loader.initialize() failed/timed out: {e}")

    # 3. MCP Registry
    print("\n--- [3] MCP Registry ---")
    start = time.time()
    from src.core.tools.mcp_registry import MCPRegistry
    mcp_registry = MCPRegistry()
    print("MCPRegistry instantiated.")

    # 4. Spawning MCP servers
    print("\n--- [4] Prewarming MCP Cache (Spawning MCP Servers) ---")
    start = time.time()
    try:
        # Let's inspect prewarm_cache
        print("Calling mcp_registry.prewarm_cache()...")
        await asyncio.wait_for(mcp_registry.prewarm_cache(), timeout=5.0)
        print(f"mcp_registry.prewarm_cache() completed in {time.time() - start:.2f}s!")
    except Exception as e:
        print(f"mcp_registry.prewarm_cache() failed/timed out: {e}")

    print("\n--- [5] Discover MCP Servers ---")
    start = time.time()
    from src.infrastructure.tools import ToolRegistry
    registry = ToolRegistry()
    registry.discover_all(sync_cloud=False)
    
    try:
        print("Calling registry.discover_mcp_servers()...")
        await asyncio.wait_for(registry.discover_mcp_servers(mcp_registry), timeout=5.0)
        print(f"registry.discover_mcp_servers completed in {time.time() - start:.2f}s!")
    except Exception as e:
        print(f"registry.discover_mcp_servers failed/timed out: {e}")

if __name__ == "__main__":
    asyncio.run(main())
