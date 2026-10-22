from typing import List, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class MemoryFacadeMixin:
    """Mixin for memory-related facade methods."""

    def get_recent_memories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent memories.

        Args:
            limit: Maximum number of memories to return

        Returns:
            List of memory dictionaries
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return []

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'get_recent_short_term'):
            return []

        try:
            return memory_engine.get_recent_short_term(limit=limit)
        except Exception as e:
            logger.error(f"Failed to get recent memories: {e}")
            return []

    def search_memories(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search memories by query.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching memory dictionaries
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return []

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'search_memories'):
            return []

        try:
            return memory_engine.search_memories(query, limit=limit)
        except Exception as e:
            logger.error(f"Failed to search memories for '{query}': {e}")
            return []

    def clear_short_term_memory(self) -> bool:
        """
        Clear short-term memory.

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return False

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'clear_short_term'):
            return False

        try:
            # 1. Clear database short-term memory
            memory_engine.clear_short_term()

            # 2. Clear in-memory conversation history of active clients
            if self._text_mode_client:
                self._text_mode_client.clear_history()

            logger.info("Short-term memory and client history cleared successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to clear short-term memory: {e}")
            return False

    def get_memory_summary(self) -> Dict[str, Any]:
        """
        Get memory statistics summary.

        Returns:
            Dictionary with memory stats (short_term_count, long_term_count, etc.)
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return {"short_term_count": 0, "long_term_count": 0}

        memory_engine = self._assistant.memory
        if not memory_engine:
            return {"short_term_count": 0, "long_term_count": 0}

        try:
            short_term = len(memory_engine.get_recent_short_term(limit=1000))
            long_term = memory_engine.get_long_term_count() if hasattr(memory_engine, 'get_long_term_count') else 0

            return {
                "short_term_count": short_term,
                "long_term_count": long_term
            }
        except Exception as e:
            logger.error(f"Failed to get memory summary: {e}")
            return {"short_term_count": 0, "long_term_count": 0}

    def get_all_long_term_memories(self) -> List[Dict[str, Any]]:
        """
        Get all long-term memories.

        Returns:
            List of long-term memory dictionaries
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return []

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'get_recent_short_term'):
            return []

        try:
            return memory_engine.get_recent_short_term(limit=1000)
        except Exception as e:
            logger.error(f"Failed to get long-term memories: {e}")
            return []

    def delete_memory(self, memory_id: int) -> bool:
        """
        Delete a specific memory.

        Args:
            memory_id: ID of memory to delete

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return False

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'delete_memory'):
            return False

        try:
            memory_engine.delete_memory(memory_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            return False

    def delete_long_term_memory(self, memory_id: int) -> bool:
        """
        Delete a long-term memory.

        Args:
            memory_id: ID of memory to delete

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return False

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'delete_memory'):
            return False

        try:
            memory_engine.delete_memory(memory_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete long-term memory {memory_id}: {e}")
            return False

    def clear_all_memories(self) -> bool:
        """
        Clear all memories (short-term and long-term).

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return False

        memory_engine = self._assistant.memory
        if not memory_engine or not hasattr(memory_engine, 'purge_all_memories'):
            return False

        try:
            memory_engine.purge_all_memories()
            return True
        except Exception as e:
            logger.error(f"Failed to clear all memories: {e}")
            return False

    def save_memory(self, content: str, category: str = "general", importance: float = 0.5) -> int:
        """Add a new explicit short-term memory."""
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return -1
        try:
            return self._assistant.memory.save_memory(content, category=category, importance=importance)
        except Exception as e:
            logger.error(f"save_memory failed: {e}")
            return -1

    def update_memory_content(self, memory_id: int, content: str = None, category: str = None, importance: float = None) -> bool:
        """Update an existing memory's content."""
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return False
        try:
            return self._assistant.memory.update_memory(memory_id, content=content, category=category, importance=importance)
        except Exception as e:
            logger.error(f"update_memory failed: {e}")
            return False

    def semantic_search_memories(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Perform vectorized semantic search over long-term and short-term memories."""
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return []
        try:
            # Calls standard search which wraps formatting properly for UI
            return self._assistant.memory.search_memories(query, limit=limit)
        except Exception as e:
            logger.error(f"semantic_search failed: {e}")
            return []

    def get_storage_stats(self) -> Dict[str, Any]:
        """Retrieve detailed binary storage metrics (vector DB vs SQLite size)."""
        if not self._assistant or not hasattr(self._assistant, 'memory'):
            return {"error": "No memory engine"}
        try:
            return self._assistant.memory.get_storage_stats()
        except Exception as e:
            logger.error(f"get_storage_stats failed: {e}")
            return {"error": str(e)}

    async def get_memory_catalog(self, preview_limit: int = 6) -> Dict[str, Any]:
        """Grouped memory preview for the Settings → Memory UI."""
        from src.shared.memory_sections import section_defs_for_catalog

        stats = self.get_storage_stats()
        categories: Dict[str, int] = stats.get("categories") or {}
        sections_out: List[Dict[str, Any]] = []

        graph_client = None
        try:
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            graph_client = GraphRAGClient()
            await graph_client.initialize()
        except Exception as e:
            logger.error(f"get_memory_catalog graph init failed: {e}")

        for section_def in section_defs_for_catalog(categories):
            section_id = section_def["id"]
            source = section_def.get("source")
            category = section_def.get("category")
            always_show = section_def.get("always_show", False)

            if source == "graph" and graph_client:
                total = graph_client.count_active_facts()
                preview = graph_client.list_active_facts(limit=preview_limit, offset=0)
                deletable = False
            elif source == "graph_edges" and graph_client:
                total = graph_client.count_relationships()
                preview = graph_client.list_relationships(limit=preview_limit, offset=0)
                deletable = False
            elif source == "reflection_lessons":
                from src.core.intelligence.reflection_lessons_store import (
                    count_approved_lessons, get_approved_lessons, lesson_row_to_memory_item,
                )
                total = await count_approved_lessons()
                rows = await get_approved_lessons(limit=preview_limit, offset=0)
                preview = [lesson_row_to_memory_item(r) for r in rows]
                deletable = False
            elif category:
                total = categories.get(category, 0)
                preview = []
                if total > 0 and self._assistant and hasattr(self._assistant, "memory"):
                    preview = self._assistant.memory.list_memories_by_category(
                        category=category,
                        limit=preview_limit,
                        offset=0,
                    )
                deletable = True
            else:
                continue

            if not always_show and total <= 0:
                continue

            sections_out.append({
                "id": section_id,
                "label": section_def["label"],
                "description": section_def.get("description", ""),
                "total_count": total,
                "preview": preview,
                "deletable": deletable,
                "source": source or "supabase",
                "category": category,
            })

        return {"sections": sections_out}

    async def get_memory_section(
        self,
        section_id: str,
        offset: int = 0,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Paginated items for one Memory UI section."""
        from src.shared.memory_sections import section_defs_for_catalog

        stats = self.get_storage_stats()
        categories: Dict[str, int] = stats.get("categories") or {}
        section_def = next(
            (s for s in section_defs_for_catalog(categories) if s["id"] == section_id),
            None,
        )
        if not section_def:
            return {"items": [], "total_count": 0, "has_more": False}

        source = section_def.get("source")
        category = section_def.get("category")

        if source == "graph":
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            client = GraphRAGClient()
            await client.initialize()
            total = client.count_active_facts()
            items = client.list_active_facts(limit=limit, offset=offset)
            return {"items": items, "total_count": total, "has_more": offset + len(items) < total}

        if source == "graph_edges":
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            client = GraphRAGClient()
            await client.initialize()
            total = client.count_relationships()
            items = client.list_relationships(limit=limit, offset=offset)
            return {"items": items, "total_count": total, "has_more": offset + len(items) < total}

        if source == "reflection_lessons":
            from src.core.intelligence.reflection_lessons_store import (
                count_approved_lessons, get_approved_lessons, lesson_row_to_memory_item,
            )
            total = await count_approved_lessons()
            rows = await get_approved_lessons(limit=limit, offset=offset)
            items = [lesson_row_to_memory_item(r) for r in rows]
            return {"items": items, "total_count": total, "has_more": offset + len(items) < total}

        if category and self._assistant and hasattr(self._assistant, "memory"):
            total = categories.get(category, 0)
            items = self._assistant.memory.list_memories_by_category(
                category=category,
                limit=limit,
                offset=offset,
            )
            return {"items": items, "total_count": total, "has_more": offset + len(items) < total}

        return {"items": [], "total_count": 0, "has_more": False}
