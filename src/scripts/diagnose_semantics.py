
import sys
import os
import asyncio
import argparse
import logging

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from src.infrastructure.tools.registry import ToolRegistry
from src.shared.config_manager import ConfigManager
from src.core.intelligence.prompt_builder import SystemPromptBuilder

# Silence logs
logging.getLogger("ToolRegistry").setLevel(logging.ERROR)
logging.getLogger("ExecutorStrategy").setLevel(logging.ERROR)

async def run_diagnostic(query: str, target: str = None, top_k: int = 10):
    print(f"\n--- Semantic Tool Diagnostic ---")
    print(f"Query: '{query}'")
    if target:
        print(f"Target Tool: '{target}'")
    print("-" * 35)
    
    registry = ToolRegistry()
    config = ConfigManager()
    
    # Initialize registry (scans src/tools and src/skills)
    registry.discover_all(config)
    
    # We might need to mock MCP tools if they aren't connected
    # For now, we'll just check what's registered
    
    try:
        # Get raw ranked list
        results = registry.search_tools(query, limit=top_k)
        
        print(f"\nTop {top_k} Recommended Tools:")
        found_target = False
        target_rank = -1
        
        for i, schema in enumerate(results):
            name = schema.get("name", "unknown")
            marker = ""
            if target and name.lower() == target.lower():
                marker = " <--- [ TARGET ]"
                found_target = True
                target_rank = i + 1
            
            desc = schema.get('description', 'No description')
                
            # Truncate description
            desc = (desc[:75] + '...') if len(desc) > 75 else desc
            
            print(f"  {i+1:2}. {name:15} | {desc}{marker}")

        print("\n" + "=" * 35)
        if target:
            if found_target:
                if target_rank <= 5:
                    print(f"SUCCESS: '{target}' is at Rank #{target_rank} (High Priority)")
                else:
                    print(f"WARNING: '{target}' is at Rank #{target_rank} (Might be pruned)")
            else:
                print(f"FAILURE: '{target}' was NOT found in top {top_k}")
        
        # Check 'is_simple' status (affects thinking level)
        schemas_result = registry.get_schemas_for_turn(query)
        is_simple = schemas_result.get("is_simple", False)
        print(f"Complexity Check: {'Simple (Minimal Thinking)' if is_simple else 'Complex (Deep Thinking)'}")
        print("=" * 35 + "\n")

    except Exception as e:
        print(f"Error during diagnostic: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnose semantic tool selection for a query.")
    parser.add_argument("query", help="The user query to test (e.g., 'open discord')")
    parser.add_argument("--target", help="Optional: The tool name you expect to see")
    parser.add_argument("--k", type=int, default=10, help="Number of tools to show")
    
    args = parser.parse_args()
    
    asyncio.run(run_diagnostic(args.query, args.target, args.k))
