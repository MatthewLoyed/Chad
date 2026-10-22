"""Storage backends for :class:`ChatHistoryRepository`.

The local/cloud decision is made **once** (in ``ChatHistoryRepository.__init__``, by picking
one of these) instead of an ``if not self.supabase.enabled:`` branch at the top of every
method. Each backend owns one storage dialect end-to-end:

- :class:`SupabaseChatHistoryBackend` — PostgREST (cloud), async.
- :class:`LocalChatHistoryBackend` — zero-config sqlite store (issue #416); its sync sqlite
  helpers run off the event loop via ``asyncio.to_thread`` (sqlite3 connections aren't
  shareable across threads, so each opens/closes its own short-lived connection).

Callers get identical semantics either way. Input normalization that is backend-independent
(UUID extraction, base64 stripping, title derivation) stays on the repository, above the
backend, so it isn't duplicated here.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger
from src.infrastructure.database import local_store

logger = setup_logger("ChatHistoryRepository")

_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
# FTS5 MATCH syntax chars to strip before tokenizing. Do NOT strip ':' — it appears in
# URLs and quoting each token keeps column-filter syntax from firing on user input.
_FTS_SPECIAL_RE = re.compile(r'["\'\*\(\)\{\}\[\]\^~\\]')
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:@#-]+")
_SNIPPET_RADIUS = 80


def _content_as_text(content: Any) -> str:
    """Flatten stored message content (str or multimodal JSON list) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(p for p in parts if p)
    return str(content)


def make_snippet(content: Any, query: str, radius: int = _SNIPPET_RADIUS) -> str:
    """~160-char window centered on the first case-insensitive match of ``query``."""
    text = _content_as_text(content)
    if not text:
        return ""
    q = (query or "").strip()
    if not q:
        return text[:160] + ("…" if len(text) > 160 else "")
    lower = text.lower()
    idx = lower.find(q.lower())
    if idx < 0:
        # Try first alphanumeric token when the full query (e.g. a URL) isn't contiguous.
        token = next((t for t in _FTS_TOKEN_RE.findall(q) if len(t) >= 3), None)
        idx = lower.find(token.lower()) if token else -1
    if idx < 0:
        return text[:160] + ("…" if len(text) > 160 else "")
    start = max(0, idx - radius)
    end = min(len(text), idx + max(len(q), 1) + radius)
    snip = text[start:end]
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    return snip


def _fts_match_query(query: str) -> Optional[str]:
    """Build an FTS5 MATCH expression from a user query, or None if nothing usable remains."""
    raw = (query or "").strip()
    if not raw:
        return None
    # Prefer phrase match when the cleaned query is still a contiguous phrase.
    cleaned = _FTS_SPECIAL_RE.sub(" ", raw).strip()
    tokens = _FTS_TOKEN_RE.findall(cleaned)
    if not tokens:
        return None
    # AND tokens so multi-word queries stay precise; quote each to allow /:@.- in URLs.
    return " AND ".join(f'"{t}"' for t in tokens)


