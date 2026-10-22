import sys, os, asyncio, json
from pathlib import Path

# Force UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

def tok(text: str) -> int:
    if not text:
        return 0
    return len(enc.encode(text))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.repositories.prompt_registry import PromptRegistry
from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.core.skill_system.skill_loader import get_skill_loader
from src.infrastructure.tools.mcp_roster import build_active_mcp_roster
from src.infrastructure.tools.registry import ToolRegistry
from src.core.orchestration.agents.factory import AGENT_CONFIGS
from src.core.orchestration.handoffs import create_handoff_tools
from langchain_core.utils.function_calling import convert_to_openai_tool
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.activity_rag_client import ActivityRAGClient
from src.core.intelligence.drive_catalog_client import DriveCatalogClient
from src.core.intelligence.session_history_client import SessionHistoryClient
from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient
from src.core.intelligence.entity_grounding_client import EntityGroundingClient
from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost

PROMPTS = [
    {
        "id": "prompt_1",
        "name": "Kroki Flowchart (Skill expected)",
        "query": "Can you draw an architecture flowchart showing how the Limb connects to the Brain server over WebSocket?",
        "expected_skill": "kroki"
    },
    {
        "id": "prompt_2",
        "name": "Marp/Presenton Slides (Skill expected)",
        "query": "Create a 5-slide presentation on our Q3 release roadmap.",
        "expected_skill": "presenton/marp"
    },
    {
        "id": "prompt_3",
        "name": "Typst Invoice PDF (Skill expected)",
        "query": "Generate a formatted project invoice and status report as a designed PDF.",
        "expected_skill": "typst"
    },
    {
        "id": "prompt_4",
        "name": "Drive + Memory + Summary Doc (No skill expected)",
        "query": "Search my Google Drive for files mentioning wedding budget, look up in memory what we discussed about catering costs, and draft a summary in artifacts/wedding_budget_summary.md",
        "expected_skill": "None"
    },
    {
        "id": "prompt_5",
        "name": "Web Search + Local Code + Calendar (No skill expected)",
        "query": "Search the web for Python 3.13 changelog features, search local files in src/core/orchestration for deprecated imports, and check my upcoming calendar for free slots this week.",
        "expected_skill": "None"
    }
]

async def run_ab_comparison():
    print("=" * 80)
    print("A/B COMPARISON: FULL SKILL CATALOG (A) vs ON-DEMAND SKILL BRIEF (B)")
    print("=" * 80)
    
    loader = get_skill_loader()
    if loader.is_empty:
        await loader.initialize()
        
    results = []
    
    for item in PROMPTS:
        qid = item["id"]
        qname = item["name"]
        query = item["query"]
        expected = item["expected_skill"]
        
        # Version A: Full catalog
        catalog_a = loader.build_catalog_prompt(full=True)
        chars_a = len(catalog_a)
        tokens_a = tok(catalog_a)
        
        # Version B: On-demand brief
        catalog_b = loader.build_catalog_prompt(query=query)
        chars_b = len(catalog_b)
        tokens_b = tok(catalog_b)
        
        # Tokens saved
        tokens_saved = tokens_a - tokens_b
        pct_saved = (tokens_saved / tokens_a) * 100 if tokens_a > 0 else 0
        
        # Check skill surfacing
        matched_skills = [e.name for e in loader.find_relevant_skills(query)]
        
        results.append({
            "id": qid,
            "name": qname,
            "query": query,
            "expected_skill": expected,
            "matched_skills": matched_skills,
            "chars_a": chars_a,
            "tokens_a": tokens_a,
            "chars_b": chars_b,
            "tokens_b": tokens_b,
            "tokens_saved": tokens_saved,
            "pct_saved": pct_saved,
            "brief_sample": catalog_b.replace("\n", " ")[:100] if catalog_b else "None (0 chars)"
        })
        
        print(f"\n[{qid}] {qname}")
        print(f"  Query: {query[:70]}...")
        print(f"  Expected Skill: {expected}")
        print(f"  Matched Skills: {matched_skills}")
        print(f"  Version A (Full Catalog):     {chars_a:5d} chars | {tokens_a:5d} tokens")
        print(f"  Version B (On-Demand Brief):  {chars_b:5d} chars | {tokens_b:5d} tokens")
        print(f"  Savings:                      {tokens_saved:5d} tokens ({pct_saved:.1f}% reduction in catalog)")
        if catalog_b:
            print(f"  Brief Preview: {catalog_b.strip()[:140]}...")
        else:
            print(f"  Brief Preview: [No catalog injected - 0 tokens!]")
            
    # Summary Table
    print("\n" + "=" * 80)
    print("SUMMARY RESULTS TABLE")
    print("=" * 80)
    print(f"{'ID':6s} | {'Test Scenario':32s} | {'Matched':18s} | {'A (Full)':8s} | {'B (Brief)':8s} | {'Saved Tokens':12s}")
    print("-" * 95)
    for r in results:
        m_str = ",".join(r["matched_skills"]) or "None"
        print(f"{r['id']:6s} | {r['name'][:32]:32s} | {m_str:18s} | {r['tokens_a']:8d} | {r['tokens_b']:8d} | -{r['tokens_saved']:5d} ({r['pct_saved']:.0f}%)")
        
    total_saved = sum(r["tokens_saved"] for r in results)
    avg_saved = total_saved / len(results)
    print("-" * 95)
    print(f"Average tokens saved per turn across 5 prompts: {avg_saved:.1f} tokens\n")
    
    out_file = PROJECT_ROOT / "data" / "ab_prompt_reduction_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Saved full benchmark results to {out_file}")

if __name__ == "__main__":
    asyncio.run(run_ab_comparison())
