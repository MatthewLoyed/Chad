"""
Generic tool-approval registry.

Backs the human-in-the-loop "requires_approval" primitive: any tool can pause
for an accept/decline or multiple-choice decision by going through this store
instead of hand-rolling its own token mechanism (see propose_file_plan.py /
file_manager.py for the older, tool-specific precedent this generalizes).

Lifecycle for one approval:
    register()  -> pending, no decision yet
    resolve()   -> user answered; payload kept (not popped) so the gate can
                   inspect the decision on the tool's next invocation
    consume()   -> pops the payload once the decision has been acted on
"""

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from src.shared.logger import setup_logger

logger = setup_logger("ApprovalStore")

_pending: Dict[str, Dict[str, Any]] = {}

_DEFAULT_EXPIRES_MINUTES = 10


def mint_token() -> str:
    """Generate a human-readable approval token, e.g. 'CHAD-A7X9'."""
    return f"CHAD-{secrets.token_hex(2).upper()}"


def register(token: str, payload: Dict[str, Any], expires_minutes: int = _DEFAULT_EXPIRES_MINUTES) -> None:
    """Register a freshly-minted approval request awaiting a human decision."""
    _pending[token] = {
        "payload": payload,
        "resolved": False,
        "decision": None,
        "expires_at": datetime.now() + timedelta(minutes=expires_minutes),
        "created_at": datetime.now().isoformat(),
    }
    logger.info(f"[ APPROVAL ] Registered token {token} for tool '{payload.get('tool_name')}'")


def _is_expired(entry: Dict[str, Any]) -> bool:
    return datetime.now() > entry["expires_at"]


def find_pending(tool_name: str, conversation_id: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Find the most recent (resolved or unresolved) approval entry for this
    tool + conversation. Expired entries are dropped and skipped.
    """
    for token, entry in list(_pending.items()):
        if _is_expired(entry):
            _pending.pop(token, None)
            continue
        payload = entry["payload"]
        if payload.get("tool_name") == tool_name and payload.get("conversation_id") == conversation_id:
            return token, entry
    return None


def resolve(token: str, decision: str) -> bool:
    """Mark a pending approval as resolved with the user's decision. Does not pop it."""
    entry = _pending.get(token)
    if not entry:
        logger.warning(f"[ APPROVAL ] resolve() called for unknown token {token}")
        return False
    if _is_expired(entry):
        _pending.pop(token, None)
        logger.warning(f"[ APPROVAL ] resolve() called for expired token {token}")
        return False
    entry["resolved"] = True
    entry["decision"] = decision
    logger.info(f"[ APPROVAL ] Token {token} resolved with decision='{decision}'")
    return True


def consume(token: str) -> Optional[Dict[str, Any]]:
    """Pop and return the stored payload for a token, or None if missing/expired."""
    entry = _pending.pop(token, None)
    if not entry:
        return None
    if _is_expired(entry):
        logger.warning(f"[ APPROVAL ] Token {token} expired before consume")
        return None
    return entry["payload"]


def peek(token: str) -> Optional[Dict[str, Any]]:
    """Return the stored entry without consuming it. Used for inspection/testing."""
    entry = _pending.get(token)
    if not entry or _is_expired(entry):
        return None
    return entry


def has_resolved_decision(conversation_id: str) -> bool:
    """Approval follow-ups must bypass semantic caches and model routing."""
    return any(entry.get("resolved") and not _is_expired(entry)
               and entry["payload"].get("conversation_id") == conversation_id
               for entry in _pending.values())
