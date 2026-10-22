"""
Persistent ring-buffer log of autonomous background activity.

Covers LLM turns (heartbeat, cron, webhooks) and tool-only daemons (ingestion sync,
reflection). Stored in Supabase system_state so the Automations UI can show a
durable history — not just ephemeral toasts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.infrastructure.repositories.state_repository import StateRepository

logger = logging.getLogger("AutonomousRunLog")

RUN_LOG_KEY = "autonomous_run_log"
MAX_RUNS = 200

# Normalized source ids used across the UI.
SOURCE_HEARTBEAT = "heartbeat"
SOURCE_CRON = "cron"
SOURCE_WEBHOOK = "webhook"
SOURCE_SKILL = "skill"
SOURCE_INGESTION = "ingestion_sync"
SOURCE_REFLECTION = "reflection"
SOURCE_SLEEP_COGNIFY = "sleep_cognify"
SOURCE_SYSTEM = "system"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_run(
    source: str,
    *,
    outcome: str = "ok",
    summary: str = "",
    tools: Optional[List[str]] = None,
    duration_sec: Optional[float] = None,
    silent: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Append one autonomous run entry. Returns the entry or None on failure."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "source": source,
        "outcome": outcome,
        "summary": (summary or "")[:2000],
        "tools": tools or [],
        "duration_sec": round(duration_sec, 2) if duration_sec is not None else None,
        "silent": bool(silent),
        "metadata": metadata or {},
        "ran_at": _now_iso(),
    }
    try:
        repo = StateRepository()
        runs: List[Dict[str, Any]] = repo.get_value(RUN_LOG_KEY, default=[]) or []
        runs.insert(0, entry)
        runs = runs[:MAX_RUNS]
        repo.set_value(RUN_LOG_KEY, runs)
        return entry
    except Exception as e:
        logger.warning(f"[ RUN LOG ] Failed to record {source} run: {e}")
        return None


def list_runs(limit: int = 50, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest-first autonomous run history."""
    try:
        runs: List[Dict[str, Any]] = StateRepository().get_value(RUN_LOG_KEY, default=[]) or []
        if source:
            runs = [r for r in runs if r.get("source") == source]
        return runs[: max(1, min(limit, MAX_RUNS))]
    except Exception as e:
        logger.warning(f"[ RUN LOG ] Failed to list runs: {e}")
        return []


def last_run_at(source: str) -> Optional[str]:
    """ISO timestamp of the most recent run for a source, if any."""
    for run in list_runs(limit=MAX_RUNS, source=source):
        return run.get("ran_at")
    return None


def resolve_llm_source(initiator: str, user_input: Optional[str], category: str) -> str:
    """Map a finalized orchestrator turn to a run-log source id."""
    if initiator == "webhook":
        return SOURCE_WEBHOOK
    if initiator == "skill":
        return SOURCE_SKILL
    if initiator == "system":
        text = (user_input or "")
        if "BACKGROUND HEARTBEAT CYCLE" in text:
            return SOURCE_HEARTBEAT
        if category == "autonomous":
            return SOURCE_CRON
        return SOURCE_SYSTEM
    return SOURCE_SYSTEM


def record_llm_turn(
    *,
    initiator: str,
    category: str,
    user_input: Optional[str],
    model_response: Optional[str],
    tools_executed: Optional[List[str]],
    duration_sec: float,
) -> None:
    """Record any non-user orchestrator turn (heartbeat, cron, webhook, skill)."""
    if initiator in ("user", "followup"):
        return

    source = resolve_llm_source(initiator, user_input, category)
    response = (model_response or "").strip()
    is_ok = response.upper() == "HEARTBEAT_OK" or not response
    silent = is_ok and not (tools_executed or [])

    if silent:
        summary = "All clear (no action needed)."
        outcome = "ok"
    elif is_ok and tools_executed:
        summary = f"Ran {len(tools_executed)} tool(s)."
        outcome = "action"
    else:
        summary = response[:500] if response else "Completed."
        outcome = "action" if tools_executed else "ok"

    record_run(
        source,
        outcome=outcome,
        summary=summary,
        tools=list(tools_executed or []),
        duration_sec=duration_sec,
        silent=silent,
        metadata={"category": category, "initiator": initiator},
    )
