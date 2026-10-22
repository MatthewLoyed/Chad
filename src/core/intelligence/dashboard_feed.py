"""
dashboard_feed.py — Chad-authored "my life" dashboard feed (issue #161).

Replaces the static DASHBOARD.md file with structured rows in the
`dashboard_entries` Supabase table. Chad appends entries via the
`update_dashboard` tool (src/tools/system/update_dashboard.py) from any turn —
heartbeat, Gmail webhook investigation, normal chat, research — so the feed
fills itself rather than being hand-edited.

Five kinds:
  - thought   — an autonomous note/observation, no action attached
  - life      — a personal widget (wedding countdown, semester, etc.); usually pinned
  - status    — system/automation health Chad wants to surface
  - discovery — something interesting Chad found and wants to resurface later
  - action    — a proposed next step with a single runnable `action` payload
                ("click here for me to do this for you")

Mirrors the persistence style of capability_gaps.py: thin async helpers around
the shared Supabase manager, no ORM.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.infrastructure.database.local_postgrest import register_table

logger = logging.getLogger("DashboardFeed")

VALID_KINDS = {"thought", "life", "status", "discovery", "action"}
VALID_STATUSES = {"active", "done", "dismissed", "expired"}

# Local (no-SUPABASE_URL) backing table for LocalPostgrestShim (issue #469).
# `id` is left undeclared so the shim defaults it to an autoincrementing
# INTEGER rowid, matching the Supabase UUID column's role (unique row key)
# without needing a UUID generator offline.
register_table(
    "dashboard_entries",
    columns={
        "kind": "TEXT",
        "title": "TEXT",
        "body": "TEXT",
        "metadata": "JSON",
        "action": "JSON",
        "status": "TEXT",
        "pinned": "INTEGER",
        "expires_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    pk="id",
)


async def append_entry(
    kind: str,
    title: str,
    body: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    action: Optional[Dict[str, Any]] = None,
    pinned: bool = False,
    expires_in_hours: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Insert one dashboard entry and publish DashboardEntryCreated."""
    if kind not in VALID_KINDS:
        logger.error(f"[ DASHBOARD ] Rejected entry with invalid kind: {kind!r}")
        return None

    expires_at = None
    if expires_in_hours is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=float(expires_in_hours))).isoformat()

    # status/created_at/updated_at are set explicitly (rather than left to
    # the DB-side DEFAULT 'active'/NOW()) so the row is fully formed offline,
    # where the local shim applies no column defaults; Supabase happily
    # accepts the explicit values.
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "kind": kind,
        "title": title,
        "body": body,
        "metadata": metadata or {},
        "action": action,
        "status": "active",
        "pinned": bool(pinned),
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }

    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        res = await supabase.async_insert("dashboard_entries", row)
        if not res:
            return None
        entry = res[0] if isinstance(res, list) else res
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to insert entry: {e}")
        return None

    try:
        from src.application.events import EventBus, DashboardEntryCreated
        EventBus.get_instance().publish(DashboardEntryCreated(
            entry_id=str(entry.get("id")),
            kind=kind, title=title, body=body,
            metadata=row["metadata"], action=action))
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to publish DashboardEntryCreated: {e}")

    return entry


