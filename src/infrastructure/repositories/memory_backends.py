"""Storage backends for :class:`MemoryRepository`.

The local/cloud decision is made **once** (in ``MemoryRepository.__init__``, by picking
one of these) instead of being re-derived by an ``if not self.supabase.enabled:`` branch at
the top of every method. Each backend owns one storage dialect end-to-end:

- :class:`SupabaseMemoryBackend` — PostgREST + pgvector RPC (cloud).
- :class:`LocalMemoryBackend` — zero-config sqlite store (issue #416), brute-force cosine.

Both implement the same surface, so the repository can delegate without knowing which is
live. Session-isolation semantics (exact session OR global/NULL OR same-account channel) are
kept identical across the two — see ``_build_session_filter`` vs ``_local_session_where``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.shared.logger import setup_logger
from src.infrastructure.database import local_store

logger = setup_logger("MemoryRepository")


def build_session_filter(session_key: Optional[str]) -> Dict[str, str]:
    """Session isolation + fallback for PostgREST: exact session OR legacy/global (NULL)
    OR parallel channel for the same account. Mirrors public.match_memories. Shared by
    the cloud backend and the repository's back-compat helper."""
    if not session_key:
        return {}
    clauses = [f"session_key.eq.{session_key}", "session_key.is.null"]
    if ":" in session_key:
        parts = session_key.split(':')
        if len(parts) >= 2:
            account = parts[1]
            if account and account != "*":
                clauses.append(f"session_key.ilike.*:{account}:*")
    return {"or": f"({','.join(clauses)})"}


