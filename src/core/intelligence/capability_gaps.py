"""
capability_gaps.py — the "blocked" self-improvement signal.

When Chad explicitly calls `search_for_tool` and the registry returns nothing, that
is his own deliberate admission of a missing capability. (Research note: watching for
"no tools selected" is a poor signal — the registry's fallback layers almost always
hand Chad *something* and he improvises. `search_for_tool` returning zero is a real,
high-precision admission. See docs/features/CAPABILITY_SELF_IMPROVEMENT.md.)

Pipeline, mirroring the Propose→Synthesize→Verify shape that CRAFT / SkillWeaver /
SkillCraft converged on:

  1. LOG       — every miss → `capability_gap_log` (survives short sessions). Cheap,
                 deterministic, runs on the hot path (fire-and-forget insert).
  2. CLUSTER   — `cluster_gaps()` groups recurring misses by embedding similarity, so
                 "send a telegram message" and "message someone on Telegram" count as
                 one gap. Pure + unit-testable. Deterministic, no LLM.
  3. DRAFT     — only once a cluster recurs >= min_cluster_size does an LLM draft a
                 concrete suggestion. Repetition is what makes the suggestion accurate
                 rather than a single-turn guess.
  4. GATEKEEP  — `SuggestionGatekeeper` rejects low-confidence drafts and anything that
                 duplicates a capability Chad already has (so it never suggests
                 rebuilding an existing tool/skill).
  5. SURFACE   — admitted suggestions are stored `pending` and a `CapabilitySuggestionRaised`
                 event is published (GUI can subscribe later). Nothing auto-writes code.

Stage 6 (execute-and-verify the drafted tool in a sandbox before surfacing — the
objective check Voyager-style self-judgment needs) is a documented extension point on
the analyzer (`verify_fn`); it is intentionally not wired in this phase.

Design principles (same as lesson_gatekeeper.py): the clustering and gatekeeper are
PURE and dependency-free — the analyzer injects the embedder, judge, and registry
snapshot — so the decision logic is trivially testable without a DB or a model.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Optional, Set

logger = logging.getLogger("CapabilityGaps")

# ── pure helpers ──────────────────────────────────────────────────────────────


def _norm_tokens(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _centroid(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    n = len(vectors)
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


def cluster_gaps(gaps: List[dict], *, sim_threshold: float = 0.82) -> List[List[dict]]:
    """Greedy single-pass clustering of gap rows by cosine similarity of their embedding.

    Each gap dict should carry `query_embedding` (list[float]); rows without one fall
    back to exact-text grouping so the function never drops data. Pure + deterministic
    (input order defines tie-breaks). Returns clusters sorted largest-first.
    """
    clusters: List[dict] = []  # {members: [...], centroid: [...], text_key: str}
    for g in gaps or []:
        emb = g.get("query_embedding")
        if emb:
            best_i, best_sim = -1, 0.0
            for i, c in enumerate(clusters):
                if not c["centroid"]:
                    continue
                sim = _cosine(emb, c["centroid"])
                if sim > best_sim:
                    best_i, best_sim = i, sim
            if best_i >= 0 and best_sim >= sim_threshold:
                c = clusters[best_i]
                c["members"].append(g)
                c["centroid"] = _centroid([m["query_embedding"] for m in c["members"]
                                           if m.get("query_embedding")])
                continue
            clusters.append({"members": [g], "centroid": list(emb), "text_key": None})
        else:
            key = (g.get("query_text") or "").strip().lower()
            match = next((c for c in clusters if c["text_key"] == key), None)
            if match:
                match["members"].append(g)
            else:
                clusters.append({"members": [g], "centroid": [], "text_key": key})

    out = [c["members"] for c in clusters]
    out.sort(key=len, reverse=True)
    return out


# ── suggestion gatekeeper (pure) ────────────────────────────────────────────────

VALID_KINDS = {"tool", "skill"}


@dataclass
class SuggestionGateOutcome:
    admitted: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return f"{len(self.admitted)} admitted, {len(self.rejected)} rejected"


class SuggestionGatekeeper:
    """Decides which drafted suggestions are worth surfacing. Pure — the caller passes
    snapshots of the live capability catalog and existing pending suggestions.

    Guards, in order (first failure wins):
      1. schema      — valid kind + non-empty title/description
      2. confidence  — judge confidence >= floor
      3. exists      — the proposed capability already overlaps a live tool/skill
                       (so Chad never suggests rebuilding something he has)
      4. duplicate   — near-duplicate of an existing pending suggestion
      5. batch-dup   — near-duplicate of an earlier admit this cycle
    """

    def __init__(self, *, confidence_floor: float = 0.7, dup_threshold: float = 0.6,
                 existing_capabilities: Optional[Iterable[str]] = None,
                 existing_suggestions: Optional[Iterable[str]] = None):
        self.confidence_floor = confidence_floor
        self.dup_threshold = dup_threshold
        # Each capability string = "name: description" of a live tool/skill.
        self._cap_tok = [_norm_tokens(c) for c in (existing_capabilities or [])]
        self._sugg_tok = [_norm_tokens(s) for s in (existing_suggestions or [])]

    def filter(self, candidates: List[dict]) -> SuggestionGateOutcome:
        out = SuggestionGateOutcome()
        admitted_tok: List[Set[str]] = []

        ordered = sorted(candidates or [],
                         key=lambda c: float(c.get("confidence", 0.0) or 0.0), reverse=True)
        for c in ordered:
            kind = (c.get("kind") or "").strip().lower()
            title = (c.get("title") or "").strip()
            desc = (c.get("description") or "").strip()
            conf = float(c.get("confidence", 0.0) or 0.0)

            def reject(reason: str):
                out.rejected.append({"title": title, "kind": kind, "reason": reason})

            if kind not in VALID_KINDS or not title or not desc:
                reject(f"invalid schema (kind={kind!r}, empty_title={not title}, empty_desc={not desc})")
                continue
            if conf < self.confidence_floor:
                reject(f"confidence {conf:.2f} < floor {self.confidence_floor:.2f}")
                continue

            tok = _norm_tokens(f"{title} {desc}")
            dup_cap = max((_jaccard(tok, e) for e in self._cap_tok), default=0.0)
            if dup_cap >= self.dup_threshold:
                reject(f"capability already exists (overlap {dup_cap:.2f}) — not worth rebuilding")
                continue
            dup_existing = max((_jaccard(tok, s) for s in self._sugg_tok), default=0.0)
            if dup_existing >= self.dup_threshold:
                reject(f"duplicate of an existing pending suggestion (overlap {dup_existing:.2f})")
                continue
            dup_batch = max((_jaccard(tok, a) for a in admitted_tok), default=0.0)
            if dup_batch >= self.dup_threshold:
                reject(f"duplicate of another candidate this cycle (overlap {dup_batch:.2f})")
                continue

            out.admitted.append({**c, "kind": kind, "title": title, "description": desc})
            admitted_tok.append(tok)

        return out


# ── persistence: log a gap (hot path) ───────────────────────────────────────────


async def log_gap(query_text: str, *, found_count: int = 0,
                  matched_tools: Optional[List[str]] = None,
                  turn_id: Optional[str] = None,
                  registry: Optional[object] = None) -> None:
    """Persist one capability-gap event, then nudge the analysis throttle.

    `registry` is the LIVE tool registry from the call site (ToolSearchTool already
    holds it). We use it to embed the query with the already-loaded RAG model and we
    stash it on the tracker so the background analysis pass can reuse the same populated
    registry rather than constructing an empty one.

    Best-effort: any failure is swallowed (logged), never raised — this runs on the
    turn's hot path via fire-and-forget and must never break a tool call.
    """
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return

        if registry is not None:
            CapabilityGapTracker._registry = registry

        embedding = None
        selector = getattr(registry, "_selector", None) if registry is not None else None
        if selector is not None and hasattr(selector, "embed_text"):
            try:
                embedding = selector.embed_text(query_text)
            except Exception:
                embedding = None

        row = {
            "query_text": query_text,
            "found_count": int(found_count),
            "matched_tools": matched_tools or [],
        }
        if embedding:
            row["query_embedding"] = embedding
        if turn_id:
            row["turn_id"] = turn_id

        res = await supabase.async_insert("capability_gap_log", row)
        if res:
            logger.info(f"[ CAP_GAP ] Logged capability gap: {query_text!r} (found={found_count})")
            await CapabilityGapTracker.increment_and_check()
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to log capability gap: {e}")


# ── analysis throttle (mirrors OATSRefiner) ─────────────────────────────────────


class CapabilityGapTracker:
    """Counts logged gaps and kicks off a background analysis pass once enough new
    misses have accumulated. Mirrors OATSRefiner so the trigger story is consistent.
    """

    _lock = asyncio.Lock()
    _counter = 0
    _is_analyzing = False
    _registry: Optional[object] = None  # live registry captured at log time

    @classmethod
    async def increment_and_check(cls, target_count: int = 3):
        async with cls._lock:
            cls._counter += 1
            logger.info(f"[ CAP_GAP ] Gaps since last analysis: {cls._counter}/{target_count}")
            if cls._counter >= target_count and not cls._is_analyzing:
                cls._counter = 0
                cls._is_analyzing = True
                asyncio.create_task(cls._run_analysis_in_background())

    @classmethod
    async def _run_analysis_in_background(cls):
        try:
            analyzer = build_default_analyzer()
            if analyzer:
                await analyzer.analyze()
        except Exception as e:
            logger.error(f"[ CAP_GAP ] Background analysis failed: {e}", exc_info=True)
        finally:
            async with cls._lock:
                cls._is_analyzing = False


# ── shared persistence (used by both the gap and pattern analyzers) ─────────────


async def existing_pending_titles(supabase) -> List[str]:
    """Return "title: description" for every still-open suggestion, so the gatekeeper
    can reject near-duplicates regardless of which analyzer drafted them."""
    rows = await supabase.async_query(
        "capability_suggestions", select="title,description",
        filters={"status": "in.(pending,shown)"}, limit=100) or []
    return [f"{r.get('title','')}: {r.get('description','')}" for r in rows]


async def persist_and_publish(supabase, cand: dict) -> bool:
    """Insert one suggestion row and publish CapabilitySuggestionRaised (GUI side-channel)."""
    row = {
        "kind": cand.get("kind", "tool"),
        "title": cand.get("title", ""),
        "description": cand.get("description", ""),
        "rationale": cand.get("rationale", ""),
        "evidence": cand.get("evidence", []),
        "confidence": float(cand.get("confidence", 0.0) or 0.0),
        "cluster_size": int(cand.get("cluster_size", 0)),
        "status": "pending",
    }
    res = await supabase.async_insert("capability_suggestions", row)
    if not res:
        return False
    logger.warning(f"[ CAP_GAP ] Surfaced capability suggestion: {row['title']!r} "
                   f"({row['kind']}, conf={row['confidence']:.2f}, backed by {row['cluster_size']} signals)")
    try:
        from src.application.events import EventBus, CapabilitySuggestionRaised
        sid = res[0].get("id") if isinstance(res, list) and res else None
        EventBus.get_instance().publish(CapabilitySuggestionRaised(
            suggestion_id=str(sid) if sid else None,
            kind=row["kind"], title=row["title"], description=row["description"],
            rationale=row["rationale"], confidence=row["confidence"],
            cluster_size=row["cluster_size"]))
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to publish CapabilitySuggestionRaised: {e}")
    return True


# ── analyzer ─────────────────────────────────────────────────────────────────

# Draft-fn type: given a representative gap query + the cluster's queries, return a
# candidate suggestion dict {kind, title, description, rationale, confidence} or None.
DraftFn = Callable[[str, List[str]], Awaitable[Optional[dict]]]
EmbedFn = Callable[[str], Optional[List[float]]]


@dataclass
class CapabilityGapAnalyzer:
    embed_fn: Optional[EmbedFn]
    draft_fn: DraftFn
    capability_snapshot: List[str]          # "name: description" for every live tool/skill
    min_cluster_size: int = 3
    sim_threshold: float = 0.82
    confidence_floor: float = 0.7
    lookback_limit: int = 200
    # Stage-6 extension point: objective execute-and-check before surfacing. Unwired.
    verify_fn: Optional[Callable[[dict], Awaitable[bool]]] = None

    async def analyze(self) -> int:
        """Cluster unprocessed gaps, draft+gatekeep suggestions for recurring ones,
        persist admits, and publish events. Returns the number of suggestions surfaced.
        """
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return 0

        gaps = await supabase.async_query(
            "capability_gap_log", select="*",
            filters={"processed_at": "is.null"},
            order="created_at.desc", limit=self.lookback_limit) or []
        if not gaps:
            return 0

        # Backfill embeddings for rows logged without one (e.g. embedder was cold).
        if self.embed_fn:
            for g in gaps:
                if not g.get("query_embedding"):
                    g["query_embedding"] = self.embed_fn(g.get("query_text") or "")

        clusters = cluster_gaps(gaps, sim_threshold=self.sim_threshold)
        recurring = [c for c in clusters if len(c) >= self.min_cluster_size]
        if not recurring:
            return 0

        # Every recurring cluster we ACT ON (draft for) gets marked processed at the end,
        # regardless of outcome — otherwise a stable cluster the judge declines (or the
        # gatekeeper rejects as redundant) would be re-drafted and re-judged every cycle.
        # Small/singleton clusters stay unprocessed so they can keep accruing evidence.
        decided_gaps: List[dict] = []

        # Draft a candidate per recurring cluster.
        candidates: List[dict] = []
        for cluster in recurring:
            decided_gaps.extend(cluster)
            queries = [m.get("query_text") or "" for m in cluster]
            try:
                draft = await self.draft_fn(queries[0], queries)
            except Exception as e:
                logger.error(f"[ CAP_GAP ] draft_fn failed: {e}")
                draft = None
            if not draft:
                continue
            draft["cluster_size"] = len(cluster)
            draft["evidence"] = queries[:10]
            candidates.append(draft)

        surfaced = 0
        try:
            if candidates:
                existing_suggestions = await existing_pending_titles(supabase)
                gate = SuggestionGatekeeper(
                    confidence_floor=self.confidence_floor,
                    existing_capabilities=self.capability_snapshot,
                    existing_suggestions=existing_suggestions)
                outcome = gate.filter(candidates)
                logger.info(f"[ CAP_GAP ] Suggestion gate: {outcome.summary}")
                for r in outcome.rejected:
                    logger.info(f"[ CAP_GAP ] rejected {r['title']!r}: {r['reason']}")

                for cand in outcome.admitted:
                    if self.verify_fn:
                        try:
                            if not await self.verify_fn(cand):
                                logger.info(f"[ CAP_GAP ] verify_fn rejected {cand.get('title')!r}")
                                continue
                        except Exception as e:
                            logger.error(f"[ CAP_GAP ] verify_fn errored: {e}")
                            continue
                    if await persist_and_publish(supabase, cand):
                        surfaced += 1
        finally:
            # Mark drafted-for clusters processed even if drafting/gating raised.
            await self._mark_processed(supabase, decided_gaps)

        return surfaced

    async def _mark_processed(self, supabase, cluster: List[dict]) -> None:
        ids = [m.get("id") for m in cluster if m.get("id") is not None]
        if not ids:
            return
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            for gap_id in ids:
                await supabase.async_update(
                    "capability_gap_log", {"id": f"eq.{gap_id}"}, {"processed_at": now})
        except Exception as e:
            logger.error(f"[ CAP_GAP ] Failed to mark gaps processed: {e}")


# ── default wiring (judge + embedder from the live system) ──────────────────────

_DRAFT_SYSTEM = (
    "You are Chad's capability architect. Chad repeatedly searched for a tool he does "
    "not have. Given the cluster of failed capability searches below, decide whether a "
    "single new TOOL (a focused API/action) or SKILL (a multi-step specialized agent) "
    "would genuinely close the gap.\n"
    "Be conservative: if a general-purpose tool (web search, a code sandbox, file ops) "
    "already covers this well enough, return null — do NOT propose redundant capabilities.\n"
    "Respond with ONLY a JSON object: "
    '{"kind": "tool"|"skill", "title": "snake_case_name", "description": "what it does in one sentence", '
    '"rationale": "why it is worth building", "confidence": <float 0..1>}  '
    "or the literal null if no new capability is warranted. No prose, no markdown fences."
)


def _build_judge_draft_fn(model: str) -> DraftFn:
    async def _draft(representative: str, queries: List[str]) -> Optional[dict]:
        from evals.judges import build_judge_adapter, _extract_json_object
        adapter = build_judge_adapter(model)
        joined = "\n".join(f"- {q}" for q in queries[:15])
        messages = [
            {"role": "system", "content": _DRAFT_SYSTEM},
            {"role": "user", "content": f"FAILED CAPABILITY SEARCHES (all returned no tool):\n{joined}"},
        ]
        resp = await adapter.generate(messages=messages, temperature=0.0,
                                      thinking_level="low", max_tokens=1200)
        data = _extract_json_object(resp.text or "")
        if not isinstance(data, dict) or not data.get("title"):
            return None
        return data
    return _draft


def build_default_analyzer() -> Optional[CapabilityGapAnalyzer]:
    """Construct an analyzer wired to the live registry's embedder and a small judge.

    Uses the populated registry captured at log time (CapabilityGapTracker._registry);
    returns None if no gap has been logged yet (e.g. during early boot), so the
    background trigger simply no-ops rather than crashing.
    """
    registry = CapabilityGapTracker._registry
    if registry is None:
        return None

    selector = getattr(registry, "_selector", None)
    embed_fn = (lambda t: selector.embed_text(t)) if selector and hasattr(selector, "embed_text") else None

    # Snapshot the live capability catalog so the gatekeeper can reject redundant ideas.
    capability_snapshot: List[str] = []
    try:
        for schema in registry.get_all_schemas():
            capability_snapshot.append(f"{schema.get('name','')}: {schema.get('description','')}")
    except Exception:
        pass

    import os
    model = os.getenv("CHAD_CAPABILITY_JUDGE_MODEL", "qwen/qwen3.5-9b")
    from src.core.intelligence.capability_verifier import build_capability_verify_fn
    return CapabilityGapAnalyzer(
        embed_fn=embed_fn,
        draft_fn=_build_judge_draft_fn(model),
        capability_snapshot=capability_snapshot,
        verify_fn=build_capability_verify_fn())


# ── surfacing helpers (used by the orchestrator / future GUI) ───────────────────


async def get_pending_suggestions(limit: int = 5) -> List[dict]:
    """Return pending capability suggestions, newest first (for surfacing to the user)."""
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return []
        return await supabase.async_query(
            "capability_suggestions", select="*",
            filters={"status": "eq.pending"}, order="created_at.desc", limit=limit) or []
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to fetch pending suggestions: {e}")
        return []


async def set_suggestion_status(suggestion_id: str, status: str) -> bool:
    """Update a suggestion's lifecycle state (shown / accepted / dismissed)."""
    if status not in {"pending", "shown", "accepted", "dismissed"}:
        return False
    try:
        from datetime import datetime, timezone
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return False
        now = datetime.now(timezone.utc).isoformat()
        await supabase.async_update(
            "capability_suggestions",
            {"id": f"eq.{suggestion_id}"},
            {"status": status, "updated_at": now})
        return True
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to set suggestion status: {e}")
        return False


