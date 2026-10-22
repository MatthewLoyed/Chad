r"""
sleep_cognify.py — overnight/idle local-LLM cognify pass over the knowledge graph (#611).

Post-MVP Slice B from docs/research/TYPED_GRAPH_MEMORY.md (MVP Slice A / #608-#610
accepted first — see that doc's §7.4). Mirrors reflection.py's propose -> gate -> apply
architecture, applied to the knowledge graph instead of persona lessons:

    valid graph fact/observation nodes
              │
              ▼   cognify()  — judge proposes typed edges (incl. causal/in_service_of),
                    consolidated observations (with evidence fact ids), and a rewritten
                    char-capped core profile
        candidate items
              │
              ▼   deterministic Tier-1 sensitivity gate (health/financial/legal/etc.)
        non-sensitive items auto-admit; Tier-1 items + the core profile ALWAYS queue
              │
        ┌─────┴──────────────────────────────┐
        ▼                                     ▼
  apply_admitted()                    reflection_lessons queue (kind=sleep_cognify)
  writes non-sensitive edges/           — human reviews/approves each item in the
  observations directly to the graph    existing Reflection panel (#194), mirroring
                                         how reflection.py routes tools-scope lessons

CRITICAL SAFETY POSTURE: this is OFFLINE-ONLY. Nothing here runs on the hot (turn) read
path — GraphRAGClient.search()/max_similarity() never call cognify(). The core profile
rewrite is NEVER auto-applied (always queued for review), since it becomes always-on
context read every turn once approved. Tier-1 sensitive facts (health, financial, legal,
family conflict) are ALWAYS queued too, regardless of confidence.

CLI:
    # dry-run — cognify, gate, print the diff. NOTHING is written.
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m src.core.intelligence.sleep_cognify \
        --model "qwen/qwen3.6-27b"
    # write the auto-admitted (non-sensitive) batch directly; Tier-1/core-profile still
    # only ever land in the review queue regardless of --write.
    ... -m src.core.intelligence.sleep_cognify --model "qwen/qwen3.6-27b" --write
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SleepCognify")

# ──────────────────────────────────────────────────────────────────────────────
# Tier-1 sensitivity — deterministic (no LLM), mirrors _detect_topic's keyword style.
# Any candidate item whose rendered text OR source evidence content matches one of
# these categories is ALWAYS routed to the review queue, never auto-applied, no matter
# how confident the judge is.
# ──────────────────────────────────────────────────────────────────────────────
TIER1_KEYWORDS: Dict[str, frozenset] = {
    "health": frozenset({
        "health", "medical", "diagnosis", "diagnosed", "medication", "therapy",
        "therapist", "doctor", "symptom", "symptoms", "flare", "crohn", "illness",
        "mental health", "depression", "anxiety", "disability",
    }),
    "financial": frozenset({
        "salary", "income", "debt", "loan", "bank", "account number", "ssn",
        "social security", "credit card", "financial", "net worth", "savings",
        "tax return", "password", "api key", "secret",
    }),
    "legal": frozenset({
        "legal", "lawsuit", "lawyer", "attorney", "divorce", "custody", "arrest",
        "criminal", "contract dispute",
    }),
    "relationship_conflict": frozenset({
        "breakup", "broke up", "argument", "fight", "conflict", "affair",
        "cheating", "estranged", "falling out",
    }),
}


def classify_tier(text: str) -> Optional[str]:
    """Return the Tier-1 category name if `text` matches, else None (non-sensitive)."""
    low = (text or "").lower()
    for tier, keywords in TIER1_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return tier
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Judge prompt
# ──────────────────────────────────────────────────────────────────────────────

COGNIFY_SYSTEM = r"""You are the overnight "sleep cognify" pass for a personal AI assistant's knowledge graph. You run OFFLINE (never during a live conversation) over the user's existing graph facts. Your job: find relationships the facts imply but don't yet state as typed edges, consolidate recurring facts into short durable observations, and (rarely) propose a refreshed core profile.

You will receive a numbered list of FACTS, each with a stable id like `fact_003`. Reference facts ONLY by their id in your `evidence` lists — never invent an id.

