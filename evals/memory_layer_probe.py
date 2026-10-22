"""
memory_layer_probe.py — no-LLM fixture probes for Drive / filesystem / ActivityRAG.

Seeds a temp sqlite DB with known catalog + activity rows (plus tiny deterministic
toy embeddings — not an LLM), then runs the real DriveCatalogClient /
ActivityRAGClient search paths against prompts from
``evals/fixtures/memory_layer.jsonl`` and asserts ``must_include`` / ``must_not``
on the returned text (issue #618).

Run:
    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe -m evals.runner --memory-layer
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "evals" / "fixtures" / "memory_layer.jsonl"
RUNS_DIR = PROJECT_ROOT / "evals" / "runs"
_EMBED_DIM = 64
_STOP = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it",
    "my", "me", "i", "you", "that", "this", "with", "from", "about", "did",
    "anything", "where", "whats", "what's", "wheres", "where's", "disk",
})


@dataclass
class ProbeCase:
    id: str
    prompt: str
    layer: str
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
                layer=str(obj["layer"]),
                must_include=list(obj.get("must_include") or []),
                must_not=list(obj.get("must_not") or []),
                note=str(obj.get("note") or ""),
            )
        )
    return cases


def toy_embed(text: str, dim: int = _EMBED_DIM) -> List[float]:
    """Deterministic bag-of-tokens embedding — no model / no LLM."""
    vec = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(tok) < 3 or tok in _STOP:
            continue
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _seed_fixtures(repo) -> None:
    """Insert deterministic Drive / filesystem / activity rows into local sqlite."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=3)).isoformat()

    fixtures = [
        {
            "content": (
                "Drive File: 'Wedding Budget 2026.xlsx'\n"
                "Path: My Drive / Wedding / Finances"
            ),
            "category": "drive_catalog",
            "importance": 0.8,
            "source": "drive_catalog",
            "metadata": {
                "file_id": "fixture-drive-wedding-budget",
                "name": "Wedding Budget 2026.xlsx",
                "path": "My Drive / Wedding / Finances",
                "hash": "fix_drive_wedding_budget",
                "source": "drive_catalog",
            },
        },
        {
            "content": (
                "Drive File: 'tax_return_2019.pdf'\n"
                "Path: My Drive / Taxes / Archive"
            ),
            "category": "drive_catalog",
            "importance": 0.5,
            "source": "drive_catalog",
            "metadata": {
                "file_id": "fixture-drive-tax",
                "name": "tax_return_2019.pdf",
                "path": "My Drive / Taxes / Archive",
                "hash": "fix_drive_tax",
                "source": "drive_catalog",
            },
        },
        {
            "content": (
                "File: 'wedding_budget.xlsx' at C:/Users/matt/Documents/Wedding/wedding_budget.xlsx\n"
                f"Last Modified: {recent}"
            ),
            "category": "personal_index",
            "importance": 0.4,
            "source": "filesystem",
            "metadata": {
                "source": "filesystem",
                "path": "C:/Users/matt/Documents/Wedding/wedding_budget.xlsx",
                "name": "wedding_budget.xlsx",
                "timestamp": recent,
                "hash": "fix_fs_wedding_budget",
            },
        },
        {
            "content": "Watched YouTube: 'How to plan a wedding budget'",
            "category": "personal_index",
            "importance": 0.6,
            "source": "chrome",
            "metadata": {
                "source": "chrome",
                "type": "youtube_video",
                "title": "How to plan a wedding budget",
                "url": "https://www.youtube.com/watch?v=fixtureWeddingBudget",
                "timestamp": recent,
                "hash": "fix_chrome_yt_budget",
            },
        },
        {
            "content": (
                "Email from alice@example.com: 'Wedding venue deposit'\n"
                "Snippet: please confirm the venue deposit against the wedding budget"
            ),
            "category": "personal_index",
            "importance": 0.7,
            "source": "gmail",
            "metadata": {
                "source": "gmail",
                "message_id": "fixture-gmail-venue",
                "sender": "alice@example.com",
                "subject": "Wedding venue deposit",
                "timestamp": recent,
                "hash": "fix_gmail_venue",
            },
        },
        {
            "content": "Visited Web Page: 'Weekly grocery list ideas'",
            "category": "personal_index",
            "importance": 0.3,
            "source": "chrome",
            "metadata": {
                "source": "chrome",
                "type": "webpage",
                "title": "Weekly grocery list ideas",
                "url": "https://example.com/grocery",
                "timestamp": recent,
                "hash": "fix_chrome_grocery",
            },
        },
    ]

    for row in fixtures:
        mid = repo.create_memory(
            content=row["content"],
            category=row["category"],
            importance=row["importance"],
            source=row["source"],
            embedding=toy_embed(row["content"]),
            metadata=row["metadata"],
        )
        if mid == -1:
            raise RuntimeError(f"Failed to seed fixture: {row['metadata'].get('hash')}")


