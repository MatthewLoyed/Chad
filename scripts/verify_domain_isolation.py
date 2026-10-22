"""
End-to-end proof that domain-agent isolation holds through the REAL retrieval path.

Runs the actual GraphRAGClient + fetch_knowledge_boost fan-out that a live turn uses,
including the query cache — the component most likely to leak — but against a throwaway
graph file, so it never touches data/knowledge_graph.json and never calls a model.

    PYTHONPATH=. python scripts/verify_domain_isolation.py

Exits non-zero on any leak. Safe to run any time; it is the cheap standing check that the
guarantee still holds after touching retrieval.
"""

import asyncio
import os
import sys
import tempfile

FINANCIAL_FACT = "My brokerage account is at Fidelity"
FITNESS_FACT = "I bench press 185 pounds"
GLOBAL_FACT = "My name is Alex Rivera"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


async def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="chad_domain_verify_")
    graph_file = os.path.join(tmpdir, "knowledge_graph.json")

    from src.core.intelligence.graph_rag_client import GraphRAGClient
    from src.core.orchestration.context import domain_var
    from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost

    client = GraphRAGClient()
    await client.initialize()

    # Redirect every subsequent WRITE to a throwaway file and start from an empty graph.
    # Done after initialize() (which reads the real graph) so this never persists
    # verification facts into the user's data/knowledge_graph.json, and so seeded personal
    # facts can't accidentally mask a leak.
    client._graph_path = graph_file
    client._graph.clear()
    client._query_cache.clear()

    print("\n== 1. Writes are stamped with the turn's domain ==")
    domain_var.set("financial")
    await client.ingest(FINANCIAL_FACT, source="verify")
    domain_var.set("fitness")
    await client.ingest(FITNESS_FACT, source="verify")
    domain_var.set(None)
    await client.ingest(GLOBAL_FACT, source="verify")

    tagged = {d.get("content"): d.get("domain") for _, d in client._graph.nodes(data=True)
              if d.get("kind") == "fact"}
    check("financial fact tagged 'financial'", tagged.get(FINANCIAL_FACT) == "financial",
          f"got {tagged.get(FINANCIAL_FACT)!r}")
    check("fitness fact tagged 'fitness'", tagged.get(FITNESS_FACT) == "fitness",
          f"got {tagged.get(FITNESS_FACT)!r}")
    check("global fact left untagged", tagged.get(GLOBAL_FACT) is None,
          f"got {tagged.get(GLOBAL_FACT)!r}")

    print("\n== 2. Reads are scoped (via the real knowledge-boost fan-out) ==")

    async def boost(domain):
        domain_var.set(domain)
        return (await fetch_knowledge_boost("what is my brokerage account", client)) or ""

    fin = await boost("financial")
    check("financial turn sees its own fact", "Fidelity" in fin)
    check("financial turn does NOT see fitness", "bench press" not in fin, fin[:120])

    gen = await boost(None)
    check("general turn does NOT see financial", "Fidelity" not in gen, gen[:120])

    fit = await boost("fitness")
    check("fitness turn does NOT see financial", "Fidelity" not in fit, fit[:120])

    print("\n== 3. Layering: untagged globals stay visible everywhere ==")
    name_q = "what is my name"

    async def boost_q(domain, q):
        domain_var.set(domain)
        return (await fetch_knowledge_boost(q, client)) or ""

    check("financial turn still sees global fact", "Alex Rivera" in await boost_q("financial", name_q))
    check("fitness turn still sees global fact", "Alex Rivera" in await boost_q("fitness", name_q))
    check("general turn sees global fact", "Alex Rivera" in await boost_q(None, name_q))

    print("\n== 4. Cache does not leak across domains (same query, back to back) ==")
    # The original bug: cache keyed on query text alone, so this exact sequence served the
    # financial answer to the general turn.
    same = "what is my brokerage account"
    first = await boost_q("financial", same)
    second = await boost_q(None, same)
    third = await boost_q("fitness", same)
    check("cached financial answer not replayed to general", "Fidelity" not in second, second[:120])
    check("cached financial answer not replayed to fitness", "Fidelity" not in third, third[:120])
    check("financial still gets its own answer", "Fidelity" in first)

    print()
    if failures:
        print(f"LEAK DETECTED — {len(failures)} check(s) failed: {failures}")
        return 1
    print("All domain-isolation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
