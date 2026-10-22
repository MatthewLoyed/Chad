
import sys
import os
import asyncio
from typing import List, Dict, Any
from sentence_transformers import SentenceTransformer

# Add project root to path
sys.path.append(os.path.abspath("."))

from src.infrastructure.tools.registry import ToolRegistry
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.core.tools.mcp_registry import MCPRegistry
from src.shared.config_manager import ConfigManager

async def populate_supabase_tools():
    print("Initializing ToolRegistry and discovering local tools...")
    registry = ToolRegistry()
    registry.discover_all()
    
    print("Initializing MCPRegistry and discovering MCP tools...")
    config = ConfigManager.load_config()
    mcp = MCPRegistry()
    
    # Register MCP servers from config
    mcp_config = config.get('mcp_servers', {})
    for name, cfg in mcp_config.items():
        env = cfg.get('env', {})
        resolved_env = {}
        for k, v in env.items():
            if isinstance(v, str):
                import os
                # Expand env vars like %USERPROFILE% or ${VAR}
                resolved_env[k] = os.path.expandvars(v)
            else:
                resolved_env[k] = v
        mcp.register(name, cfg['command'], resolved_env, cfg)
    
    # Discover tools (this connects to servers)
    try:
        print("Connecting to MCP servers for discovery (this may take a moment)...")
        await registry.discover_mcp_servers(mcp)
    except Exception as e:
        print(f"Warning: MCP discovery failed: {e}")

    # ADD FALLBACK MOCK MCP TOOLS for testing if they weren't discovered
    # This ensures RAG has them even if the local servers aren't running
    important_mcp_tools = [
        ("Snapshot", "Take a visual snapshot of the current screen for UI automation analysis."),
        ("Click", "Click on a UI element found in a snapshot. Use with 'label' parameter."),
        ("Type", "Type text into the currently focused UI element. Use for chat input."),
        ("Scroll", "Scroll the active window or a specific element up/down."),
        ("brave_web_search", "Search the web using Brave Search API for up-to-date information."),
    ]
    
    for name, desc in important_mcp_tools:
        if name not in registry._tools:
            print(f"Adding mock MCP tool for RAG indexing: {name}")
            # Mock tool object
            class MockTool:
                def __init__(self, n, d):
                    self.name = n
                    self.description = d
                def to_schema(self):
                    return {"name": self.name, "description": self.description, "parameters": {"type": "object", "properties": {}}}
            registry.register_tool(MockTool(name, desc), category='mcp')
    
    print("Loading embedding model (all-MiniLM-L6-v2)...")
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    supabase = get_supabase_manager()
    if not supabase.enabled:
        print("Supabase is not enabled. Check your .env file.")
        return

    # Tools to port: ALL discovered tools
    all_registered_tools = registry.get_all_tools()
    print(f"Porting {len(all_registered_tools)} tools to Supabase...")
    
    for tool in all_registered_tools:
        # Extract tool name
        tool_name = getattr(tool, 'name', tool.get('name') if isinstance(tool, dict) else str(tool))
        
        # Get schema and description
        if hasattr(tool, 'get_schema'):
            schema = tool.get_schema()
            desc = schema.get('description', tool_name)
        elif isinstance(tool, dict):
            schema = {
                'name': tool['name'],
                'description': tool.get('description', ''),
                'parameters': tool.get('inputSchema', {})
            }
            desc = tool.get('description', tool_name)
        else:
            schema = {'name': tool_name}
            desc = tool_name
            
        # Generate embedding
        corpus_text = f"{tool_name}: {desc}"
        embedding = model.encode(corpus_text).tolist()
        
        data = {
            "name": tool_name,
            "description": desc,
            "schema": schema,
            "category": getattr(tool, 'category', 'general') if not isinstance(tool, dict) else 'mcp',
            "embedding": embedding
        }
        
        print(f"  Uploading '{tool_name}'...")
        # Upsert logic
        res = supabase.upsert("tools", data, on_conflict="name")
        if not res:
            print(f"  Failed to upsert '{tool_name}'")

    print("\nSupabase tool population complete.")

if __name__ == "__main__":
    asyncio.run(populate_supabase_tools())