def _check(case: ProbeCase, text: Optional[str]) -> ProbeResult:
    returned = text or ""
    hay = returned.lower()
    missing = [s for s in case.must_include if s.lower() not in hay]
    forbidden = [s for s in case.must_not if s.lower() in hay]
    return ProbeResult(
        case=case,
        passed=not missing and not forbidden and bool(returned.strip()),
        returned=returned,
        missing=missing,
        forbidden_hits=forbidden,
        error="" if returned.strip() else "empty retrieval result",
    )


async def _search(layer: str, prompt: str, drive_client, activity_client) -> Optional[str]:
    if layer == "drive_catalog":
        return await drive_client.search(prompt, limit=5)
    if layer in ("activity", "filesystem"):
        # Filesystem catalog rows share personal_index with Chrome/Gmail activity.
        return await activity_client.search(prompt, limit=5)
    raise ValueError(f"Unknown layer: {layer}")


async def run_memory_layer_probe(cases: Optional[List[ProbeCase]] = None) -> List[ProbeResult]:
    cases = cases or load_cases()
    results: List[ProbeResult] = []

    with tempfile.TemporaryDirectory(prefix="chad_mem_layer_") as tmp:
        db_path = Path(tmp) / "fixture.db"
        os.environ["CHAD_LOCAL_DB_PATH"] = str(db_path)

        from src.infrastructure.database import local_store
        from src.infrastructure.repositories import memory_repository as mr_mod
        from src.core.intelligence.drive_catalog_client import DriveCatalogClient
        from src.core.intelligence.activity_rag_client import ActivityRAGClient
        from src.core.intelligence.memory_engine import MemoryEngine

        local_store._DB_PATH = None

        fake_sb = MagicMock()
        fake_sb.enabled = False

        def fake_get(key, default=None):
            overrides = {
                "memory.storage": "local",
                "drive_catalog.storage": "local",
                "drive_catalog.enabled": True,
                "drive_catalog.max_results": 5,
                "drive_catalog.relevance_threshold": 0.05,
                "knowledge_graph.activity_max_results": 5,
                "knowledge_graph.activity_relevance_threshold": 0.05,
                "knowledge_graph.max_item_chars": 240,
                "knowledge_graph.activity_max_age_days": 365,
            }
            if key in overrides:
                return overrides[key]
            return default

        with patch.object(mr_mod, "get_supabase_manager", return_value=fake_sb), patch(
            "src.shared.config_manager.ConfigManager.get", side_effect=fake_get
        ):
            repo = mr_mod.MemoryRepository()
            _seed_fixtures(repo)

            engine = MemoryEngine()
            engine.repository = repo
            engine.get_embedding = lambda text: toy_embed(text)  # type: ignore[method-assign]

            drive = DriveCatalogClient()
            drive._engine = engine
            activity = ActivityRAGClient()
            activity._engine = engine

            for case in cases:
                try:
                    text = await _search(case.layer, case.prompt, drive, activity)
                    results.append(_check(case, text))
                except Exception as e:  # noqa: BLE001
                    results.append(
                        ProbeResult(
                            case=case,
                            passed=False,
                            returned="",
                            error=str(e),
                        )
                    )
    return results


def render_report(results: List[ProbeResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    lines = [
        "# Memory-layer fixture probes (no LLM)",
        "",
        f"- cases: **{len(results)}** | passed: **{passed}** | failed: **{len(results) - passed}**",
        "",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"## [{mark}] `{r.case.id}` ({r.case.layer})")
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
    results = asyncio.run(run_memory_layer_probe())
    report = render_report(results)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "memory_layer_latest.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