class ChatHistoryBackend(ABC):
    """Storage-dialect strategy for conversations + messages. All methods async."""

    conv_table = "conversations"
    msg_table = "messages"

    @abstractmethod
    async def get_conversations(self, limit: int, offset: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_conversation(self, cleaned_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def get_messages(self, cleaned_id: str, include_archived: bool) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def archive_messages(self, message_ids: List[str]) -> bool: ...

    @abstractmethod
    async def update_message_metadata(self, cleaned_id: str, updates: Dict[str, Any]) -> bool: ...

    @abstractmethod
    async def create_conversation(self, model: Optional[str], title: str,
                                  domain: Optional[str] = None) -> Dict[str, Any]: ...

    @abstractmethod
    async def append_message(self, cleaned_id: str, role: str, processed_content: str,
                             metadata: Optional[Dict[str, Any]], message_id: Optional[str]) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_conversation_title(self, cleaned_id: str, title: str) -> bool: ...

    @abstractmethod
    async def update_conversation_pinned(self, cleaned_id: str, is_pinned: bool) -> bool: ...

    @abstractmethod
    async def update_conversation_project(self, cleaned_id: str, project_path: Optional[str]) -> bool: ...

    @abstractmethod
    async def update_conversation_domain(self, cleaned_id: str, domain: Optional[str]) -> bool: ...

    @abstractmethod
    async def reassign_project_path(self, old_path: str, new_path: str) -> bool: ...

    @abstractmethod
    async def delete_conversation(self, cleaned_id: str) -> bool: ...

    @abstractmethod
    async def search_chat_messages(self, query: str, limit: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def search_conversations(self, query: str, limit: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def truncate_conversation_after(self, cleaned_id: str, keep_count: Optional[int] = None) -> bool: ...


class SupabaseChatHistoryBackend(ChatHistoryBackend):
    """PostgREST storage (cloud)."""

    def __init__(self, supabase):
        self.supabase = supabase

    async def get_conversations(self, limit, offset) -> List[Dict[str, Any]]:
        return await self.supabase.async_query(
            self.conv_table, limit=limit, offset=offset, order="is_pinned.desc,updated_at.desc"
        )

    async def get_conversation(self, cleaned_id) -> Optional[Dict[str, Any]]:
        res = await self.supabase.async_query(self.conv_table, filters={"id": f"eq.{cleaned_id}"})
        if res and len(res) > 0:
            return res[0]
        return None

    async def get_messages(self, cleaned_id, include_archived) -> List[Dict[str, Any]]:
        # If the ID isn't a valid UUID, don't query (Supabase requires UUID for this column)
        if not cleaned_id or not re.match(_UUID_RE, cleaned_id):
            return []
        filters = {"conversation_id": f"eq.{cleaned_id}"}
        if not include_archived:
            filters["or"] = "(is_archived.eq.false,is_archived.is.null)"
        res = await self.supabase.async_query(self.msg_table, filters=filters, order="created_at.asc")
        for msg in res:
            _maybe_parse_json_content(msg)
        return res

    async def archive_messages(self, message_ids) -> bool:
        # PostgREST syntax for matching multiple IDs: id=in.(uuid1,uuid2,...)
        ids_str = ",".join(message_ids)
        filters = {"id": f"in.({ids_str})"}
        data = {"is_archived": True, "compacted_at": datetime.now().isoformat()}
        try:
            return await self.supabase.async_update(self.msg_table, filters=filters, data=data)
        except Exception as e:
            logger.error(f"Failed to archive messages: {e}")
            return False

    async def update_message_metadata(self, cleaned_id, updates) -> bool:
        res = await self.supabase.async_query(self.msg_table, select="metadata", filters={"id": f"eq.{cleaned_id}"})
        if not res:
            return False
        metadata = res[0].get("metadata") or {}
        metadata.update(updates)
        return await self.supabase.async_update(
            self.msg_table, filters={"id": f"eq.{cleaned_id}"}, data={"metadata": metadata}
        )

    async def create_conversation(self, model, title, domain=None) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        data = {"title": title, "model": model, "created_at": now, "updated_at": now}
        if domain:
            data["domain"] = domain
        res = await self.supabase.async_insert(self.conv_table, data)
        if not res:
            logger.error("Failed to create conversation in Supabase")
            return {}
        return res

    async def append_message(self, cleaned_id, role, processed_content, metadata, message_id) -> Dict[str, Any]:
        data = {
            "conversation_id": cleaned_id,
            "role": role,
            "content": processed_content,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
        }
        if message_id:
            data["id"] = message_id
        res = await self.supabase.async_insert(self.msg_table, data)
        if not res:
            logger.error(f"Failed to append message to conversation {cleaned_id}")
            return {}
        # updated_at is bumped by a DB trigger; return the inserted message
        return res

    async def update_conversation_title(self, cleaned_id, title) -> bool:
        return await self.supabase.async_update(
            self.conv_table, filters={"id": f"eq.{cleaned_id}"}, data={"title": title}
        )

    async def update_conversation_pinned(self, cleaned_id, is_pinned) -> bool:
        return await self.supabase.async_update(
            self.conv_table, filters={"id": f"eq.{cleaned_id}"}, data={"is_pinned": is_pinned}
        )

    async def update_conversation_project(self, cleaned_id, project_path) -> bool:
        return await self.supabase.async_update(
            self.conv_table, filters={"id": f"eq.{cleaned_id}"}, data={"project_path": project_path}
        )

    async def update_conversation_domain(self, cleaned_id, domain) -> bool:
        return await self.supabase.async_update(
            self.conv_table, filters={"id": f"eq.{cleaned_id}"}, data={"domain": domain}
        )

    async def reassign_project_path(self, old_path, new_path) -> bool:
        return await self.supabase.async_update(
            self.conv_table, filters={"project_path": f"eq.{old_path}"}, data={"project_path": new_path}
        )

    async def delete_conversation(self, cleaned_id) -> bool:
        # Cascade delete handles messages automatically.
        return await self.supabase.async_delete(self.conv_table, filters={"id": f"eq.{cleaned_id}"})

    async def search_chat_messages(self, query, limit) -> List[Dict[str, Any]]:
        # ilike for case-insensitive partial match; join conversations to get the title.
        # Local-first path uses FTS5; Supabase keeps ilike (parity gap documented in MEMORY_AND_CONTEXT).
        filters = {"content": f"ilike.*{query}*"}
        res = await self.supabase.async_query(
            self.msg_table, select="*,conversations(title)", filters=filters, limit=limit
        )
        for msg in res:
            conv = msg.get("conversations")
            if conv and isinstance(conv, dict):
                msg["conversation_title"] = conv.get("title", "Untitled Chat")
            elif conv and isinstance(conv, list) and len(conv) > 0:
                msg["conversation_title"] = conv[0].get("title", "Untitled Chat")
            else:
                msg["conversation_title"] = "Untitled Chat"
            _maybe_parse_json_content(msg)
            msg["snippet"] = make_snippet(msg.get("content"), query)
            msg["rank"] = None
        return res

    async def search_conversations(self, query, limit) -> List[Dict[str, Any]]:
        filters = {"title": f"ilike.*{query}*"}
        return await self.supabase.async_query(self.conv_table, filters=filters, limit=limit)

    async def truncate_conversation_after(self, cleaned_id: str, keep_count: Optional[int] = None) -> bool:
        if not cleaned_id or not re.match(_UUID_RE, cleaned_id):
            return False
        messages = await self.get_messages(cleaned_id, include_archived=False)
        if not messages:
            return True
        start_idx = max(0, keep_count) if keep_count is not None else 0
        to_archive = messages[start_idx:]
        if not to_archive:
            return True
        ids_to_archive = [m["id"] for m in to_archive if m.get("id")]
        if not ids_to_archive:
            return True
        return await self.archive_messages(ids_to_archive)


class LocalChatHistoryBackend(ChatHistoryBackend):
    """Zero-config sqlite storage (issue #416). Sync helpers run via asyncio.to_thread."""

    def __init__(self):
        local_store.init_local_db()

    # --- async surface -> off-loop sync helpers ---
    async def get_conversations(self, limit, offset):
        return await asyncio.to_thread(self._get_conversations, limit, offset)

    async def get_conversation(self, cleaned_id):
        return await asyncio.to_thread(self._get_conversation, cleaned_id)

    async def get_messages(self, cleaned_id, include_archived):
        return await asyncio.to_thread(self._get_messages, cleaned_id, include_archived)

    async def archive_messages(self, message_ids):
        return await asyncio.to_thread(self._archive_messages, message_ids)

    async def update_message_metadata(self, cleaned_id, updates):
        return await asyncio.to_thread(self._update_message_metadata, cleaned_id, updates)

    async def create_conversation(self, model, title, domain=None):
        return await asyncio.to_thread(self._create_conversation, model, title, domain)

    async def append_message(self, cleaned_id, role, processed_content, metadata, message_id):
        return await asyncio.to_thread(self._append_message, cleaned_id, role, processed_content, metadata, message_id)

    async def update_conversation_title(self, cleaned_id, title):
        return await asyncio.to_thread(self._update_conversation, cleaned_id, {"title": title})

    async def update_conversation_pinned(self, cleaned_id, is_pinned):
        return await asyncio.to_thread(self._update_conversation, cleaned_id, {"is_pinned": int(is_pinned)})

    async def update_conversation_project(self, cleaned_id, project_path):
        return await asyncio.to_thread(self._update_conversation, cleaned_id, {"project_path": project_path})

    async def update_conversation_domain(self, cleaned_id, domain):
        return await asyncio.to_thread(self._update_conversation, cleaned_id, {"domain": domain})

    async def reassign_project_path(self, old_path, new_path):
        return await asyncio.to_thread(self._reassign_project_path, old_path, new_path)

    async def delete_conversation(self, cleaned_id):
        return await asyncio.to_thread(self._delete_conversation, cleaned_id)

    async def search_chat_messages(self, query, limit):
        return await asyncio.to_thread(self._search_messages, query, limit)

    async def search_conversations(self, query, limit):
        return await asyncio.to_thread(self._search_conversations, query, limit)

    async def truncate_conversation_after(self, cleaned_id: str, keep_count: Optional[int] = None) -> bool:
        return await asyncio.to_thread(self._truncate_conversation_after, cleaned_id, keep_count)

    # --- row converters ---
    @staticmethod
    def _conv_row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        d["is_pinned"] = bool(d.get("is_pinned"))
        return d

    @staticmethod
    def _msg_row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        d["is_archived"] = bool(d.get("is_archived"))
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        _maybe_parse_json_content(d)
        return d

    # --- sync sqlite helpers ---
    def _get_conversations(self, limit, offset):
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY is_pinned DESC, updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [self._conv_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def _get_conversation(self, conversation_id):
        conn = local_store.local_db_connect()
        try:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return self._conv_row_to_dict(row) if row else None
        finally:
            conn.close()

    def _get_messages(self, conversation_id, include_archived):
        if not conversation_id:
            return []
        conn = local_store.local_db_connect()
        try:
            sql = "SELECT * FROM messages WHERE conversation_id = ?"
            if not include_archived:
                sql += " AND is_archived = 0"
            sql += " ORDER BY created_at ASC"
            rows = conn.execute(sql, (conversation_id,)).fetchall()
            return [self._msg_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def _archive_messages(self, message_ids):
        conn = local_store.local_db_connect()
        try:
            now = datetime.now().isoformat()
            placeholders = ",".join("?" for _ in message_ids)
            conn.execute(
                f"UPDATE messages SET is_archived = 1, compacted_at = ? WHERE id IN ({placeholders})",
                [now] + message_ids,
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to archive messages locally: {e}")
            return False
        finally:
            conn.close()

    def _truncate_conversation_after(self, conversation_id: str, keep_count: Optional[int] = None) -> bool:
        if not conversation_id:
            return False
        messages = self._get_messages(conversation_id, include_archived=False)
        if not messages:
            return True
        start_idx = max(0, keep_count) if keep_count is not None else 0
        to_archive = messages[start_idx:]
        if not to_archive:
            return True
        ids_to_archive = [m["id"] for m in to_archive if m.get("id")]
        if not ids_to_archive:
            return True
        return self._archive_messages(ids_to_archive)

    def _update_message_metadata(self, message_id, updates):
        conn = local_store.local_db_connect()
        try:
            row = conn.execute("SELECT metadata FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None:
                return False
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            metadata.update(updates)
            conn.execute("UPDATE messages SET metadata = ? WHERE id = ?", (json.dumps(metadata), message_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update message metadata locally for {message_id}: {e}")
            return False
        finally:
            conn.close()

    def _create_conversation(self, model, title, domain=None):
        conn = local_store.local_db_connect()
        try:
            conv_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO conversations (id, title, model, domain, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, title, model, domain, now, now),
            )
            conn.commit()
            return {"id": conv_id, "title": title, "model": model, "project_path": None,
                    "domain": domain, "is_pinned": False, "created_at": now, "updated_at": now}
        finally:
            conn.close()

    def _append_message(self, conversation_id, role, content, metadata, message_id):
        conn = local_store.local_db_connect()
        try:
            msg_id = message_id or str(uuid.uuid4())
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, conversation_id, role, content, json.dumps(metadata or {}), now),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            conn.commit()
            return {"id": msg_id, "conversation_id": conversation_id, "role": role, "content": content,
                    "metadata": metadata or {}, "is_archived": False, "created_at": now}
        except Exception as e:
            logger.error(f"Failed to append message locally to conversation {conversation_id}: {e}")
            return {}
        finally:
            conn.close()

    def _update_conversation(self, conversation_id, fields):
        conn = local_store.local_db_connect()
        try:
            sets = ", ".join(f"{k} = ?" for k in fields)
            params = list(fields.values()) + [datetime.now().isoformat(), conversation_id]
            conn.execute(f"UPDATE conversations SET {sets}, updated_at = ? WHERE id = ?", params)
            conn.commit()
            return True
        finally:
            conn.close()

    def _reassign_project_path(self, old_path, new_path):
        conn = local_store.local_db_connect()
        try:
            conn.execute(
                "UPDATE conversations SET project_path = ?, updated_at = ? WHERE project_path = ?",
                (new_path, datetime.now().isoformat(), old_path),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _delete_conversation(self, conversation_id):
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def _search_messages(self, query, limit):
        conn = local_store.local_db_connect()
        try:
            fts_hits = self._search_messages_fts(conn, query, limit)
            if fts_hits:
                return fts_hits
            return self._search_messages_like(conn, query, limit)
        finally:
            conn.close()

    def _search_messages_fts(self, conn, query: str, limit: int) -> List[Dict[str, Any]]:
        match = _fts_match_query(query)
        if not match:
            return []
        try:
            # content is FTS column index 3 (message_id, conversation_id, role, content, title).
            rows = conn.execute(
                """
                SELECT m.*, c.title AS conversation_title,
                       snippet(messages_fts, 3, '', '', '…', 24) AS snippet,
                       bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.message_id
                LEFT JOIN conversations c ON c.id = m.conversation_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except Exception as e:
            logger.warning(f"[ CHAT_FTS ] MATCH failed for {query!r} ({e}); falling back to LIKE")
            return []

        results = []
        for r in rows:
            d = self._msg_row_to_dict(r)
            d["conversation_title"] = r["conversation_title"] or "Untitled Chat"
            snip = r["snippet"]
            d["snippet"] = snip if snip else make_snippet(d.get("content"), query)
            d["rank"] = float(r["rank"]) if r["rank"] is not None else None
            results.append(d)
        return results

    def _search_messages_like(self, conn, query: str, limit: int) -> List[Dict[str, Any]]:
        rows = conn.execute(
            "SELECT m.*, c.title AS conversation_title FROM messages m "
            "LEFT JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.content LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        results = []
        for r in rows:
            d = self._msg_row_to_dict(r)
            d["conversation_title"] = r["conversation_title"] or "Untitled Chat"
            d["snippet"] = make_snippet(d.get("content"), query)
            d["rank"] = None
            results.append(d)
        return results

    def _search_conversations(self, query, limit):
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE title LIKE ? LIMIT ?", (f"%{query}%", limit)
            ).fetchall()
            return [self._conv_row_to_dict(r) for r in rows]
        finally:
            conn.close()


def _maybe_parse_json_content(msg: Dict[str, Any]) -> None:
    """Deserialize a message's ``content`` in place if it's a JSON array/object string."""
    content = msg.get("content", "")
    if content and isinstance(content, str) and (content.startswith("[") or content.startswith("{")):
        try:
            msg["content"] = json.loads(content)
        except json.JSONDecodeError:
            pass