=== THREE KINDS OF OUTPUT ===
(1) EDGE — a typed relationship between two entities the facts imply. Prefer WHY-shaped
    relations (`in_service_of`, `caused_by`, `motivated_by`) and causal/temporal ones
    (`happened_at`, `occurs_on`) over generic ones. Entities are referenced as
    `Type:Name` (e.g. `Person:Sam`, `Document:budget.xlsx`, `Goal:wedding_planning`);
    reuse an id from ALREADY KNOWN ENTITIES if it matches, otherwise invent a new
    `Type:Name` id (a new entity node will be created for it).
(2) OBSERVATION — a short, durable belief distilled from MULTIPLE facts that recur
    across the corpus (not a single fact restated). Must carry `evidence`: the fact ids
    it was distilled from (at least 2, unless the fact is unusually information-dense).
(3) CORE_PROFILE — OPTIONAL, at most one per cycle. A tiny paragraph (identity + top 2-3
    active relationships/goals) under the given character budget. Only emit this if the
    existing core profile (given below) is stale or missing; otherwise omit it entirely.

=== DISCIPLINE ===
Be conservative. An empty `items` list is correct and expected when nothing new is
supported by the evidence. Do NOT restate an edge or observation already in ALREADY
KNOWN GRAPH STATE. Do NOT hallucinate entities, dates, or relationships not implied by
the facts. Confidence reflects how directly the evidence supports the claim.

Respond with ONLY this JSON, no prose, no markdown fences, no <think> tags:
{"items": [
  {"kind":"edge","source":"Type:Name","relation":"snake_case_relation","target":"Type:Name","evidence":["fact_003"],"confidence":0.0-1.0},
  {"kind":"observation","text":"<terse distilled belief>","evidence":["fact_001","fact_004"],"confidence":0.0-1.0},
  {"kind":"core_profile","text":"<profile paragraph, must fit the character budget>","confidence":0.0-1.0}
]}
Return {"items": []} if nothing clears the bar."""


def _format_facts_block(facts: List[Tuple[str, str]]) -> str:
    return "\n".join(f"- {fid}: {content}" for fid, content in facts)


def _format_known_graph_state(entity_ids: List[str], edge_lines: List[str]) -> str:
    parts = []
    if entity_ids:
        parts.append("Known entities: " + ", ".join(sorted(entity_ids)[:80]))
    if edge_lines:
        parts.append("Known edges:\n" + "\n".join(f"- {l}" for l in edge_lines[:80]))
    return "\n\n".join(parts) if parts else "(graph has no entities/edges yet)"


@dataclass
class CognifyResult:
    fact_count: int = 0
    model: str = ""
    items: List[dict] = field(default_factory=list)
    empty_output: bool = False
    error: str = ""


async def _load_fact_corpus(graph) -> List[Tuple[str, str]]:
    """(node_id, content) for every valid fact/observation node — the judge's input."""
    from src.core.intelligence.graph_rag_client import _RETRIEVABLE_KINDS

    out: List[Tuple[str, str]] = []
    for nid, data in graph._graph.nodes(data=True):
        if not data.get("valid", True):
            continue
        if data.get("kind", "fact") not in _RETRIEVABLE_KINDS:
            continue
        content = (data.get("content") or "").strip()
        if content:
            out.append((str(nid), content))
    return out


def _known_graph_state(graph) -> Tuple[List[str], List[str]]:
    entity_ids = [
        str(nid) for nid, d in graph._graph.nodes(data=True)
        if d.get("valid", True) and d.get("kind") == "entity"
    ]
    edge_lines = []
    for src, tgt, _key, data in graph._graph.edges(keys=True, data=True):
        rel = data.get("relation", "related")
        if rel == "related":
            continue
        edge_lines.append(f"{graph.retrieval.node_label(src)} -{rel}-> {graph.retrieval.node_label(tgt)}")
    return entity_ids, edge_lines


