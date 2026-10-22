import asyncio
import logging
import sys
from pathlib import Path

# Add src to PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.shared.config_manager import ConfigManager
from src.application.system_factory import SystemFactory
from src.application.system_container import SystemContainer

async def verify_bootstrapping():
    print("\n[ BUS ] Starting SystemFactory Verification Pipeline...")
    
    # Stub environment variables needed for constructor validation
    import os
    if "GEMINI_API_KEY" not in os.environ:
        os.environ["GEMINI_API_KEY"] = "dummy_test_key"
    
    # 1. Load Clean Config
    config = ConfigManager.load_config()
    print("[ OK ] Loaded Configuration")

    # 2. Build Core Engine
    print("\n[ STEP ] Building Core Engine (Synchronous)...")
    container = SystemFactory.build_core_engine(config)
    
    # Assert fundamental existence
    assert isinstance(container, SystemContainer), "Output must be a SystemContainer"
    assert container.tool_registry is not None, "Missing ToolRegistry"
    assert container.memory_engine is not None, "Missing MemoryEngine"
    assert container.model_router is not None, "Missing ModelRouter"
    
    print("[ OK ] Core components constructed successfully.")

    # 3. Assert Critical Dependency Injections (The Bug Fixes)
    
    # FIX 1: ModelRouter must HAVE MemoryEngine
    assert container.model_router.memory_engine is not None, "FAIL: ModelRouter has no MemoryEngine injected!"
    assert container.model_router.memory_engine == container.memory_engine, "FAIL: ModelRouter injected memory mismatch!"
    print("[ OK ] Verified: ModelRouter received MemoryEngine injection.")

    # FIX 2: MemoryTool must HAVE MemoryEngine
    mem_tool = container.tool_registry.get_tool("manage_user_memory")
    if mem_tool:
        assert hasattr(mem_tool, "memory") and mem_tool.memory == container.memory_engine, "FAIL: MemoryTool has no injected MemoryEngine!"
        print("[ OK ] Verified: MemoryTool received MemoryEngine injection.")
    else:
        print("[ SKIP ] Skipping MemoryTool injection check (tool not registered in this config).")


    
    # 4. Test Async Infrastructure Warmup
    print("\n[ STEP ] Warming up Async Infrastructure (MCPs/Skills)...")
    await SystemFactory.warmup_async_infrastructure(container, config)
    assert container.mcp_registry is not None, "FAIL: MCPRegistry missing after warmup"
    assert container.skill_loader is not None, "FAIL: SkillLoader missing after warmup"
    print(f"[ OK ] Verified: MCPRegistry and SkillLoader initialized successfully.")

    print("\n[ SUCCESS ] All SystemFactory verification assertions passed.")
    
if __name__ == "__main__":
    asyncio.run(verify_bootstrapping())
