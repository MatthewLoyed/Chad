"""Offline probe: what personal context does Chad actually see for a query?

Prints the always-on core profile plus the retrieved boost block (entity seeds,
walk lines in rank order, per-fact similarity vs the threshold), so you can see
*why* a fact did or didn't surface. No model calls, no server.

  # probe the configured graph with the built-in query list
  python scripts/probe_memory_recall.py

  # probe another checkout's graph (e.g. prod's), read-only
  CHAD_PROBE_GRAPH=/path/to/knowledge_graph.json python scripts/probe_memory_recall.py

  # probe your own queries
  python scripts/probe_memory_recall.py "plan a date night" "who is my best man"

For pass/fail scoring against expected facts, use `evals/memory_recall_probe.py`.
"""
from __future__ import annotations

import asyncio
import os
import sys

QUERIES = [
    "plan me a date with my fiancee, include places, prices, dessert, outfits",
    "what should I get my partner for their birthday",
    "how long until my wedding",
    "where do I work",
    "fix this python traceback in my websocket handler",
]


async def main(queries: list[str]) -> None:
    from src.core.intelligence.graph_rag_client import GraphRAGClient
    from src.core.intelligence.prompt_builder import SystemPromptBuilder

    client = GraphRAGClient()
    await client.initialize()

    override = os.environ.get("CHAD_PROBE_GRAPH", "")
    if override:
        import networkx as nx

        client._graph_path = override
        client._graph = nx.MultiDiGraph()
        client._load_graph()

    print(
        f"graph nodes={client._graph.number_of_nodes()} "
        f"edges={client._graph.number_of_edges()} ego={client.retrieval.ego_node_id()}\n"
    )
    print("=" * 100)
    print("ALWAYS-ON CORE PROFILE (every user-facing turn, not retrieved)")
    print("=" * 100)
    print(SystemPromptBuilder.core_profile_block() or "(none — add ## _core_profile to personal_knowledge.md)")

    for q in queries:
        print("\n" + "=" * 100)
        print("QUERY:", q)
        print("  seeds:", client.retrieval.seed_entity_ids(q))
        walk = client.retrieval.collect_walk_lines(q, max_hops=2)
        print(f"  walk_lines ({len(walk)}), top 8:")
        for line in walk[:8]:
            print("    ", line)
        hits = sorted(
            client.retrieval.collect_fact_hits(q, threshold=0.0),
            key=lambda h: h["similarity"],
            reverse=True,
        )
        print("  fact scores (top 6; threshold 0.22):")
        for h in hits[:6]:
            mark = "PASS" if h["similarity"] >= 0.22 else "drop"
            print(f"    [{mark}] {h['similarity']:.3f}  {h['content'][:80]!r}")
        print("  --- RETRIEVED BLOCK ---")
        print(await client.search(q) or "(none)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("PYTHONPATH", ".")
    asyncio.run(main(sys.argv[1:] or QUERIES))