async def get_suggestion_by_id(suggestion_id: str) -> Optional[dict]:
    """Fetch one suggestion row (the candidate dict propose_skill_write needs)."""
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return None
        rows = await supabase.async_query(
            "capability_suggestions", select="*",
            filters={"id": f"eq.{suggestion_id}"}, limit=1) or []
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to fetch suggestion {suggestion_id}: {e}")
        return None


async def mark_suggestion_built(suggestion_id: str, *, skill_name: str,
                                written_files: List[str]) -> bool:
    """Record a successful commit_skill_write — terminal 'built' state."""
    try:
        from datetime import datetime, timezone
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return False
        now = datetime.now(timezone.utc).isoformat()
        await supabase.async_update(
            "capability_suggestions",
            {"id": f"eq.{suggestion_id}"},
            {"status": "built", "skill_name": skill_name, "written_files": written_files,
             "built_at": now, "updated_at": now})
        return True
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to mark suggestion {suggestion_id} built: {e}")
        return False


async def mark_suggestion_build_failed(suggestion_id: str, *, error: str) -> bool:
    """Record a failed commit_skill_write (invalid token, write error, etc.)."""
    try:
        from datetime import datetime, timezone
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return False
        now = datetime.now(timezone.utc).isoformat()
        await supabase.async_update(
            "capability_suggestions",
            {"id": f"eq.{suggestion_id}"},
            {"status": "build_failed", "updated_at": now})
        logger.error(f"[ CAP_GAP ] Suggestion {suggestion_id} build failed: {error}")
        return True
    except Exception as e:
        logger.error(f"[ CAP_GAP ] Failed to mark suggestion {suggestion_id} build_failed: {e}")
        return False
