import asyncio
import os
import sys

# Ensure project root is in python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.intelligence.query_synthesizer import QuerySynthesizer
from src.infrastructure.tools.selection.semantic_selector import SemanticToolSelector
from src.infrastructure.database.supabase_manager import get_supabase_manager

# List of 10 highly representative test prompts
PROMPTS = [
    # 1. Checking Emails
    "check my emails for any updates on the project status",
    
    # 2. Opening Apps
    "open discord and Slack, then start Google Chrome",
    
    # 3. Multi-step Developer Flow
    "read the file at src/main.py, check git status, and push changes to origin",
    
    # 4. Turn On Lamp / IoT
    "turn on my study desk lamp and dim the bedroom ceiling lights to 50%",
    
    # 5. Coding Task
    "write a python script to parse a csv file, execute it, and check the output",
    
    # 6. Task Management
    "add a new urgent task 'buy groceries' due tomorrow in my todoist inbox",
    
    # 7. Web Search
    "search the web for the latest price of nvidia stock and summarize it",
    
    # 8. UI Automation / Screen Capture
    "take a screenshot of the current screen to see what window is active",
    
    # 9. Conversational/Strict Negative (should yield minimal/no tools)
    "hello there! how are you doing today?",
    
    # 10. System Management
    "increase my system audio volume to 80% and raise the screen brightness to max"
]

async def run_baseline_evaluation():
    log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "baseline_output.log"))
    
    with open(log_path, "w", encoding="utf-8") as f:
        def log_write(text):
            print(text)
            f.write(text + "\n")
            
        log_write("=" * 80)
        log_write("RAG BASELINE EVALUATION - NOMIC V2 MOE MODEL")
        log_write("=" * 80)
        
        selector = SemanticToolSelector()
        # Ensure local SentenceTransformer is fully loaded
        selector._ensure_model()
        
        from src.infrastructure.tools.selection.semantic_selector import COSINE_THRESHOLD, RRF_THRESHOLD
        log_write(f"\n[ CONFIG ] Active Thresholds:")
        log_write(f"  - Cosine Threshold (Vector): {COSINE_THRESHOLD}")
        log_write(f"  - RRF Threshold (Hybrid):    {RRF_THRESHOLD}")
        log_write("-" * 80)

        supabase = get_supabase_manager()

        for idx, prompt in enumerate(PROMPTS, 1):
            log_write(f"\n\n--> PROMPT #{idx}: \"{prompt}\"")
            log_write("=" * 60)
            
            # 1. Decomposition
            sub_queries = QuerySynthesizer.decompose(prompt)
            log_write(f"  Decomposed into {len(sub_queries)} sub-intents:")
            for sq in sub_queries:
                log_write(f"    - \"{sq}\"")
            log_write("-" * 60)

            global_candidates = {}

            for sq in sub_queries:
                log_write(f"\n  * Sub-Intent: \"{sq}\"")
                log_write("  " + "." * 50)
                
                # Encode query
                query_text = f"search_query: {sq}"
                raw_emb = selector._model.encode(query_text)
                query_embedding = raw_emb.tolist()

                # Retrieve top 15 database matches
                try:
                    results = supabase.rpc(
                        "retrieve_relevant_tools",
                        {
                            "query_text": sq,
                            "query_embedding": query_embedding,
                            "match_count": 15
                        }
                    )
                except Exception as db_err:
                    log_write(f"    [ERROR] Supabase RPC failed: {db_err}")
                    continue

                if not results:
                    log_write("    (No matches returned from database)")
                    continue

                # Identify scoring domain
                highest_score = results[0].get('similarity', 0.0)
                is_rrf = highest_score < 0.1
                pruning_delta = RRF_THRESHOLD if is_rrf else COSINE_THRESHOLD
                metric_name = "RRF Hybrid" if is_rrf else "Cosine"

                log_write(f"    Highest DB similarity: {highest_score:.4f} ({metric_name})")
                log_write(f"    Pruning Delta in use:  {pruning_delta:.4f}")
                log_write(f"    Candidates returned from DB:")
                

                # Print status of each candidate in database response
                for rank, r in enumerate(results, 1):
                    name = r['name']
                    score = r['similarity']
                    gap = highest_score - score
                    
                    # Local pruning condition
                    is_pruned = gap > pruning_delta
                    status_str = "[PRUNED]" if is_pruned else "[EXPOSED]"
                    
                    desc = r.get('description', '') or ''
                    desc_ascii = desc.encode('ascii', 'ignore').decode('ascii')
                    desc_short = desc_ascii[:70] + "..." if len(desc_ascii) > 70 else desc_ascii
                    
                    log_write(f"      {rank:2d}. {name:<25} | Score: {score:.5f} | Gap: {gap:.5f} | {status_str:<9} | {desc_short}")

                    # Accumulate for global union (matching production local pruning)
                    if name not in global_candidates:
                        global_candidates[name] = {
                            'name': name,
                            'similarity': score,
                            'is_exposed': not is_pruned,
                            'matched_sub_queries': [sq]
                        }
                    else:
                        # Keep the highest similarity score
                        if score > global_candidates[name]['similarity']:
                            global_candidates[name]['similarity'] = score
                        # If it is exposed in any sub-query, it is globally exposed!
                        if not is_pruned:
                            global_candidates[name]['is_exposed'] = True
                        global_candidates[name]['matched_sub_queries'].append(sq)

            # Merge and output final exposed tools for the whole prompt
            log_write("-" * 60)
            # Re-sort combined list by highest similarity score
            merged_candidates = sorted(global_candidates.values(), key=lambda x: x['similarity'], reverse=True)
            
            if not merged_candidates:
                log_write("  FINAL EXPOSED TOOLS FOR PROMPT: None")
                continue
                
            final_exposed = []
            log_write("  FINAL SELECTION ANALYSIS (Local relative pruning per sub-intent):")
            for rank, c in enumerate(merged_candidates, 1):
                name = c['name']
                score = c['similarity']
                is_exposed = c['is_exposed']
                
                status_char = "o" if is_exposed else "x"
                status_text = "EXPOSED" if is_exposed else "PRUNED"
                log_write(f"    [{status_char}] {name:<25} | Max Score: {score:.5f} | Status: {status_text:<7} | Matched Sub-queries: {c['matched_sub_queries']}")
                
                if is_exposed:
                    final_exposed.append(name)
                    
            log_write(f"\n  --> FINAL EXPOSED TOOLS SUMMARY: {final_exposed}")
            log_write("=" * 60)

    print(f"\n[ SUCCESS ] Written complete baseline log to {log_path}")

if __name__ == "__main__":
    asyncio.run(run_baseline_evaluation())
