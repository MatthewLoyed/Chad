import sys
from pathlib import Path
import asyncio

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools import ToolRegistry as UnifiedRegistry

def test_registry():
    async def run_test():
        registry = UnifiedRegistry()
        registry.discover_all()
        
        # Verify basic tool counts / discovery
        assert len(registry.tools) > 0
        
        tools_to_check = ["file_manager", "search_for_tool", "call_mcp_tool"]
        for name in tools_to_check:
            tool = registry.get_tool(name)
            assert tool is not None
        
        # Verify call_mcp_tool has registry injected
        mcp_tool = registry.get_tool("call_mcp_tool")
        assert getattr(mcp_tool, "registry", None) is not None

        # Execute search_for_tool to verify it has the registry injected properly
        result = await registry.execute_tool("search_for_tool", {"query": "file"})
        assert result.get("success") is True, f"search_for_tool failed: {result}"
        assert "result" in result
            
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(run_test())
    asyncio.get_event_loop().close()

def test_traversal_agent_tools():
    registry = UnifiedRegistry()
    registry.discover_all()
    traversal_tools = [t["name"] for t in registry.get_tools_by_domain("traversal")]
    assert "search_for_tool" in traversal_tools
    assert "refresh_google_auth" in traversal_tools

if __name__ == "__main__":
    test_registry()
    test_traversal_agent_tools()
