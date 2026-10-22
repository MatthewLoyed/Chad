"""
Memory Repository

Data access layer for memory + conversation-history persistence.

The local/cloud storage decision is made **once**, in ``__init__``, by selecting a
:class:`MemoryBackend` strategy (see ``memory_backends.py``) — not re-derived by an
``if not self.supabase.enabled:`` branch at the top of every method. Public methods are
thin delegators to the chosen backend, so there is a single place a given operation can
diverge between local and cloud.

**What may go to Supabase when enabled (backup only):**
  - Personal knowledge-graph facts (``GraphRAGClient`` / ``knowledge_graph.storage``)
  - Persona prompt overlays (``update_persona_config`` → optional ``persona_config`` upsert)

**What stays on-disk forever (never mirrored, never cloud-primary):**
  - Bulk activity: Chrome, Gmail, Drive activity, filesystem catalog (``personal_index``)
  - Drive location catalog (``drive_catalog``)
  - Session logs and other high-volume memories

All methods are synchronous — SupabaseManager uses httpx.Client (sync).
"""

from typing import List, Dict, Any, Optional

from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.repositories import persona_store
from src.infrastructure.repositories.memory_backends import (
    LocalMemoryBackend,
    SupabaseMemoryBackend,
)

logger = setup_logger("MemoryRepository")

# Bulk / high-volume ingest sources — sqlite only (issues #619 + storage policy).
_LOCAL_ONLY_SOURCES = frozenset({
    "chrome",
    "gmail",
    "filesystem",
    "google_drive",
    "drive_catalog",
})

# Categories that must never leave the machine (even if source is unexpected).
_LOCAL_ONLY_CATEGORIES = frozenset({
    "personal_index",
    "drive_catalog",
    "session_log",
})