async def cognify(model: str, *, core_profile_max_chars: int = 800) -> CognifyResult:
    """Run the sleep-cognify judge over the graph's fact corpus. Read-only — proposes only."""
    from evals.judges import build_judge_adapter, _extract_json_object
    from src.core.intelligence.graph_rag_client import GraphRAGClient

    graph = GraphRAGClient()
    await graph.initialize()

    facts = await _load_fact_corpus(graph)
    if not facts:
        return CognifyResult(model=model, error="no fact/observation nodes in the graph")

    entity_ids, edge_lines = _known_graph_state(graph)
    current_profile = await graph.get_core_profile() or "(none yet)"

    adapter = build_judge_adapter(model)
    user_msg = (
        f"CHARACTER BUDGET for core_profile: {core_profile_max_chars}\n\n"
        f"CURRENT CORE PROFILE:\n{current_profile}\n\n"
        f"ALREADY KNOWN GRAPH STATE:\n{_format_known_graph_state(entity_ids, edge_lines)}\n\n"
        f"FACTS:\n{_format_facts_block(facts)}"
    )
    messages = [
        {"role": "system", "content": COGNIFY_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    async def _ask(max_tokens: int, thinking: str):
        return await adapter.generate(messages=messages, temperature=0.0,
                                      thinking_level=thinking, max_tokens=max_tokens)

    resp = await _ask(max_tokens=6000, thinking="low")
    if not (resp.text or "").strip():
        resp = await _ask(max_tokens=9000, thinking="low")

    data = _extract_json_object(resp.text or "")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = []
    empty = not (resp.text or "").strip()
    return CognifyResult(fact_count=len(facts), model=model, items=items, empty_output=empty)


# ──────────────────────────────────────────────────────────────────────────────
# Propose (read-only, deterministic gate) -> Apply (gated write)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CognifyProposal:
    """Result of cognify -> deterministic Tier-1 gate. Read-only: describes WOULD-be writes."""
    result: CognifyResult
    auto_admit_edges: List[dict] = field(default_factory=list)
    auto_admit_observations: List[dict] = field(default_factory=list)
    pending_review: List[dict] = field(default_factory=list)  # Tier-1 edges/observations
    core_profile_candidate: Optional[dict] = None  # always pending — never auto-applied


def _render_edge_text(item: dict) -> str:
    return f"{item.get('source', '?')} -{item.get('relation', 'related')}-> {item.get('target', '?')}"


async def propose_cognify(model: str, *, confidence_floor: float = 0.6,
                          core_profile_max_chars: int = 800) -> CognifyProposal:
    """Cognify, then deterministically gate by Tier-1 sensitivity. WRITES NOTHING."""
    result = await cognify(model, core_profile_max_chars=core_profile_max_chars)
    proposal = CognifyProposal(result=result)
    if result.error or not result.items:
        return proposal

    for item in result.items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        confidence = float(item.get("confidence", 0.0) or 0.0)

        if kind == "core_profile":
            text = (item.get("text") or "").strip()
            if text:
                # Only one per cycle; last one wins if the judge emits more than one.
                proposal.core_profile_candidate = {**item, "text": text[:core_profile_max_chars]}
            continue

        if kind == "edge":
            if not (item.get("source") and item.get("relation") and item.get("target")):
                continue
            if confidence < confidence_floor:
                continue
            text = _render_edge_text(item)
            tier = classify_tier(text) or classify_tier(str(item.get("evidence", "")))
            if tier:
                proposal.pending_review.append({**item, "tier": tier, "text": text})
            else:
                proposal.auto_admit_edges.append(item)
            continue

        if kind == "observation":
            text = (item.get("text") or "").strip()
            if not text or confidence < confidence_floor:
                continue
            if len(item.get("evidence") or []) < 1:
                continue
            tier = classify_tier(text)
            if tier:
                proposal.pending_review.append({**item, "tier": tier})
            else:
                proposal.auto_admit_observations.append(item)
            continue

    return proposal


async def apply_admitted(proposal: CognifyProposal) -> Dict[str, bool]:
    """GATED WRITE. Writes ONLY the non-sensitive auto-admit batch.

    Tier-1 items and the core-profile candidate are never written here — they only
    ever reach the graph via the review queue (persist_pending_items + GUI approve),
    which calls apply_item() per row after a human decision.
    """
    from src.core.intelligence.graph_rag_client import GraphRAGClient

    results: Dict[str, bool] = {}
    if not proposal.auto_admit_edges and not proposal.auto_admit_observations:
        return results

    graph = GraphRAGClient()
    await graph.initialize()

    for item in proposal.auto_admit_edges:
        text = _render_edge_text(item)
        try:
            ok = await graph.upsert_inferred_edge(
                item["source"], item["target"], item["relation"], confidence="inferred")
            results[f"edge:{text}"] = bool(ok)
        except Exception as e:  # noqa: BLE001
            logger.error(f"apply_admitted edge write failed ({text}): {e}")
            results[f"edge:{text}"] = False

    for item in proposal.auto_admit_observations:
        text = (item.get("text") or "").strip()
        try:
            node_id = await graph.add_observation(text, item.get("evidence") or [])
            results[f"observation:{node_id}"] = True
        except Exception as e:  # noqa: BLE001
            logger.error(f"apply_admitted observation write failed ({text[:40]}): {e}")
            results[f"observation:{text[:40]}"] = False

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Review queue (Tier-1 sensitive + core profile) — reuses reflection_lessons_store's
# `reflection_lessons` table with kind='sleep_cognify', mirroring how reflection.py's
# `tools`-scope lessons already surface in the same GUI panel (#194).
# ──────────────────────────────────────────────────────────────────────────────

async def apply_item(item: Dict[str, Any]) -> bool:
    """Write ONE approved sleep-cognify review-queue row to its destination.

    Dispatched from reflection_lessons_store.apply_lesson() when kind=='sleep_cognify'.
    `item['scope']` is one of 'graph_edge' | 'observation' | 'core_profile' (set when
    the row was persisted — see persist_pending_items below). `item` is either the
    in-memory proposal item (has a `payload` dict) or a `reflection_lessons` DB row
    (payload was JSON-encoded into its `evidence` column by persist_pending_items).
    """
    from src.core.intelligence.graph_rag_client import GraphRAGClient

    scope = item.get("scope")
    payload = item.get("payload")
    if payload is None:
        raw = item.get("evidence")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            payload = {}
    graph = GraphRAGClient()
    await graph.initialize()

    if scope == "graph_edge":
        source, relation, target = payload.get("source"), payload.get("relation"), payload.get("target")
        if not (source and relation and target):
            return False
        return bool(await graph.upsert_inferred_edge(source, target, relation, confidence="observed"))

    if scope == "observation":
        text = (payload.get("text") or item.get("text") or "").strip()
        if not text:
            return False
        node_id = await graph.add_observation(text, payload.get("evidence") or [])
        return bool(node_id)

    if scope == "core_profile":
        text = (payload.get("text") or item.get("text") or "").strip()
        if not text:
            return False
        await graph.set_core_profile(text)
        return True

    return False


def _payload_for(item: dict) -> dict:
    kind = item.get("kind")
    if kind == "edge":
        return {"source": item.get("source"), "relation": item.get("relation"), "target": item.get("target")}
    if kind == "observation":
        return {"text": item.get("text"), "evidence": item.get("evidence") or []}
    if kind == "core_profile":
        return {"text": item.get("text")}
    return {}


def _scope_for(item: dict) -> str:
    return {"edge": "graph_edge", "observation": "observation", "core_profile": "core_profile"}.get(
        item.get("kind"), "")


def _text_for(item: dict) -> str:
    if item.get("kind") == "edge":
        return _render_edge_text(item)
    return (item.get("text") or "").strip()


async def persist_pending_items(proposal: CognifyProposal) -> int:
    """Surface Tier-1 items + the core-profile candidate as reviewable rows.

    Mirrors heartbeat_service._maybe_run_reflection's use of persist_lesson_and_publish
    — one row per item, deduped against the same queue, visible in the existing
    Reflection GUI panel. Returns the number of rows actually persisted.
    """
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    from src.core.intelligence.reflection_lessons_store import persist_lesson_and_publish

    supabase = get_supabase_manager()
    if not supabase or not supabase.enabled:
        logger.warning("[ SLEEP_COGNIFY ] Supabase unavailable — pending items NOT persisted "
                       "(local shim still applies if configured).")

    persisted = 0
    candidates = list(proposal.pending_review)
    if proposal.core_profile_candidate:
        candidates.append(proposal.core_profile_candidate)

    for item in candidates:
        row_item = {
            "kind": "sleep_cognify",
            "scope": _scope_for(item),
            "text": _text_for(item),
            "why": f"Tier-1 ({item.get('tier')})" if item.get("tier") else "core profile rewrite",
            "evidence": json.dumps(_payload_for(item)),
            "confidence": item.get("confidence", 0.0),
        }
        if not row_item["scope"] or not row_item["text"]:
            continue
        ok = await persist_lesson_and_publish(supabase, row_item, "sleep_cognify")
        if ok:
            persisted += 1
    return persisted


# ──────────────────────────────────────────────────────────────────────────────
# CLI — dry-run by default; --write requires a typed confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _render_proposal(p: CognifyProposal) -> str:
    out: List[str] = ["# Sleep Cognify — proposal (#611)\n"]
    r = p.result
    if r.error:
        out.append(f"⚠️ {r.error}")
        return "\n".join(out)
    out.append(f"- judge: `{r.model}` | facts considered: {r.fact_count} | "
               f"items proposed: {len(r.items)}")
    out.append(f"- auto-admit: {len(p.auto_admit_edges)} edge(s), "
               f"{len(p.auto_admit_observations)} observation(s)")
    out.append(f"- **pending review (Tier-1 / core profile): {len(p.pending_review) + (1 if p.core_profile_candidate else 0)}**\n")

    if p.auto_admit_edges:
        out.append("### ✅ Auto-admit edges (non-sensitive)\n")
        for e in p.auto_admit_edges:
            out.append(f"- {_render_edge_text(e)} (conf={e.get('confidence')})")
        out.append("")
    if p.auto_admit_observations:
        out.append("### ✅ Auto-admit observations (non-sensitive)\n")
        for o in p.auto_admit_observations:
            out.append(f"- {o.get('text')} (evidence={o.get('evidence')}, conf={o.get('confidence')})")
        out.append("")
    if p.pending_review:
        out.append("### 🔒 Pending review — Tier-1 sensitive\n")
        for item in p.pending_review:
            out.append(f"- [{item.get('tier')}] {_text_for(item)} (conf={item.get('confidence')})")
        out.append("")
    if p.core_profile_candidate:
        out.append("### 🔒 Pending review — core profile rewrite\n")
        out.append(f"```\n{p.core_profile_candidate.get('text')}\n```\n")
    return "\n".join(out)


def main():
    import argparse
    import asyncio
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="Sleep cognify: extract typed edges/observations from the knowledge "
                    "graph's fact corpus, gate Tier-1 sensitive items to the review queue, "
                    "and (with --write) apply the non-sensitive auto-admit batch.")
    ap.add_argument("--model", default="qwen/qwen3.6-27b",
                    help="judge model id (local — this is an offline/idle job)")
    ap.add_argument("--confidence-floor", type=float, default=0.6)
    ap.add_argument("--core-profile-max-chars", type=int, default=800)
    ap.add_argument("--write", action="store_true",
                    help="apply the auto-admit (non-sensitive) batch directly; also "
                        "persists Tier-1/core-profile items to the review queue")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"[sleep_cognify] judge='{args.model}' ({'WRITE-gated' if args.write else 'DRY-RUN'})...")
    proposal = asyncio.run(propose_cognify(
        args.model, confidence_floor=args.confidence_floor,
        core_profile_max_chars=args.core_profile_max_chars))

    report = _render_proposal(proposal)
    runs_dir = Path(__file__).resolve().parents[3] / "evals" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "sleep_cognify_proposal_latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print("\n[sleep_cognify] report: evals/runs/sleep_cognify_proposal_latest.md")

    if not args.write:
        print("\n[sleep_cognify] DRY-RUN — nothing written. Re-run with --write to apply "
              "the auto-admit batch (Tier-1/core-profile still only reach the review queue).")
        return

    try:
        answer = input(
            f"Type 'WRITE' to apply {len(proposal.auto_admit_edges)} edge(s) + "
            f"{len(proposal.auto_admit_observations)} observation(s) directly, and queue "
            f"the rest for review: ").strip()
    except EOFError:
        answer = ""
    if answer != "WRITE":
        print("[sleep_cognify] aborted — no write performed.")
        return

    results = asyncio.run(apply_admitted(proposal))
    for dest, ok in results.items():
        print(f"[sleep_cognify] {dest}: {'✅ written' if ok else '❌ FAILED'}")

    n_queued = asyncio.run(persist_pending_items(proposal))
    print(f"[sleep_cognify] {n_queued} item(s) surfaced to the review queue.")


if __name__ == "__main__":
    main()