class MemoryBackend(ABC):
    """Storage-dialect strategy for memories + conversation history."""

    mem_table = "memories"
    hist_table = "conversation_history"

    # --- Memory CRUD ---
    @abstractmethod
    def create_memory(self, content: str, category: str, importance: float, source: str,
                      embedding: Optional[List[float]], session_key: Optional[str],
                      metadata: Optional[Dict[str, Any]]) -> int: ...

    @abstractmethod
    def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def update_memory(self, memory_id: int, content: Optional[str], category: Optional[str],
                      importance: Optional[float]) -> bool: ...

    @abstractmethod
    def has_memory_with_hash(self, item_hash: str) -> bool: ...

    @abstractmethod
    def expire_memories_by_source(self, source: str, max_age_days: int) -> bool: ...

    @abstractmethod
    def delete_memory(self, memory_id: int) -> bool: ...

    @abstractmethod
    def clear_all(self) -> bool: ...

    # --- Memory queries ---
    @abstractmethod
    def list_memories(self, category: Optional[str], session_key: Optional[str],
                      limit: int, offset: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def search_memories(self, query: str, session_key: Optional[str], limit: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def search_memories_by_category(self, query: str, category: str, session_key: Optional[str],
                                    limit: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def vector_search_memories(self, embedding: List[float], match_count: int,
                               session_key: Optional[str], match_threshold: float) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_all_long_term_memories(self, session_key: Optional[str], limit: int) -> List[Dict[str, Any]]: ...

    # --- History ---
    @abstractmethod
    def create_message(self, role: str, content: str, session_key: Optional[str],
                       timestamp: Optional[str], metadata: Optional[str]) -> int: ...

    @abstractmethod
    def get_conversation_history(self, session_key: Optional[str], limit: int, offset: int) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def clear_conversation_history(self) -> bool: ...

    @abstractmethod
    def count_messages(self) -> int: ...

    @abstractmethod
    def get_statistics(self) -> Dict[str, Any]: ...

    # --- Drive catalog upsert (optional; both backends implement) ---
    def find_memory_by_file_id(
        self, file_id: str, category: str = "drive_catalog"
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def upsert_memory_by_file_id(
        self,
        file_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        raise NotImplementedError

    def delete_source_memories_missing_file_ids(
        self, source: str, keep_file_ids: set, category: str = "drive_catalog"
    ) -> int:
        raise NotImplementedError

    # --- Gmail personal_index upsert (identity = metadata.message_id) ---
    def find_memory_by_message_id(
        self, message_id: str, category: str = "personal_index"
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def upsert_memory_by_message_id(
        self,
        message_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        raise NotImplementedError


class SupabaseMemoryBackend(MemoryBackend):
    """PostgREST + pgvector RPC storage (cloud)."""

    def __init__(self, supabase):
        self.supabase = supabase

    def _session_filter(self, session_key: Optional[str]) -> Dict[str, str]:
        return build_session_filter(session_key)

    def create_memory(self, content, category, importance, source, embedding, session_key, metadata) -> int:
        now = datetime.now().isoformat()
        data = {
            "content": content,
            "category": category,
            "importance": importance,
            "source": source,
            "session_key": session_key,
            "metadata": metadata or {},
            "created_at": now,
            "last_accessed": now,
        }
        # Only include vector embedding if successfully generated to avoid PostgREST null-casting 400 errors
        if embedding is not None:
            data["embedding"] = embedding
        res = self.supabase.insert(self.mem_table, data)
        return res.get("id") if res else -1

    def get_memory(self, memory_id) -> Optional[Dict[str, Any]]:
        res = self.supabase.query(self.mem_table, filters={"id": f"eq.{memory_id}"}, limit=1)
        return res[0] if res else None

    def update_memory(self, memory_id, content, category, importance) -> bool:
        now = datetime.now().isoformat()
        updates: Dict[str, Any] = {}
        if content is not None:
            updates["content"] = content
        if category is not None:
            updates["category"] = category
        if importance is not None:
            updates["importance"] = importance
        updates["last_accessed"] = now
        return self.supabase.update(self.mem_table, filters={"id": f"eq.{memory_id}"}, data=updates)

    def has_memory_with_hash(self, item_hash) -> bool:
        if not item_hash:
            return False
        try:
            # Use Postgres JSONB arrow operator syntax supported by PostgREST
            res = self.supabase.query(
                self.mem_table, select="id", filters={"metadata->>hash": f"eq.{item_hash}"}, limit=1
            )
            return len(res) > 0
        except Exception as e:
            logger.error(f"Failed to query hash '{item_hash}': {e}")
            return False

    def expire_memories_by_source(self, source, max_age_days) -> bool:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        try:
            filters = {"source": f"eq.{source}", "created_at": f"lt.{cutoff}"}
            return self.supabase.delete(self.mem_table, filters=filters)
        except Exception as e:
            logger.error(f"Failed to expire old memories for source '{source}': {e}")
            return False

    def delete_memory(self, memory_id) -> bool:
        return self.supabase.delete(self.mem_table, filters={"id": f"eq.{memory_id}"})

    def clear_all(self) -> bool:
        return self.supabase.delete(self.mem_table, filters={"id": "gt.0"})

    def list_memories(self, category, session_key, limit, offset) -> List[Dict[str, Any]]:
        filters: Dict[str, str] = {}
        if category:
            filters["category"] = f"eq.{category}"
        if session_key:
            filters.update(self._session_filter(session_key))
        return self.supabase.query(
            self.mem_table, filters=filters, limit=limit, offset=offset or None, order="created_at.desc"
        )

    def search_memories(self, query, session_key, limit) -> List[Dict[str, Any]]:
        filters = {"content": f"ilike.*{query}*"}
        if session_key:
            filters.update(self._session_filter(session_key))
        res = self.supabase.query(self.mem_table, filters=filters, limit=limit)
        res.sort(key=lambda x: (x.get("importance", 0.5), x.get("created_at", "")), reverse=True)
        return res

    def search_memories_by_category(self, query, category, session_key, limit) -> List[Dict[str, Any]]:
        filters = {"category": f"eq.{category}", "content": f"ilike.*{query}*"}
        if session_key:
            filters.update(self._session_filter(session_key))
        return self.supabase.query(self.mem_table, filters=filters, limit=limit)

    def vector_search_memories(self, embedding, match_count, session_key, match_threshold) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "query_embedding": embedding,
            "match_threshold": match_threshold,
            "match_count": match_count,
        }
        if session_key:
            params["filter_session_key"] = session_key
        res = self.supabase.rpc("match_memories", params)
        return res or []

    def get_all_long_term_memories(self, session_key, limit) -> List[Dict[str, Any]]:
        filters: Dict[str, str] = {}
        if session_key:
            filters.update(self._session_filter(session_key))
        res = self.supabase.query(self.mem_table, filters=filters, limit=limit)
        res.sort(key=lambda x: (x.get("importance", 0.5), x.get("last_accessed", "")), reverse=True)
        return res

    def create_message(self, role, content, session_key, timestamp, metadata) -> int:
        ts = timestamp or datetime.now().isoformat()
        data = {"role": role, "content": content, "session_key": session_key, "timestamp": ts, "metadata": metadata}
        res = self.supabase.insert(self.hist_table, data)
        return res.get("id") if res else -1

    def get_conversation_history(self, session_key, limit, offset) -> List[Dict[str, Any]]:
        filters: Dict[str, str] = {}
        if session_key:
            filters["session_key"] = f"eq.{session_key}"
        res = self.supabase.query(self.hist_table, filters=filters, limit=limit)
        res.sort(key=lambda x: x.get("timestamp", ""))
        return res

    def clear_conversation_history(self) -> bool:
        return self.supabase.delete(self.hist_table, filters={"id": "gt.0"})

    def count_messages(self) -> int:
        res = self.supabase.query(self.hist_table, select="id")
        return len(res)

    def get_statistics(self) -> Dict[str, Any]:
        mems = self.supabase.query(self.mem_table, select="id,category")
        msgs = self.supabase.query(self.hist_table, select="id")
        categories: Dict[str, int] = {}
        for m in mems:
            cat = m.get("category", "general")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            'total_memories': len(mems),
            'total_messages': len(msgs),
            'categories': categories,
            'remote': True,
        }

    def find_memory_by_file_id(
        self, file_id: str, category: str = "drive_catalog"
    ) -> Optional[Dict[str, Any]]:
        if not file_id:
            return None
        try:
            res = self.supabase.query(
                self.mem_table,
                filters={
                    "category": f"eq.{category}",
                    "metadata->>file_id": f"eq.{file_id}",
                },
                limit=1,
            )
            return res[0] if res else None
        except Exception as e:
            logger.error(f"find_memory_by_file_id failed for '{file_id}': {e}")
            return None

    def upsert_memory_by_file_id(
        self,
        file_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        existing = self.find_memory_by_file_id(file_id, category=category)
        now = datetime.now().isoformat()
        meta = metadata or {}
        if existing and existing.get("id") is not None:
            updates: Dict[str, Any] = {
                "content": content,
                "importance": importance,
                "source": source,
                "metadata": meta,
                "last_accessed": now,
            }
            if embedding is not None:
                updates["embedding"] = embedding
            ok = self.supabase.update(
                self.mem_table,
                filters={"id": f"eq.{existing['id']}"},
                data=updates,
            )
            return int(existing["id"]) if ok else -1
        return self.create_memory(
            content, category, importance, source, embedding, None, meta
        )

    def find_memory_by_message_id(
        self, message_id: str, category: str = "personal_index"
    ) -> Optional[Dict[str, Any]]:
        if not message_id:
            return None
        try:
            res = self.supabase.query(
                self.mem_table,
                filters={
                    "category": f"eq.{category}",
                    "metadata->>message_id": f"eq.{message_id}",
                },
                limit=1,
            )
            return res[0] if res else None
        except Exception as e:
            logger.error(f"find_memory_by_message_id failed for '{message_id}': {e}")
            return None

    def upsert_memory_by_message_id(
        self,
        message_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        existing = self.find_memory_by_message_id(message_id, category=category)
        now = datetime.now().isoformat()
        meta = metadata or {}
        if existing and existing.get("id") is not None:
            updates: Dict[str, Any] = {
                "content": content,
                "importance": importance,
                "source": source,
                "metadata": meta,
                "last_accessed": now,
            }
            if embedding is not None:
                updates["embedding"] = embedding
            ok = self.supabase.update(
                self.mem_table,
                filters={"id": f"eq.{existing['id']}"},
                data=updates,
            )
            return int(existing["id"]) if ok else -1
        return self.create_memory(
            content, category, importance, source, embedding, None, meta
        )

    def delete_source_memories_missing_file_ids(
        self, source: str, keep_file_ids: set, category: str = "drive_catalog"
    ) -> int:
        """Delete catalog rows for ``source`` whose file_id is not in keep_file_ids.

        Mass-delete is gated by ``plan_catalog_reconcile_deletes`` (empty keep /
        high delete ratio refuse by default) so a bad sync cannot wipe the catalog.
        """
        from src.core.intelligence.ingestion.ingest_safety import (
            plan_catalog_reconcile_deletes,
        )
        from src.shared.config_manager import ConfigManager

        try:
            rows = self.supabase.query(
                self.mem_table,
                select="id,metadata",
                filters={"source": f"eq.{source}", "category": f"eq.{category}"},
                limit=50000,
            ) or []
            existing_ids = set()
            id_by_fid = {}
            for row in rows:
                meta = row.get("metadata") or {}
                fid = meta.get("file_id")
                if fid:
                    existing_ids.add(fid)
                    id_by_fid.setdefault(fid, []).append(row["id"])

            max_ratio = float(
                ConfigManager.get("drive_catalog.max_reconcile_delete_ratio", 0.25) or 0.25
            )
            allow_wipe = bool(
                ConfigManager.get("drive_catalog.allow_empty_catalog_wipe", False)
            )
            to_delete, skip = plan_catalog_reconcile_deletes(
                existing_ids,
                keep_file_ids or set(),
                max_delete_ratio=max_ratio,
                allow_empty_wipe=allow_wipe,
            )
            if skip:
                logger.error(
                    f"[ Memory ] Skipping {source} reconcile delete: {skip}"
                )
                return 0

            deleted = 0
            for fid in to_delete:
                for row_id in id_by_fid.get(fid, []):
                    if self.delete_memory(row_id):
                        deleted += 1
            return deleted
        except Exception as e:
            logger.error(f"delete_source_memories_missing_file_ids failed: {e}")
            return 0


class LocalMemoryBackend(MemoryBackend):
    """Zero-config sqlite storage with brute-force cosine vector search (issue #416)."""

    def __init__(self):
        local_store.init_local_db()

    @staticmethod
    def _row_to_memory_dict(row) -> Dict[str, Any]:
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        d.pop("embedding", None)
        return d

    def _session_where(self, session_key: Optional[str]) -> tuple:
        """SQL equivalent of the PostgREST session filter for the local sqlite backend."""
        if not session_key:
            return "1=1", []
        clauses = ["session_key = ?", "session_key IS NULL"]
        params = [session_key]
        if ":" in session_key:
            parts = session_key.split(':')
            if len(parts) >= 2 and parts[1] and parts[1] != "*":
                clauses.append("session_key LIKE ?")
                params.append(f"%:{parts[1]}:%")
        return f"({' OR '.join(clauses)})", params

    def create_memory(self, content, category, importance, source, embedding, session_key, metadata) -> int:
        now = datetime.now().isoformat()
        conn = local_store.local_db_connect()
        try:
            blob = local_store.embedding_to_blob(embedding) if embedding is not None else None
            cur = conn.execute(
                "INSERT INTO memories (content, category, importance, source, session_key, metadata, embedding, created_at, last_accessed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (content, category, importance, source, session_key, json.dumps(metadata or {}), blob, now, now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_memory(self, memory_id) -> Optional[Dict[str, Any]]:
        conn = local_store.local_db_connect()
        try:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return self._row_to_memory_dict(row) if row else None
        finally:
            conn.close()

    def update_memory(self, memory_id, content, category, importance) -> bool:
        now = datetime.now().isoformat()
        sets, params = [], []
        if content is not None:
            sets.append("content = ?"); params.append(content)
        if category is not None:
            sets.append("category = ?"); params.append(category)
        if importance is not None:
            sets.append("importance = ?"); params.append(importance)
        sets.append("last_accessed = ?"); params.append(now)
        params.append(memory_id)
        conn = local_store.local_db_connect()
        try:
            conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            return True
        finally:
            conn.close()

    def has_memory_with_hash(self, item_hash) -> bool:
        if not item_hash:
            return False
        conn = local_store.local_db_connect()
        try:
            row = conn.execute(
                "SELECT id FROM memories WHERE json_extract(metadata, '$.hash') = ? LIMIT 1",
                (item_hash,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def expire_memories_by_source(self, source, max_age_days) -> bool:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM memories WHERE source = ? AND created_at < ?", (source, cutoff))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to expire old memories for source '{source}': {e}")
            return False
        finally:
            conn.close()

    def delete_memory(self, memory_id) -> bool:
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def clear_all(self) -> bool:
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM memories")
            conn.commit()
            return True
        finally:
            conn.close()

    def list_memories(self, category, session_key, limit, offset) -> List[Dict[str, Any]]:
        where, params = self._session_where(session_key)
        if category:
            where += " AND category = ?"
            params.append(category)
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset or 0],
            ).fetchall()
            return [self._row_to_memory_dict(r) for r in rows]
        finally:
            conn.close()

    def search_memories(self, query, session_key, limit) -> List[Dict[str, Any]]:
        where, params = self._session_where(session_key)
        where += " AND content LIKE ?"
        params.append(f"%{query}%")
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(f"SELECT * FROM memories WHERE {where}", params).fetchall()
            res = [self._row_to_memory_dict(r) for r in rows]
            res.sort(key=lambda x: (x.get("importance", 0.5), x.get("created_at", "")), reverse=True)
            return res[:limit]
        finally:
            conn.close()

    def search_memories_by_category(self, query, category, session_key, limit) -> List[Dict[str, Any]]:
        where, params = self._session_where(session_key)
        where += " AND category = ? AND content LIKE ?"
        params += [category, f"%{query}%"]
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where} LIMIT ?", params + [limit]
            ).fetchall()
            return [self._row_to_memory_dict(r) for r in rows]
        finally:
            conn.close()

    def vector_search_memories(self, embedding, match_count, session_key, match_threshold) -> List[Dict[str, Any]]:
        where, params = self._session_where(session_key)
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where} AND embedding IS NOT NULL", params
            ).fetchall()
            results = local_store.cosine_topk(embedding, rows, "embedding", match_count, match_threshold)
            for r in results:
                if isinstance(r.get("metadata"), str):
                    try:
                        r["metadata"] = json.loads(r["metadata"]) if r["metadata"] else {}
                    except Exception:
                        r["metadata"] = {}
            return results
        finally:
            conn.close()

    def get_all_long_term_memories(self, session_key, limit) -> List[Dict[str, Any]]:
        where, params = self._session_where(session_key)
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(f"SELECT * FROM memories WHERE {where} LIMIT ?", params + [limit]).fetchall()
            res = [self._row_to_memory_dict(r) for r in rows]
            res.sort(key=lambda x: (x.get("importance", 0.5), x.get("last_accessed", "")), reverse=True)
            return res
        finally:
            conn.close()

    def find_memory_by_file_id(
        self, file_id: str, category: str = "drive_catalog"
    ) -> Optional[Dict[str, Any]]:
        if not file_id:
            return None
        conn = local_store.local_db_connect()
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE category = ? AND json_extract(metadata, '$.file_id') = ? LIMIT 1",
                (category, file_id),
            ).fetchone()
            return self._row_to_memory_dict(row) if row else None
        finally:
            conn.close()

    def upsert_memory_by_file_id(
        self,
        file_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        existing = self.find_memory_by_file_id(file_id, category=category)
        now = datetime.now().isoformat()
        meta = metadata or {}
        conn = local_store.local_db_connect()
        try:
            blob = local_store.embedding_to_blob(embedding) if embedding is not None else None
            if existing and existing.get("id") is not None:
                if embedding is not None:
                    conn.execute(
                        "UPDATE memories SET content=?, importance=?, source=?, metadata=?, "
                        "embedding=?, last_accessed=? WHERE id=?",
                        (
                            content,
                            importance,
                            source,
                            json.dumps(meta),
                            blob,
                            now,
                            existing["id"],
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE memories SET content=?, importance=?, source=?, metadata=?, "
                        "last_accessed=? WHERE id=?",
                        (content, importance, source, json.dumps(meta), now, existing["id"]),
                    )
                conn.commit()
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO memories (content, category, importance, source, session_key, metadata, "
                "embedding, created_at, last_accessed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content,
                    category,
                    importance,
                    source,
                    None,
                    json.dumps(meta),
                    blob,
                    now,
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def find_memory_by_message_id(
        self, message_id: str, category: str = "personal_index"
    ) -> Optional[Dict[str, Any]]:
        if not message_id:
            return None
        conn = local_store.local_db_connect()
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE category = ? "
                "AND json_extract(metadata, '$.message_id') = ? LIMIT 1",
                (category, message_id),
            ).fetchone()
            return self._row_to_memory_dict(row) if row else None
        finally:
            conn.close()

    def upsert_memory_by_message_id(
        self,
        message_id: str,
        content: str,
        category: str,
        importance: float,
        source: str,
        embedding: Optional[List[float]],
        metadata: Optional[Dict[str, Any]],
    ) -> int:
        existing = self.find_memory_by_message_id(message_id, category=category)
        now = datetime.now().isoformat()
        meta = metadata or {}
        conn = local_store.local_db_connect()
        try:
            blob = local_store.embedding_to_blob(embedding) if embedding is not None else None
            if existing and existing.get("id") is not None:
                if embedding is not None:
                    conn.execute(
                        "UPDATE memories SET content=?, importance=?, source=?, metadata=?, "
                        "embedding=?, last_accessed=? WHERE id=?",
                        (
                            content,
                            importance,
                            source,
                            json.dumps(meta),
                            blob,
                            now,
                            existing["id"],
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE memories SET content=?, importance=?, source=?, metadata=?, "
                        "last_accessed=? WHERE id=?",
                        (content, importance, source, json.dumps(meta), now, existing["id"]),
                    )
                conn.commit()
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO memories (content, category, importance, source, session_key, metadata, "
                "embedding, created_at, last_accessed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content,
                    category,
                    importance,
                    source,
                    None,
                    json.dumps(meta),
                    blob,
                    now,
                    now,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def delete_source_memories_missing_file_ids(
        self, source: str, keep_file_ids: set, category: str = "drive_catalog"
    ) -> int:
        """Delete catalog rows not in keep_file_ids, with mass-delete safety gates."""
        from src.core.intelligence.ingestion.ingest_safety import (
            plan_catalog_reconcile_deletes,
        )
        from src.shared.config_manager import ConfigManager

        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                "SELECT id, metadata FROM memories WHERE source = ? AND category = ?",
                (source, category),
            ).fetchall()
            existing_ids = set()
            id_by_fid = {}
            for row in rows:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                fid = meta.get("file_id")
                if fid:
                    existing_ids.add(fid)
                    id_by_fid.setdefault(fid, []).append(row["id"])

            max_ratio = float(
                ConfigManager.get("drive_catalog.max_reconcile_delete_ratio", 0.25) or 0.25
            )
            allow_wipe = bool(
                ConfigManager.get("drive_catalog.allow_empty_catalog_wipe", False)
            )
            to_delete, skip = plan_catalog_reconcile_deletes(
                existing_ids,
                keep_file_ids or set(),
                max_delete_ratio=max_ratio,
                allow_empty_wipe=allow_wipe,
            )
            if skip:
                logger.error(
                    f"[ Memory ] Skipping {source} reconcile delete: {skip}"
                )
                return 0

            deleted = 0
            for fid in to_delete:
                for row_id in id_by_fid.get(fid, []):
                    conn.execute("DELETE FROM memories WHERE id = ?", (row_id,))
                    deleted += 1
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"delete_source_memories_missing_file_ids failed: {e}")
            return 0
        finally:
            conn.close()

    def create_message(self, role, content, session_key, timestamp, metadata) -> int:
        ts = timestamp or datetime.now().isoformat()
        conn = local_store.local_db_connect()
        try:
            cur = conn.execute(
                "INSERT INTO conversation_history (role, content, session_key, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
                (role, content, session_key, ts, metadata),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_conversation_history(self, session_key, limit, offset) -> List[Dict[str, Any]]:
        where, params = ("session_key = ?", [session_key]) if session_key else ("1=1", [])
        conn = local_store.local_db_connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM conversation_history WHERE {where} ORDER BY timestamp ASC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def clear_conversation_history(self) -> bool:
        conn = local_store.local_db_connect()
        try:
            conn.execute("DELETE FROM conversation_history")
            conn.commit()
            return True
        finally:
            conn.close()

    def count_messages(self) -> int:
        conn = local_store.local_db_connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM conversation_history").fetchone()[0]
        finally:
            conn.close()

    def get_statistics(self) -> Dict[str, Any]:
        conn = local_store.local_db_connect()
        try:
            mem_rows = conn.execute("SELECT category FROM memories").fetchall()
            msg_count = conn.execute("SELECT COUNT(*) FROM conversation_history").fetchone()[0]
            categories: Dict[str, int] = {}
            for r in mem_rows:
                cat = r["category"] or "general"
                categories[cat] = categories.get(cat, 0) + 1
            return {
                'total_memories': len(mem_rows),
                'total_messages': msg_count,
                'categories': categories,
                'remote': False,
            }
        finally:
            conn.close()
