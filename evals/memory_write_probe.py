"""Deterministic memory-WRITE eval — does the gate keep the graph clean?

Companion to memory_recall_probe.py. Recall asks "can Chad find the fact"; this
asks "should that fact have been stored at all, in that shape". Zero model
tokens: the gate is pure logic plus one embedding lookup.

Each case feeds a candidate save through MemoryWriteGate against the fictional
persona fixture and checks the decision:

  expect_saved        exact facts that must be stored (post-normalization)
  expect_saved_count  how many nodes the call should produce
  expect_skip_reason  substring that must appear in the rejection text — this is
                      what the model reads mid-turn, so it is part of the contract
  expect_conflict     the save must flag a possible supersession (either
                      mechanism — embedding similarity or a typed relation)
  expect_structural_conflict
                      specifically a single-valued typed relation replacing a
                      live edge (#656), which is a certainty rather than a
                      guess. Assert this, not expect_conflict, when the point of
                      the case is the temporal-edge path.

    python -m evals.memory_write_probe
    python -m evals.memory_write_probe --verbose

Exit code 1 if any case fails.
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
DATASET = REPO_ROOT / "evals" / "datasets" / "memory_write.jsonl"


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("//")
    ]


def check(case: dict, decision) -> list[str]:
    """Return a list of failure messages (empty means the case passed)."""
    problems: list[str] = []

    if "expect_saved" in case:
        if decision.accepted != case["expect_saved"]:
            problems.append(f"saved {decision.accepted!r}, wanted {case['expect_saved']!r}")
    if "expect_saved_count" in case:
        if len(decision.accepted) != case["expect_saved_count"]:
            problems.append(
                f"saved {len(decision.accepted)} fact(s), wanted {case['expect_saved_count']}"
            )
    if "expect_skip_reason" in case:
        reasons = " ".join(reason for _, reason in decision.skipped).lower()
        if case["expect_skip_reason"].lower() not in reasons:
            problems.append(
                f"skip reason {reasons!r} does not mention {case['expect_skip_reason']!r}"
            )
    if "expect_conflict" in case:
        if case["expect_conflict"] and not decision.conflicts:
            problems.append("expected a supersession flag, got none")
        # Explicit False pins a documented blind spot, so we notice if it changes.
        if not case["expect_conflict"] and decision.conflicts:
            problems.append(f"unexpected supersession flag: {decision.conflicts}")

    # The two detection mechanisms mean different things and are asserted
    # separately (#656): an embedding near-miss is a guess that leaves the old
    # fact live, while a structural hit on a single-valued typed relation is a
    # certainty that has already expired the old edge.
    if "expect_structural_conflict" in case:
        structural = [c for c in decision.conflicts if c.structural]
        if case["expect_structural_conflict"] and not structural:
            problems.append(
                f"expected a STRUCTURAL supersession, got {decision.conflicts or 'none'}"
            )
        if not case["expect_structural_conflict"] and structural:
            problems.append(f"unexpected structural supersession: {structural}")

    return problems


async def run(verbose: bool = False) -> int:
    from src.core.intelligence import graph_rag_client as grc
    from src.core.intelligence.graph_rag_client import GraphRAGClient
    from src.core.intelligence.memory_write_gate import MemoryWriteGate
    from src.core.intelligence.personal_knowledge_parse import parse_personal_knowledge
    from src.shared.config_manager import ConfigManager

    parsed = parse_personal_knowledge(FIXTURE.read_text(encoding="utf-8"))
    tmp_dir = tempfile.TemporaryDirectory()
    graph_path = str(Path(tmp_dir.name) / "graph.json")

    real_get = ConfigManager.get
    ConfigManager.get = staticmethod(  # type: ignore[method-assign]
        lambda key, default=None: graph_path
        if key == "knowledge_graph.graph_path"
        else real_get(key, default)
    )
    original_loader = grc._load_personal_knowledge_seed
    grc._load_personal_knowledge_seed = lambda: parsed  # type: ignore[assignment]
    grc.SEED_FACTS, grc.SEED_ENTITIES, grc.SEED_EDGES = parsed.facts, parsed.entities, parsed.edges
    grc.SEED_CORE_PROFILE, grc.SEED_BEHAVIOR = parsed.core_profile, parsed.behavior

    try:
        client = GraphRAGClient()
        await client.initialize()
        gate = MemoryWriteGate.from_config(client)

        passed = 0
        by_cat: dict[str, list[bool]] = {}
        for case in load_cases():
            decision = await gate.evaluate(case["content"])
            problems = check(case, decision)
            ok = not problems
            passed += ok
            by_cat.setdefault(case["category"], []).append(ok)

            if not ok or verbose:
                print(f"[{'PASS' if ok else 'FAIL'}] ({case['category']}) {case['id']}")
            if not ok:
                print(f"    input: {case['content']!r}")
                for problem in problems:
                    print(f"    {problem}")
                if decision.skipped:
                    print(f"    skips: {decision.skipped}")
                if case.get("note"):
                    print(f"    note: {case['note']}")

        total = sum(len(v) for v in by_cat.values())
        print(f"\n=== memory write: {passed}/{total} ===")
        for cat, oks in sorted(by_cat.items()):
            print(f"  {cat:12} {sum(oks)}/{len(oks)}")
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
