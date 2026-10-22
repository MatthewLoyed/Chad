"""
Domain Repository
Data access for domain agents — user-created specializations (Financial, Fitness, School,
Research, a project) that each own a slice of Chad's memory and a persona.

Unlike most repositories here there is **no cloud backend strategy**. A domain row holds the
user's own persona prose and the slug that scopes their personal memories, so it is local-only
by policy (see `docs/guides/PRIVATE_DATA.md`). If that ever changes, note that the domain `id`
is a foreign identity written onto memory metadata and knowledge-graph nodes — it is not a
display string and must never be rewritten by a rename.
"""

import asyncio
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.infrastructure.database import local_store
from src.shared.logger import setup_logger

logger = setup_logger("DomainRepository")

# Reserved because they collide with the two *other* meanings of "category" already in the
# turn pipeline (delivery mode and turn lane), and with the untagged/global tier.
_RESERVED_IDS = {"general", "global", "none", "voice", "contact", "agent", "chat", "planning"}


def slugify_domain(name: str) -> str:
    """Derive a stable, filesystem/JSON-safe domain id from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not slug or slug in _RESERVED_IDS:
        slug = f"{slug or 'domain'}-{uuid.uuid4().hex[:6]}"
    return slug[:48]


def get_domain_persona(domain_id: Optional[str]) -> str:
    """Synchronously fetch a domain's persona prose, or "" if there isn't one.

    Sync (and un-cached) on purpose: it's a single-row primary-key read on a local sqlite
    file, which is noise next to the model call it's being assembled for, and caching it
    would mean invalidating on every persona edit for no measurable gain.
    """
    if not domain_id:
        return ""
    try:
        row = DomainRepository._get_domain(domain_id)
    except Exception as e:
        logger.error("[ DOMAIN ] Failed to load persona for '%s': %s", domain_id, e)
        return ""
    return (row or {}).get("persona") or ""


class DomainRepository:
    """CRUD for domain agents, backed by the local sqlite store."""

    def __init__(self):
        local_store.init_local_db()

    async def list_domains(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._list_domains)

    async def get_domain(self, domain_id: str) -> Optional[Dict[str, Any]]:
        if not domain_id:
            return None
        return await asyncio.to_thread(self._get_domain, domain_id)

    async def create_domain(self, name: str, description: str = "", icon: str = "",
                            color: str = "", persona: str = "") -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._create_domain, name, description, icon, color, persona
        )

    async def update_domain(self, domain_id: str, fields: Dict[str, Any]) -> bool:
        """Update mutable fields. `id` is deliberately not updatable — see module docstring."""
        allowed = {k: v for k, v in (fields or {}).items()
                   if k in {"name", "description", "icon", "color", "persona", "sort_order"}}
        if not allowed:
            return False
        return await asyncio.to_thread(self._update_domain, domain_id, allowed)

    async def delete_domain(self, domain_id: str) -> bool:
        """Delete the domain definition.

        Conversations and memories tagged with this id are intentionally left alone rather
        than cascaded: dropping a domain should not destroy the user's data. They fall back
        to behaving like untagged rows only if the id is reused, so deletion is safe but not
        a way to "un-tag" history.
        """
        return await asyncio.to_thread(self._delete_domain, domain_id)

    # --- sync helpers ---
    @staticmethod
    def _list_domains() -> List[Dict[str, Any]]:
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                "SELECT * FROM domains ORDER BY sort_order ASC, name COLLATE NOCASE ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _get_domain(domain_id: str) -> Optional[Dict[str, Any]]:
        conn = local_store.local_db_connect()
        try:
            row = conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def _create_domain(name, description, icon, color, persona) -> Dict[str, Any]:
        conn = local_store.local_db_connect()
        try:
            domain_id = slugify_domain(name)
            # Slug collision: suffix rather than overwrite someone's existing domain.
            if conn.execute("SELECT 1 FROM domains WHERE id = ?", (domain_id,)).fetchone():
                domain_id = f"{domain_id}-{uuid.uuid4().hex[:6]}"
            now = datetime.now().isoformat()
            next_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM domains"
            ).fetchone()[0]
            row = {
                "id": domain_id, "name": name, "description": description or "",
                "icon": icon or "", "color": color or "", "persona": persona or "",
                "sort_order": next_order, "created_at": now, "updated_at": now,
            }
            conn.execute(
                "INSERT INTO domains (id, name, description, icon, color, persona, "
                "sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (domain_id, row["name"], row["description"], row["icon"], row["color"],
                 row["persona"], next_order, now, now),
            )
            conn.commit()
            logger.info("[ DOMAIN ] Created domain '%s' (id=%s)", name, domain_id)
            return row
        finally:
            conn.close()

    @staticmethod
    def _update_domain(domain_id: str, fields: Dict[str, Any]) -> bool:
        conn = local_store.local_db_connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            params = list(fields.values()) + [datetime.now().isoformat(), domain_id]
            conn.execute(f"UPDATE domains SET {sets}, updated_at = ? WHERE id = ?", params)
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def _delete_domain(domain_id: str) -> bool:
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
            conn.commit()
            return True
        finally:
            conn.close()