class MemoryRepository:
    """Repository for memory persistence — **local is always the source of truth**.

    Open-source default: sqlite only. Activity / catalogs / session logs never
    mirror to Supabase. Optional cloud backup is limited to personal knowledge
    (graph) and persona prompts — not the memories table.
    """

    def __init__(self, db_path: Optional[str] = None):
        """Args: db_path — ignored, kept for legacy compatibility."""
        self.supabase = get_supabase_manager()
        self.persona_table = "persona_config"
        self._local = LocalMemoryBackend()
        self._cloud = (
            SupabaseMemoryBackend(self.supabase) if self.supabase.enabled else None
        )
        self._backend = self._select_primary_backend()

    def _memory_storage_mode(self) -> str:
        try:
            from src.shared.config_manager import ConfigManager

            configured = ConfigManager.get("memory.storage", None)
        except Exception:
            configured = None
        if configured is not None and str(configured).strip():
            return str(configured).strip().lower()
        # Unset: always local. Memories are not a Supabase backup target.
        return "local"

    def _select_primary_backend(self):
        mode = self._memory_storage_mode()
        if mode == "supabase" and self._cloud is not None:
            return self._cloud
        # local / local_then_supabase / unknown → local is primary
        # (local_then_supabase is accepted for back-compat but does not mirror memories)
        return self._local

    def _maybe_mirror_create(self, **kwargs) -> None:
        """No-op: memories table is never mirrored to Supabase.

        Personal facts backup via GraphRAG; prompts via ``update_persona_config``.
        """
        return

    @staticmethod
    def _is_local_only_source(source: Optional[str]) -> bool:
        return (source or "").strip().lower() in _LOCAL_ONLY_SOURCES

    @staticmethod
    def _is_local_only_category(category: Optional[str]) -> bool:
        return (category or "").strip().lower() in _LOCAL_ONLY_CATEGORIES

    def _write_backend_for(
        self, source: Optional[str] = None, category: Optional[str] = None
    ):
        """Bulk activity/catalogs always use sqlite, never cloud-primary."""
        if self._is_local_only_source(source) or self._is_local_only_category(category):
            return self._local
        return self._backend

    def _write_backend_for_source(self, source: Optional[str]):
        """Back-compat alias used by chrome/local-only ingest paths."""
        return self._write_backend_for(source=source)

    def _catalog_storage_mode(self) -> str:
        """Drive catalog is always local — never mirrored to Supabase."""
        try:
            from src.shared.config_manager import ConfigManager

            configured = ConfigManager.get("drive_catalog.storage", None)
        except Exception:
            configured = None
        if configured is not None and str(configured).strip():
            mode = str(configured).strip().lower()
            # Treat local_then_supabase as local (no catalog cloud copy).
            if mode == "local_then_supabase":
                return "local"
            return mode
        return "local"

    @property
    def catalog_backend(self):
        """Backend used for drive_catalog reads/writes (always local unless escape hatch)."""
        mode = self._catalog_storage_mode()
        if mode == "supabase" and self._cloud is not None:
            return self._cloud
        return self._local

    def _persona_backup_enabled(self) -> bool:
        """Persona overlays mirror when knowledge_graph backup mode is on."""
        try:
            from src.shared.config_manager import ConfigManager
            from src.infrastructure.database.supabase_manager import get_supabase_manager

            if not get_supabase_manager().enabled:
                return False
            configured = ConfigManager.get("knowledge_graph.storage", None)
            if configured is not None and str(configured).strip():
                return str(configured).strip().lower() == "local_then_supabase"
            return True  # unset + supabase enabled → backup prompts with personal graph
        except Exception:
            return False

    def _maybe_mirror_persona(self, config_id: str, content: str, scope: Optional[str]) -> None:
        if not self._persona_backup_enabled() or self._cloud is None:
            return
        try:
            # Upsert into persona_config for multi-device backup (files remain SoT).
            existing = self.supabase.query(
                self.persona_table,
                select="id",
                filters={"id": f"eq.{config_id}"},
                limit=1,
            )
            payload: Dict[str, Any] = {"content": content}
            if scope is not None:
                payload["scope"] = scope
            if existing:
                self.supabase.update(
                    self.persona_table,
                    filters={"id": f"eq.{config_id}"},
                    data=payload,
                )
            else:
                payload["id"] = config_id
                if "scope" not in payload:
                    payload["scope"] = "always"
                self.supabase.insert(self.persona_table, payload)
        except Exception as e:
            logger.warning(f"Persona Supabase backup failed for '{config_id}': {e}")

    # --- Persona Config Methods (local files — see persona_store) ---
    #
    # These delegate to `persona_store` (#589) rather than Supabase. Persona prompts are
    # files under prompts/persona (baseline) and config/persona (this user's overlay);
    # writes append a version-history row locally and never require a network round-trip.
    # When knowledge_graph.storage is local_then_supabase, a best-effort cloud copy is
    # written for multi-device backup — files remain the source of truth.

    def get_persona_config(self, config_id: str) -> Optional[str]:
        """Fetch the effective content of one persona fragment, or None if absent."""
        fragment = persona_store.get_fragment(config_id)
        return fragment[1] if fragment else None

    def update_persona_config(self, config_id: str, content: str, scope: Optional[str] = None) -> bool:
        """Write a persona fragment, recording a new version in local history.

        The write inherits the fragment's existing `scope` unless one is passed
        explicitly — without that carry-forward a write silently defaults the fragment
        to 'always', injecting it into EVERY prompt at once. `persona_store` enforces
        this; the argument is passed straight through.
        """
        ok = persona_store.write_fragment(
            config_id, content, scope=scope, source="update_persona_config"
        )
        if ok:
            resolved = scope
            if resolved is None:
                frag = persona_store.get_fragment(config_id)
                resolved = frag[0] if frag else None
            self._maybe_mirror_persona(config_id, content, resolved)
        return ok

    def list_persona_configs(self) -> List[str]:
        """List all persona fragment IDs."""
        return persona_store.list_fragment_ids()

    # --- Memory Methods (delegated to the chosen backend) ---

    def create_memory(
        self,
        content: str,
        category: str = 'general',
        importance: float = 0.5,
        source: str = 'user',
        embedding: Optional[List[float]] = None,
        session_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Create a new memory. Bulk sources/categories always write sqlite only."""
        backend = self._write_backend_for(source=source, category=category)
        mid = backend.create_memory(
            content, category, importance, source, embedding, session_key, metadata
        )
        if mid != -1:
            self._maybe_mirror_create(
                content=content,
                category=category,
                importance=importance,
                source=source,
                embedding=embedding,
                session_key=session_key,
                metadata=metadata,
            )
        return mid

    def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        return self._backend.get_memory(memory_id)

    def update_memory(
        self,
        memory_id: int,
        content: Optional[str] = None,
        category: Optional[str] = None,
        importance: Optional[float] = None,
    ) -> bool:
        """Update a memory."""
        return self._backend.update_memory(memory_id, content, category, importance)

    def has_memory_with_hash(self, item_hash: str, source: Optional[str] = None) -> bool:
        """Check if a memory with the given deduplication hash already exists.

        When ``source`` is local-only, the check is against sqlite so ingest dedupe
        matches where those rows actually live.
        """
        backend = self._write_backend_for(source=source) if source else self._backend
        return backend.has_memory_with_hash(item_hash)

    def expire_memories_by_source(self, source: str, max_age_days: int) -> bool:
        """Expire memories from a source older than max_age_days (TTL for ephemeral sources)."""
        backend = self._write_backend_for(source=source)
        return backend.expire_memories_by_source(source, max_age_days)

    def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory."""
        return self._backend.delete_memory(memory_id)

    def list_memories(
        self,
        category: Optional[str] = None,
        session_key: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List memories with optional category and session filter."""
        backend = self._write_backend_for(category=category) if category else self._backend
        return backend.list_memories(category, session_key, limit, offset)

    def search_memories(self, query: str, session_key: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Keyword search memories."""
        return self._backend.search_memories(query, session_key, limit)

    def search_memories_by_category(self, query: str, category: str, session_key: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Keyword search within a single category (e.g. browser-history keyword fallback)."""
        backend = self._write_backend_for(category=category)
        return backend.search_memories_by_category(query, category, session_key, limit)

    def vector_search_memories(
        self, embedding: List[float], match_count: int = 10, session_key: Optional[str] = None,
        match_threshold: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """Search memories by cosine similarity — pgvector RPC on Supabase, brute-force numpy locally."""
        return self._backend.vector_search_memories(embedding, match_count, session_key, match_threshold)

    def get_all_long_term_memories(self, session_key: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all memories sorted by importance and recency."""
        return self._backend.get_all_long_term_memories(session_key, limit)

    def clear_all(self) -> bool:
        """Clear all memories."""
        return self._backend.clear_all()

    # --- Conversation History (delegated to the chosen backend) ---

    def create_message(
        self,
        role: str,
        content: str,
        session_key: Optional[str] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> int:
        """Create a history message."""
        return self._backend.create_message(role, content, session_key, timestamp, metadata)

    def get_conversation_history(self, session_key: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get conversation history."""
        return self._backend.get_conversation_history(session_key, limit, offset)

    def clear_conversation_history(self) -> bool:
        """Clear conversation history."""
        return self._backend.clear_conversation_history()

    def count_messages(self) -> int:
        """Count history messages."""
        return self._backend.count_messages()

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about memories and history."""
        return self._backend.get_statistics()

    def find_memory_by_file_id(
        self, file_id: str, category: str = "drive_catalog"
    ) -> Optional[Dict[str, Any]]:
        return self.catalog_backend.find_memory_by_file_id(file_id, category=category)

    def upsert_memory_by_file_id(
        self,
        file_id: str,
        content: str,
        category: str = "drive_catalog",
        importance: float = 0.8,
        source: str = "drive_catalog",
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        # Drive catalog is local-only — no Supabase mirror.
        return self.catalog_backend.upsert_memory_by_file_id(
            file_id, content, category, importance, source, embedding, metadata
        )

    def delete_source_memories_missing_file_ids(
        self,
        source: str,
        keep_file_ids: set,
        category: str = "drive_catalog",
    ) -> int:
        return self.catalog_backend.delete_source_memories_missing_file_ids(
            source, keep_file_ids, category=category
        )

    def find_memory_by_message_id(
        self, message_id: str, category: str = "personal_index"
    ) -> Optional[Dict[str, Any]]:
        backend = self._write_backend_for(category=category, source="gmail")
        return backend.find_memory_by_message_id(message_id, category=category)

    def upsert_memory_by_message_id(
        self,
        message_id: str,
        content: str,
        category: str = "personal_index",
        importance: float = 0.7,
        source: str = "gmail",
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Upsert a Gmail personal_index row keyed by metadata.message_id (issue #617).

        Always local — Gmail activity is never mirrored to Supabase.
        """
        backend = self._write_backend_for(source=source, category=category)
        return backend.upsert_memory_by_message_id(
            message_id, content, category, importance, source, embedding, metadata
        )

    def vacuum(self) -> None:
        pass

    def close(self) -> None:
        pass