async def get_feed(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the feed: pinned entries first, then active entries newest-first.

    Lazily flips any entry past `expires_at` to status='expired' so it drops
    out of subsequent reads.
    """
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()

        await _expire_stale(supabase)

        # Both pinned and unpinned use status=active so expired/dismissed/done
        # entries (including expired auto-update pins) leave the feed.
        # `is.true`/`is.false` (rather than `eq.true`/`eq.false`) so this also
        # works offline against the SQLite shim, which stores booleans as
        # plain 0/1 INTEGERs with no native boolean comparison.
        pinned = await supabase.async_query(
            "dashboard_entries", select="*",
            filters={"pinned": "is.true", "status": "eq.active"},
            order="created_at.desc") or []
        rest = await supabase.async_query(
            "dashboard_entries", select="*",
            filters={"pinned": "is.false", "status": "eq.active"},
            order="created_at.desc", limit=limit) or []
        pinned = await _collapse_duplicate_alert_sources(pinned)
        return pinned + rest
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to fetch feed: {e}")
        return []


async def _expire_stale(supabase) -> None:
    # '+00:00' (from isoformat()) is unescaped in supabase_manager's raw URL-concat
    # filter builder, and a literal '+' in a query string parses as a space — use
    # 'Z' instead so the PostgREST `lt.` comparison gets a parseable timestamp.
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        await supabase.async_update(
            "dashboard_entries",
            {"status": "eq.active", "expires_at": f"lt.{now}"},
            {"status": "expired"})
    except Exception as e:
        logger.debug(f"[ DASHBOARD ] Expiry sweep failed (non-fatal): {e}")


# System alerts that should never stack identical pinned cards (one live card max).
_COLLAPSE_SOURCES = frozenset({"auto_update"})


async def _collapse_duplicate_alert_sources(
    entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep the newest entry per collapsible metadata.source; dismiss older dupes."""
    seen: set[str] = set()
    kept: List[Dict[str, Any]] = []
    for entry in entries:
        source = _entry_metadata(entry).get("source")
        if not isinstance(source, str) or source not in _COLLAPSE_SOURCES:
            kept.append(entry)
            continue
        if source in seen:
            entry_id = entry.get("id")
            if entry_id is not None:
                await set_entry_status(str(entry_id), "dismissed")
            continue
        seen.add(source)
        kept.append(entry)
    return kept


def _entry_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
    meta = entry.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json
            parsed = json.loads(meta)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return meta if isinstance(meta, dict) else {}


async def dismiss_by_metadata_source(source: str) -> int:
    """Dismiss active entries whose metadata.source matches (e.g. auto_update spam).

    Returns the number of entries dismissed. Used to keep repeated system alerts
    from stacking identical pinned cards on every poll.
    """
    if not source:
        return 0
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        await _expire_stale(supabase)
        rows = await supabase.async_query(
            "dashboard_entries", select="*",
            filters={"status": "eq.active"},
            order="created_at.desc", limit=200) or []
        dismissed = 0
        for entry in rows:
            if _entry_metadata(entry).get("source") != source:
                continue
            entry_id = entry.get("id")
            if entry_id is None:
                continue
            if await set_entry_status(str(entry_id), "dismissed"):
                dismissed += 1
        return dismissed
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to dismiss by source {source!r}: {e}")
        return 0


async def set_entry_status(entry_id: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        now = datetime.now(timezone.utc).isoformat()
        await supabase.async_update(
            "dashboard_entries",
            {"id": f"eq.{entry_id}"},
            {"status": status, "updated_at": now})
        return True
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to set entry {entry_id} status: {e}")
        return False


async def get_entry_by_id(entry_id: str) -> Optional[Dict[str, Any]]:
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        rows = await supabase.async_query(
            "dashboard_entries", select="*",
            filters={"id": f"eq.{entry_id}"}, limit=1) or []
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Failed to fetch entry {entry_id}: {e}")
        return None


async def run_entry_action(entry_id: str, tool_registry) -> Dict[str, Any]:
    """Execute the single runnable step attached to an 'action' entry, then mark it done.

    `action` shape: {"type": "tool"|"fast_track", "target": str, "params": dict, "label": str}
    Multi-step workflow actions are intentionally not supported here (v1 keeps
    action cards single-step; see docs/architecture for the workflow-run path
    used elsewhere for saved multi-step workflows).
    """
    entry = await get_entry_by_id(entry_id)
    if not entry:
        return {"success": False, "error": f"Dashboard entry {entry_id} not found"}

    action = entry.get("action")
    if not action or not isinstance(action, dict):
        return {"success": False, "error": "Entry has no runnable action"}

    if not tool_registry:
        return {"success": False, "error": "Tool registry unavailable"}

    action_type = action.get("type")
    target = action.get("target")
    params = action.get("params") or {}

    try:
        if action_type == "fast_track":
            command = tool_registry.resolve_fast_track(target)
            if not command:
                return {"success": False, "error": f"Unknown fast-track alias: {target}"}
            result = await tool_registry.execute_tool(command["tool"], command["params"])
        elif action_type == "tool":
            result = await tool_registry.execute_tool(target, params)
        else:
            return {"success": False, "error": f"Unsupported action type: {action_type}"}
    except Exception as e:
        logger.error(f"[ DASHBOARD ] Action for entry {entry_id} failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

    await set_entry_status(entry_id, "done")
    return {"success": True, "result": result}
