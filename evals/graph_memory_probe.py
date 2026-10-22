"""
graph_memory_probe.py — no-LLM fixture probes for the typed knowledge graph (#610).

MVP A3 acceptance for docs/research/TYPED_GRAPH_MEMORY.md: seeds a real GraphRAGClient
(NetworkX + a temp JSON file) from a fixture `## _graph` markdown block — the same
parser/seed/walk/budget code path used in production — and asserts `must_include` /
`must_not` on the real `.search()` output for each probe in
``evals/fixtures/graph_memory.jsonl``. No embedding model, no chat LLM: `_get_embedding`
is monkeypatched to `None` so scoring falls back to the deterministic keyword path,
matching how this probe behaves with or without LM Studio running.

Run:
    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe -m evals.runner --graph-memory
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "evals" / "fixtures" / "graph_memory.jsonl"
RUNS_DIR = PROJECT_ROOT / "evals" / "runs"

# Fixture personal_knowledge.md — mirrors the shape of config/personal_knowledge.md's
# real `_graph` block (Person/partner_of, Event/Document/Goal via about + in_service_of)
# without depending on the user's actual file, so this probe is deterministic in CI.
FIXTURE_MD = """## identity

Location is Portland, Oregon.

## family

Sam is Alex's fiancée.

## _graph

- Person:Alex
- Person:Sam
- Event:Wedding2026
- Place:Venue
- Document:WeddingBudget
- Goal:wedding_planning

- Sam -partner_of-> Alex
- Event:Wedding2026 -happened_at-> Place:Venue
- Document:WeddingBudget -about-> Event:Wedding2026
- Document:WeddingBudget -in_service_of-> Goal:wedding_planning
"""


@dataclass
class ProbeCase:
    id: str
    prompt: str
    must_include: List[str] = field(default_factory=list)
    must_not: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ProbeResult:
    case: ProbeCase
    passed: bool
    returned: str
    missing: List[str] = field(default_factory=list)
    forbidden_hits: List[str] = field(default_factory=list)
    error: str = ""


def load_cases(path: Path = DATASET) -> List[ProbeCase]:
    cases: List[ProbeCase] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}:{line_no}: invalid JSON ({e})") from e
        cases.append(
            ProbeCase(
                id=str(obj["id"]),
                prompt=str(obj["prompt"]),
                must_include=list(obj.get("must_include") or []),
                must_not=list(obj.get("must_not") or []),
                note=str(obj.get("note") or ""),
            )
        )
    return cases


def _check(case: ProbeCase, text: Optional[str]) -> ProbeResult:
    returned = text or ""
    missing = [s for s in case.must_include if s.lower() not in returned.lower()]
    forbidden = [s for s in case.must_not if s.lower() in returned.lower()]
    return ProbeResult(
        case=case,
        passed=not missing and not forbidden and bool(returned.strip()),
        returned=returned,
        missing=missing,
        forbidden_hits=forbidden,
        error="" if returned.strip() else "empty retrieval result",
    )


async def run_graph_memory_probe(cases: Optional[List[ProbeCase]] = None) -> List[ProbeResult]:
    cases = cases or load_cases()
    results: List[ProbeResult] = []

    from src.core.intelligence import graph_rag_client as graph_rag_client_module
    from src.core.intelligence.personal_knowledge_parse import parse_personal_knowledge

    parsed = parse_personal_knowledge(FIXTURE_MD)

    with tempfile.TemporaryDirectory(prefix="chad_graph_mem_") as tmp:
        graph_path = str(Path(tmp) / "graph_memory_fixture.json")

        from src.shared.config_manager import ConfigManager
        real_get = ConfigManager.get.__func__

        def fake_get(cls, key_path, default=None):
            if key_path == "knowledge_graph.graph_path":
                return graph_path
            return real_get(cls, key_path, default)

        with patch.object(
            graph_rag_client_module, "_load_personal_knowledge_seed",
            lambda: (parsed.facts, parsed.entities, parsed.edges),
        ), patch.object(
            graph_rag_client_module, "SEED_FACTS", parsed.facts,
        ), patch.object(
            graph_rag_client_module, "SEED_ENTITIES", parsed.entities,
        ), patch.object(
            graph_rag_client_module, "SEED_EDGES", parsed.edges,
        ), patch.object(
            ConfigManager, "get", classmethod(fake_get),
        ), patch.object(
            graph_rag_client_module.GraphRAGClient, "_get_embedding", lambda self, text: None,
        ):
            client = graph_rag_client_module.GraphRAGClient()
            await client.initialize()

            for case in cases:
                try:
                    text = await client.search(case.prompt)
                    results.append(_check(case, text))
                except Exception as e:  # noqa: BLE001
                    results.append(ProbeResult(case=case, passed=False, returned="", error=str(e)))
    return results


def render_report(results: List[ProbeResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    lines = [
        "# Typed-graph memory fixture probes (no LLM) — #610 MVP A3 acceptance",
        "",
        f"- cases: **{len(results)}** | passed: **{passed}** | failed: **{len(results) - passed}**",
        "",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"## [{mark}] `{r.case.id}`")
        lines.append(f"- prompt: {r.case.prompt}")
        if r.case.note:
            lines.append(f"- note: {r.case.note}")
        if r.error:
            lines.append(f"- error: {r.error}")
        if r.missing:
            lines.append(f"- missing must_include: {r.missing}")
        if r.forbidden_hits:
            lines.append(f"- hit must_not: {r.forbidden_hits}")
        snippet = (r.returned or "").strip().replace("\n", " / ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        lines.append(f"- returned: {snippet or '(empty)'}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = asyncio.run(run_graph_memory_probe())
    report = render_report(results)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "graph_memory_latest.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
