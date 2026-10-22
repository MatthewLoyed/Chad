"""
reflection_lessons_store.py — review/approve queue for Reflective Lessons (#194).

`reflection.propose_lessons()` produces a `LessonProposal` describing admitted lessons
and open questions but writes nothing. This module is the persistence + per-item apply
layer that lets each lesson/question be reviewed individually from the GUI instead of
only through the CLI `--write` gate, mirroring `capability_gaps.py`'s
`capability_suggestions` queue (table -> `*Raised` event -> GUI side panel).

Each row is independently actionable:
  - lesson rows:   approve -> apply_lesson() writes it (persona record or knowledge
                   graph) and the row flips to 'applied'; dismiss just flips status.
  - question rows: answered via answer_question(), which ingests `question -> answer`
                   into the knowledge graph (questions are about the user) and marks
                   the row 'answered'.

Heartbeat auto-write (reflection.auto_write_confidence_floor) calls apply_lesson() up
front and persists the row as already 'auto_applied' — still visible/dismissible in the
GUI, just without an Approve action since the write already happened.

Local-first (#467): the table is registered with `LocalPostgrestShim` so the same
`supabase.async_*` calls below persist to `data/local_chad.db` when `SUPABASE_URL`
isn't configured — mirrors `012_create_reflection_lessons.sql` minus the UUID default
(the local pk is an autoincrementing rowid instead).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from src.infrastructure.database.local_postgrest import register_table

logger = logging.getLogger("ReflectionLessons")

# Statuses that block re-proposal (dismissed intentionally excluded — user may re-surface).
_QUEUE_DEDUP_STATUSES = ("pending", "shown", "applied", "auto_applied")

def _register_local_table() -> None:
    """(Re-)declare the local backing table on the shared shim. Idempotent."""
    register_table(
        "reflection_lessons",
        columns={
            "id": "INTEGER",
            "kind": "TEXT",
            "scope": "TEXT",
            "text": "TEXT",
            "why": "TEXT",
            "evidence": "TEXT",
            "confidence": "REAL",
            "status": "TEXT",
            "answer": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        pk="id",
    )


_register_local_table()


def _norm_tokens(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate_lesson(text: str, existing: Iterable[str], *, threshold: float = 0.6) -> bool:
    """True when `text` is a near-duplicate of any string in `existing` (Jaccard on tokens)."""
    tok = _norm_tokens(text)
    if not tok:
        return False
    return max((_jaccard(tok, _norm_tokens(e)) for e in existing), default=0.0) >= threshold


async def existing_pending_lesson_texts(supabase, *, include_applied: bool = True) -> List[str]:
    """Texts from reflection_lessons rows that should block near-duplicate proposals.

    Mirrors capability_gaps.existing_pending_titles() — pending/shown stop GUI dupes;
    applied/auto_applied stop cross-cycle re-proposal of facts already filed.
    """
    statuses = list(_QUEUE_DEDUP_STATUSES if include_applied else ("pending", "shown"))
    try:
        rows = await supabase.async_query(
            "reflection_lessons", select="text",
            filters={"status": f"in.({','.join(statuses)})"},
            order="created_at.desc", limit=200) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("[ REFLECTION ] existing_pending_lesson_texts failed: %s", e)
        return []
    seen: set[str] = set()
    out: List[str] = []
    for r in rows:
        t = (r.get("text") or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def apply_lesson(item: Dict[str, Any]) -> bool:
    """Write ONE admitted lesson (or sleep-cognify item) to its destination store.

    `item` needs `scope` and `text` (or `lesson`, the judge's raw key). Reuses
    reflection.py's merge/scope helpers so there is one source of truth for how a
    lesson line is rendered and routed.

    Rows with `kind == 'sleep_cognify'` (#611 — typed edges / observations / core
    profile rewrites, queued because they're Tier-1 sensitive or an always-on rewrite)
    are dispatched to sleep_cognify.apply_item() instead — same review queue, GUI, and
    approve/dismiss flow as reflective lessons, different destination.
    """
    if item.get("kind") == "sleep_cognify":
        from src.core.intelligence.sleep_cognify import apply_item
        return await apply_item(item)

    from src.core.intelligence.reflection import (
        GRAPH_SCOPES, PERSONA_SCOPES, merge_lessons_into_content,
    )

    scope = item.get("scope")
    text = (item.get("text") or item.get("lesson") or "").strip()
    if not text:
        return False

    if scope in GRAPH_SCOPES:
        from src.core.intelligence.graph_rag_client import GraphRAGClient
        graph = GraphRAGClient()
        await graph.initialize()
        try:
            return bool(await graph.ingest(text, source="reflection"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"apply_lesson graph ingest failed: {e}")
            return False

    from src.infrastructure.repositories.memory_repository import MemoryRepository
    repo = MemoryRepository()
    pid = PERSONA_SCOPES.get(scope, "tools")
    current = repo.get_persona_config(pid) or ""
    proposed = merge_lessons_into_content(
        current, [{"lesson": text, "confidence": item.get("confidence", 0.0)}])
    try:
        return bool(repo.update_persona_config(pid, proposed))
    except Exception as e:  # noqa: BLE001
        logger.error(f"apply_lesson persona write failed for {pid}: {e}")
        return False


async def persist_lesson_and_publish(supabase, item: Dict[str, Any], kind: str, *,
                                     auto_apply: bool = False) -> bool:
    """Insert one reflection_lessons row and publish ReflectionLessonRaised.

    `kind` is 'lesson' or 'question'. When `auto_apply` is True (heartbeat
    auto-write path), the lesson is written to its destination FIRST and the row is
    persisted already as 'auto_applied' rather than 'pending'.
    """
    text = (item.get("text") or item.get("lesson") or item.get("question") or "").strip()
    if not text:
        logger.warning("[ REFLECTION ] Skipping persist — empty %s text", kind)
        return False

    try:
        existing = await existing_pending_lesson_texts(supabase)
        if is_duplicate_lesson(text, existing):
            logger.info(
                "[ REFLECTION ] Skipping duplicate %s (near-match in review queue): %r",
                kind, text[:80])
            return False
    except Exception as e:  # noqa: BLE001
        logger.warning("[ REFLECTION ] Queue dedup check failed (proceeding): %s", e)

    status = "pending"
    if kind == "lesson" and auto_apply:
        status = "auto_applied" if await apply_lesson(item) else "pending"

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "kind": kind,
        "scope": item.get("scope") or "",
        "text": text,
        "why": item.get("why", ""),
        "evidence": str(item.get("evidence", "")),
        "confidence": float(item.get("confidence", 0.0) or 0.0),
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    res = await supabase.async_insert("reflection_lessons", row)
    if not res:
        return False
    logger.info(f"[ REFLECTION ] Surfaced {kind}: {row['text'][:80]!r} (status={status})")

    try:
        from src.application.events import EventBus, ReflectionLessonRaised
        lid = res[0].get("id") if isinstance(res, list) and res else None
        EventBus.get_instance().publish(ReflectionLessonRaised(
            lesson_id=str(lid) if lid else "",
            kind=row["kind"], scope=row["scope"], text=row["text"], why=row["why"],
            evidence=row["evidence"], confidence=row["confidence"], status=row["status"]))
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to publish ReflectionLessonRaised: {e}")
    return True


async def get_pending_lessons(limit: int = 20) -> List[dict]:
    """Rows awaiting user review (pending only — auto_applied live in Memory)."""
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase:
            return []
        return await supabase.async_query(
            "reflection_lessons", select="*",
            filters={"status": "eq.pending"},
            order="created_at.desc", limit=limit) or []
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to fetch pending lessons: {e}")
        return []


_APPROVED_STATUSES = ("applied", "auto_applied", "answered")


async def count_approved_lessons() -> int:
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase:
            return 0
        rows = await supabase.async_query(
            "reflection_lessons", select="id",
            filters={"status": f"in.({','.join(_APPROVED_STATUSES)})"},
            limit=1000) or []
        return len(rows)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to count approved lessons: {e}")
        return 0


async def get_approved_lessons(limit: int = 25, offset: int = 0) -> List[dict]:
    """Applied / auto-applied / answered rows for the Memory UI audit trail."""
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase:
            return []
        fetch = max(limit + offset, limit)
        rows = await supabase.async_query(
            "reflection_lessons", select="*",
            filters={"status": f"in.({','.join(_APPROVED_STATUSES)})"},
            order="updated_at.desc", limit=fetch) or []
        return rows[offset: offset + limit]
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to fetch approved lessons: {e}")
        return []


def lesson_row_to_memory_item(row: dict) -> dict:
    """Shape a reflection_lessons row for Settings → Memory preview lists."""
    status = row.get("status") or ""
    scope = row.get("scope") or ""
    kind = row.get("kind") or "lesson"
    status_label = {
        "applied": "approved",
        "auto_applied": "auto-applied",
        "answered": "answered",
    }.get(status, status)
    sleep_cognify_labels = {
        "graph_edge": "knowledge graph (edge)",
        "observation": "knowledge graph (observation)",
        "core_profile": "core profile rewrite",
    }
    if kind == "sleep_cognify" and scope in sleep_cognify_labels:
        scope_label = sleep_cognify_labels[scope]
    else:
        scope_label = "knowledge graph" if scope == "user" else ("persona" if scope == "tools" else scope)
    return {
        "id": str(row.get("id", "")),
        "content": (row.get("text") or "").strip(),
        "type": kind,
        "timestamp": row.get("updated_at") or row.get("created_at"),
        "source": f"{scope_label} · {status_label}" if scope_label else status_label,
    }


async def get_lesson_by_id(lesson_id: str) -> Optional[dict]:
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase:
            return None
        rows = await supabase.async_query(
            "reflection_lessons", select="*",
            filters={"id": f"eq.{lesson_id}"}, limit=1) or []
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to fetch lesson {lesson_id}: {e}")
        return None


async def set_lesson_status(lesson_id: str, status: str) -> bool:
    if status not in {"pending", "shown", "applied", "auto_applied", "dismissed", "answered"}:
        return False
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase:
            return False
        now = datetime.now(timezone.utc).isoformat()
        await supabase.async_update(
            "reflection_lessons", {"id": f"eq.{lesson_id}"},
            {"status": status, "updated_at": now})
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to set status for {lesson_id}: {e}")
        return False


async def answer_question(lesson_id: str, answer: str) -> bool:
    """Turn an answered open question into a knowledge-graph fact.

    Composes "<question> -> <answer>" and ingests it (questions are scoped 'user' by
    construction — they're asked precisely because Chad can't resolve a fact about the
    user from evidence alone).
    """
    row = await get_lesson_by_id(lesson_id)
    if not row or row.get("kind") != "question":
        return False
    answer = (answer or "").strip()
    if not answer:
        return False

    fact = f"{row.get('text', '')} -> {answer}"
    try:
        from src.core.intelligence.graph_rag_client import GraphRAGClient
        graph = GraphRAGClient()
        await graph.initialize()
        ok = bool(await graph.ingest(fact, source="reflection"))
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] answer_question graph ingest failed: {e}")
        ok = False
    if not ok:
        return False

    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if supabase:
            now = datetime.now(timezone.utc).isoformat()
            await supabase.async_update(
                "reflection_lessons", {"id": f"eq.{lesson_id}"},
                {"status": "answered", "answer": answer, "scope": "user", "updated_at": now})
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ REFLECTION ] Failed to mark question {lesson_id} answered: {e}")
    return True
