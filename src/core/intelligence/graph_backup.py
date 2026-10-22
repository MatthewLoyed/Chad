"""Optional Supabase backup for personal knowledge-graph facts.

Local files are ALWAYS source of truth (`personal_knowledge.md` ->
`knowledge_graph.json`). This is the one opt-in cloud mirror in the memory stack,
enabled with `knowledge_graph.storage: local_then_supabase`, and it covers
identity facts only — bulk Activity (Chrome / Gmail / filesystem / Drive catalog)
never leaves the machine. See docs/guides/PRIVATE_DATA.md.

Split out of graph_rag_client.py: backing the graph up somewhere else is a
different job from owning it, and it was the only thing dragging cloud
infrastructure imports into the graph module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("GraphBackup")


def knowledge_graph_storage_mode() -> str:
    """local | local_then_supabase | supabase — same semantics as memory.storage."""
    try:
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.database.supabase_manager import get_supabase_manager

        configured = ConfigManager.get("knowledge_graph.storage", None)
        if configured is not None and str(configured).strip():
            return str(configured).strip().lower()
        if get_supabase_manager().enabled:
            return "local_then_supabase"
    except Exception:
        pass
    return "local"


def mirror_facts_to_supabase(graph) -> None:
    """Best-effort cloud copy when knowledge_graph.storage is local_then_supabase.

    Local files remain source of truth. Mirror writes go to Supabase only (never
    duplicate into local sqlite memories) so Activity search stays clean.
    """
    if knowledge_graph_storage_mode() != "local_then_supabase":
        return
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        from src.infrastructure.repositories.memory_backends import SupabaseMemoryBackend
        import hashlib

        sb = get_supabase_manager()
        if not sb.enabled:
            return
        cloud = SupabaseMemoryBackend(sb)
        mirrored = 0
        for nid, data in graph.nodes(data=True):
            if not data.get("valid", True):
                continue
            content = (data.get("content") or "").strip()
            if not content or is_placeholder_text(content):
                continue
            item_hash = hashlib.md5(f"kg:{nid}:{content}".encode("utf-8")).hexdigest()
            if cloud.has_memory_with_hash(item_hash):
                continue
            mid = cloud.create_memory(
                content=content,
                category="knowledge_graph",
                importance=0.9,
                source="personal_knowledge",
                embedding=None,
                session_key=None,
                metadata={
                    "hash": item_hash,
                    "graph_node_id": str(nid),
                    "topic": data.get("topic"),
                },
            )
            if mid != -1:
                mirrored += 1
        if mirrored:
            logger.info(f"[ GRAPH ] Mirrored {mirrored} fact(s) to Supabase.")
    except Exception as e:
        logger.warning(f"[ GRAPH ] Supabase mirror skipped: {e}")
