"""
Read-only snapshot of background daemons for the Automations UI.

Merges config (what should run) with runtime state (what is running) and the
autonomous run log (when things last fired).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from src.core.monitoring.autonomous_run_log import (
    SOURCE_CRON,
    SOURCE_HEARTBEAT,
    SOURCE_INGESTION,
    SOURCE_REFLECTION,
    SOURCE_WEBHOOK,
    last_run_at,
)
from src.core.intelligence.ingestion.sync_manager import SYNC_STATE_KEY
from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.config_manager import ConfigManager


def daemons_master_enabled() -> bool:
    """Overall daemons kill switch: CHAD_DAEMONS_ENABLED env var (if set) takes
    precedence over `daemons.enabled` in config/preferences.yaml — same precedence
    rule used at boot in remote_factory.py/system_factory.py."""
    env = os.getenv("CHAD_DAEMONS_ENABLED", "").lower()
    if env != "":
        return env == "true"
    return bool(ConfigManager.get("daemons.enabled", True))


def daemon_enabled(key: str, default: bool = True) -> bool:
    """Live per-daemon enabled flag: `daemons.<key>` AND the overall master switch.

    Reads straight from ConfigManager/env on every call (no caching), so callers —
    including the running HeartbeatService/CronScheduler loops — can re-check this
    each tick and pick up Settings → Automations GUI toggles without a restart.
    """
    return bool(ConfigManager.get(f"daemons.{key}", default)) and daemons_master_enabled()


def get_daemon_snapshot(facade: Any = None) -> List[Dict[str, Any]]:
    """Return all known background daemons for the Automations panel."""
    heartbeat_service = getattr(facade, "heartbeat_service", None) if facade else None
    cron_scheduler = getattr(facade, "cron_scheduler", None) if facade else None

    hb_interval = int(ConfigManager.get("daemons.heartbeat_interval_minutes", 30) or 30)
    sync_interval = int(ConfigManager.get("daemons.ingestion_sync_interval_minutes", 60) or 60)
    reflection_hours = float(ConfigManager.get("reflection.interval_hours", 24) or 24)
    auto_update = bool(ConfigManager.get("updates.auto_update", False))
    auto_update_interval = int(ConfigManager.get("updates.auto_update_interval_minutes", 60) or 60)

    sync_states = StateRepository().get_value(SYNC_STATE_KEY, default={}) or {}
    ingestion_sources = {
        k: v for k, v in sync_states.items()
        if isinstance(v, str)
    }

    hb_runtime = heartbeat_service.get_status() if heartbeat_service else None
    heartbeat_online = bool(hb_runtime and hb_runtime.get("status") == "online")

    daemons: List[Dict[str, Any]] = [
        {
            "id": "heartbeat",
            "label": "Heartbeat",
            "kind": "llm",
            "description": "Runs the checklist on a timer; notifies when something needs attention.",
            "enabled": daemon_enabled("heartbeat"),
            "running": heartbeat_online,
            "interval_minutes": hb_interval,
            "last_run": last_run_at(SOURCE_HEARTBEAT) or (hb_runtime or {}).get("last_check"),
            "config_keys": ["daemons.heartbeat", "daemons.heartbeat_interval_minutes"],
        },
        {
            "id": "cron",
            "label": "Scheduled tasks",
            "kind": "llm",
            "description": "User-defined cron / interval / one-shot prompts.",
            "enabled": daemon_enabled("cron"),
            # cron_scheduler.get_status() is async; mirror what its loop itself checks
            # each tick (live daemon_enabled) rather than await-ing here.
            "running": cron_scheduler is not None and daemon_enabled("cron"),
            "interval_minutes": None,
            "last_run": last_run_at(SOURCE_CRON),
            "config_keys": ["daemons.cron"],
        },
        {
            "id": "ingestion_sync",
            "label": "Personal index sync",
            "kind": "sync",
            "description": "Chrome history, Gmail, and Drive → searchable personal_index (no LLM).",
            "enabled": daemon_enabled("ingestion_sync", default=False),
            "running": daemon_enabled("ingestion_sync", default=False),
            "interval_minutes": sync_interval,
            "last_run": last_run_at(SOURCE_INGESTION),
            "metadata": {"sources": ingestion_sources},
            "config_keys": ["daemons.ingestion_sync", "daemons.ingestion_sync_interval_minutes"],
        },
        {
            "id": "gmail_webhook",
            "label": "Gmail webhook",
            "kind": "webhook",
            "description": "Wakes Chad when new mail arrives (may trigger an LLM turn).",
            "enabled": daemon_enabled("gmail_webhook"),
            "running": daemon_enabled("gmail_webhook"),
            "interval_minutes": None,
            "last_run": last_run_at(SOURCE_WEBHOOK),
            "config_keys": ["daemons.gmail_webhook"],
        },
        {
            "id": "reflection",
            "label": "Reflection",
            "kind": "llm",
            "description": "Propose-only self-learning cycle (never writes persona without you).",
            "enabled": bool(ConfigManager.get("reflection.enabled", False)) and daemons_master_enabled(),
            "running": bool(ConfigManager.get("reflection.enabled", False)) and daemons_master_enabled(),
            "interval_minutes": int(reflection_hours),
            "interval_unit": "hours",
            "last_run": last_run_at(SOURCE_REFLECTION),
            "config_keys": ["reflection.enabled", "reflection.interval_hours"],
        },
        {
            "id": "auto_update",
            "label": "Auto-update",
            "kind": "system",
            "description": "Polls for Brain updates and applies them (Settings → System).",
            "enabled": auto_update,
            "running": auto_update,
            "interval_minutes": auto_update_interval,
            "last_run": None,
            "config_keys": ["updates.auto_update", "updates.auto_update_interval_minutes"],
        },
    ]
    return daemons
