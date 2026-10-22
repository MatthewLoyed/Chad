"""Queue artifacts produced during a Telegram turn for Bot API delivery."""

from __future__ import annotations

from typing import Dict, List, Optional

from src.core.orchestration.context import session_id_var

_pending: Dict[str, List[Dict[str, str]]] = {}


def _is_telegram_session(session_id: Optional[str]) -> bool:
    return bool(session_id and ":telegram:" in session_id)


def note_artifact_for_telegram(
    path: str,
    filename: str,
    *,
    session_id: Optional[str] = None,
) -> None:
    """Record a file to send when the Telegram turn completes."""
    sid = session_id or session_id_var.get()
    if not _is_telegram_session(sid) or not path or not filename:
        return
    bucket = _pending.setdefault(str(sid), [])
    entry = {"path": path, "filename": filename}
    if entry not in bucket:
        bucket.append(entry)


def take_pending_artifacts(session_id: Optional[str]) -> List[Dict[str, str]]:
    """Pop and return artifacts queued for this Telegram session."""
    if not session_id:
        return []
    return _pending.pop(str(session_id), [])
