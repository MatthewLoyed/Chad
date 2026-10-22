
import sys
import os
import asyncio
from typing import List, Dict, Any

# Add project root to path
sys.path.append(os.path.abspath("."))

from src.infrastructure.tools import ToolRegistry as UnifiedRegistry

async def test_supabase_rag():
    registry = UnifiedRegistry()
    registry.discover_all()
    
    queries = [
        "Check my email",
        "Open discord and text Shloden",
        "What is the weather in London?",
        "check email, open discord and text my friend Shloden",
        "Summarize my recent docs and send them to Gmail",
        "Turn on my lamp"
    ]
    
    print("\n=== Supabase RAG Tool Selection Comparison ===")
    for query in queries:
        print(f"\nQuery: '{query}'")
        # UnifiedRegistry.get_schemas_for_turn is now updated to use Supabase RAG
        result = registry.get_schemas_for_turn(query)
        selected_tools = [s['name'] for s in result['schemas']]
        print(f"Selected Tools ({len(selected_tools)}): {', '.join(selected_tools)}")
        
        # Simple evaluation logic
        query_lower = query.lower()
        if "email" in query_lower or "gmail" in query_lower:
            has_email = any("gmail" in t.lower() for t in selected_tools)
            print(f"  Has Gmail: {'[PASS]' if has_email else '[FAIL]'}")
        
        if "discord" in query_lower:
            has_discord = any("discord" in t.lower() for t in selected_tools)
            print(f"  Has Discord: {'[PASS]' if has_discord else '[FAIL]'}")
            
        if "weather" in query_lower:
            has_weather = any("weather" in t.lower() or "search" in t.lower() for t in selected_tools)
            print(f"  Has Weather/Search: {'[PASS]' if has_weather else '[FAIL]'}")

        if "lamp" in query_lower or "light" in query_lower:
            has_hubspace = any("hubspace" in t.lower() for t in selected_tools)
            print(f"  Has Hubspace: {'[PASS]' if has_hubspace else '[FAIL]'}")

if __name__ == "__main__":
    asyncio.run(test_supabase_rag())
