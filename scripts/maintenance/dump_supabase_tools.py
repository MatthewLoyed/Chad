
import asyncio
import json
from src.infrastructure.database.supabase_manager import get_supabase_manager

async def dump_tools():
    supabase = get_supabase_manager()
    tools = await supabase.async_query("tools", select="name,description")
    
    with open("supabase_tools_dump.json", "w") as f:
        json.dump(tools, f, indent=2)
    
    print(f"Dumped {len(tools)} tools to supabase_tools_dump.json")

if __name__ == "__main__":
    asyncio.run(dump_tools())
