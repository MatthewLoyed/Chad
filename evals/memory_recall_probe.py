"""Deterministic memory-recall eval — does the right personal fact reach the model?

Zero model tokens: this is pure retrieval ranking plus prompt assembly. Scores each
query in evals/datasets/memory_recall.jsonl against a fictional persona fixture, so
it runs in CI without anyone's private personal_knowledge.md.

Three kinds of case:
  expect_any  — at least one of these substrings must appear in what Chad sees
                (always-on core profile + behavior block + retrieved boost).
  expect_none — no personal context may be retrieved (coding/general questions
                must not pull the user's family into the prompt). `allow_bullets`
                tolerates N weak hits for documented embedding near-collisions
                where no threshold separates them from real positives — see the
                `note` on those cases rather than tightening the ranker to fit.
  expect_none_substring
              — retrieval SHOULD fire, but one specific string must not be in it
                (a superseded fact leaking onto a present-tense query, #656).

    python -m evals.memory_recall_probe
    python -m evals.memory_recall_probe --verbose

Exit code 1 if any case fails. To debug one query interactively against your own
real graph, use scripts/probe_memory_recall.py instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "evals" / "fixtures" / "memory_recall_persona.md"
DATASET = REPO_ROOT / "evals" / "datasets" / "memory_recall.jsonl"


def load_cases() -> list[dict]:
    cases = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            cases.append(json.loads(line))
    return cases


async def run(verbose: bool = False) -> int:
    from src.core.intelligence import graph_always_on
    from src.core.intelligence import graph_rag_client as grc
    from src.core.intelligence.graph_rag_client import GraphRAGClient
    from src.core.intelligence.personal_knowledge_parse import parse_personal_knowledge
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    from src.shared.config_manager import ConfigManager

    parsed = parse_personal_knowledge(FIXTURE.read_text(encoding="utf-8"))

    tmp_dir = tempfile.TemporaryDirectory()
    graph_path = str(Path(tmp_dir.name) / "graph.json")

    real_get = ConfigManager.get
    overrides = {"knowledge_graph.graph_path": graph_path, "knowledge_graph.ego_node_id": "Person:Alex"}
    ConfigManager.get = staticmethod(  # type: ignore[method-assign]
        lambda key, default=None: overrides.get(key, real_get(key, default))
    )

    original_loader = grc._load_personal_knowledge_seed
    grc._load_personal_knowledge_seed = lambda: parsed  # type: ignore[assignment]
    grc.SEED_FACTS, grc.SEED_ENTITIES, grc.SEED_EDGES = parsed.facts, parsed.entities, parsed.edges
    grc.SEED_CORE_PROFILE, grc.SEED_BEHAVIOR = parsed.core_profile, parsed.behavior
    graph_always_on.reset_cache()  # this probe swaps the graph under the cache

    try:
        client = GraphRAGClient()
        await client.initialize()

        always_on = "\n".join(
            filter(None, [
                SystemPromptBuilder.core_profile_block(),
                SystemPromptBuilder.user_behavior_block(),
            ])
        )

        results: list[tuple[dict, bool, str]] = []
        for case in load_cases():
            block = await client.search(case["query"]) or ""
            if case.get("expect_none"):
                bullets = [ln for ln in block.splitlines() if ln.strip().startswith("-")]
                ok = len(bullets) <= case.get("allow_bullets", 0)
            elif case.get("expect_none_substring"):
                # Retrieval is allowed (and expected) to fire — one specific
                # string just must not be in it. Used for superseded facts,
                # where "returned nothing" would be the wrong assertion.
                ok = case["expect_none_substring"].lower() not in block.lower()
            else:
                seen = (always_on + "\n" + block).lower()
                ok = any(e.lower() in seen for e in case["expect_any"])
            results.append((case, ok, block))

        passed = sum(1 for _, ok, _ in results if ok)
        total = len(results)

        for case, ok, block in results:
            if not ok or verbose:
                print(f"[{'PASS' if ok else 'FAIL'}] ({case['category']}) {case['id']}: {case['query']}")
            if not ok:
                if case.get("expect_none"):
                    allowed = case.get("allow_bullets", 0)
                    print(f"    expected at most {allowed} personal bullet(s), got:\n{block}")
                elif case.get("expect_none_substring"):
                    print(f"    {case['expect_none_substring']!r} must NOT appear, got:\n{block}")
                else:
                    print(f"    wanted any of: {case['expect_any']}")
                    print(f"    got:\n{block or '(empty)'}")
                if case.get("note"):
                    print(f"    note: {case['note']}")

        by_cat: dict[str, list[bool]] = {}
        for case, ok, _ in results:
            by_cat.setdefault(case["category"], []).append(ok)
        print(f"\n=== memory recall: {passed}/{total} ===")
        for cat, oks in sorted(by_cat.items()):
            print(f"  {cat:18} {sum(oks)}/{len(oks)}")

        return 0 if passed == total else 1
    finally:
        grc._load_personal_knowledge_seed = original_loader  # type: ignore[assignment]
        ConfigManager.get = real_get  # type: ignore[method-assign]
        tmp_dir.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="print passing cases too")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(run(verbose=args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
