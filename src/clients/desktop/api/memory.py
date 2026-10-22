from src.application.commands import (
    GetMemoriesCommand,
    SearchMemoriesCommand,
    SaveMemoryCommand,
    UpdateMemoryCommand,
    DeleteMemoryCommand,
    ClearMemoriesCommand,
    GetStorageStatsCommand,
    SemanticSearchMemoriesCommand,
    GetMemoryCatalogCommand,
    GetMemorySectionCommand,
)


class MemoryAPIMixin:
    """Mixin for memory-related WebView API methods."""

    # UI list cap — full catalog can be 1000+ rows (e.g. personal_index); smaller
    # batches keep Supabase round-trips under the desktop relay timeout.
    _UI_MEMORY_LIMIT = 250

    def get_memories(self):
        """Get all memories for UI display."""
        if not self._get_command_handler():
            return None
        return self._execute_command(
            GetMemoriesCommand(limit=self._UI_MEMORY_LIMIT),
            timeout=15.0,
            default_fallback=[],
        )
    
    def search_memories(self, query):
        """Search memories by content or category."""
        if not query:
            return []
        return self._execute_command(SearchMemoriesCommand(query=query, limit=100), timeout=10.0, default_fallback=[])
    
    def add_memory(self, content, category="general"):
        """Add a new memory."""
        if not content:
            return {"success": False, "error": "Content cannot be empty"}
        memory_id = self._execute_command(SaveMemoryCommand(content=content, category=category), timeout=5.0, default_fallback=-1)
        return {"success": memory_id > 0, "id": memory_id} if memory_id != -1 else {"success": False, "error": "Memory engine unavailable"}
    
    def update_memory(self, memory_id, content):
        """Update an existing memory's content."""
        success = self._execute_command(UpdateMemoryCommand(memory_id=int(memory_id), content=content), timeout=5.0, default_fallback=False)
        return {"success": success}
    
    def delete_memory(self, memory_id):
        """Delete a single memory by ID."""
        success = self._execute_command(DeleteMemoryCommand(memory_id=int(memory_id)), timeout=2.0, default_fallback=False)
        return {"success": success}
    
    def purge_all_memories(self):
        """Delete ALL memories. Use with caution!"""
        return self._execute_command(ClearMemoriesCommand(memory_type="all"), timeout=5.0, default_fallback={"success": False, "deleted_count": 0})
    
    def get_memory_catalog(self):
        """Grouped memory sections for the Settings UI."""
        if not self._get_command_handler():
            return None
        return self._execute_command(
            GetMemoryCatalogCommand(preview_limit=6),
            timeout=15.0,
            default_fallback={"sections": []},
        )

    def get_memory_section(self, section_id, offset=0, limit=25):
        """Paginate one memory catalog section."""
        if not section_id:
            return {"items": [], "total_count": 0, "has_more": False}
        return self._execute_command(
            GetMemorySectionCommand(
                section_id=str(section_id),
                offset=int(offset),
                limit=int(limit),
            ),
            timeout=15.0,
            default_fallback={"items": [], "total_count": 0, "has_more": False},
        )

    def get_memory_stats(self):
        """Get memory storage statistics."""
        return self._execute_command(
            GetStorageStatsCommand(),
            timeout=10.0,
            default_fallback={"memory_count": 0, "db_size_bytes": 0, "size_percent": 0},
        )
    
    def semantic_search_memories(self, query):
        """Semantic search for memories by meaning using Gemini embeddings."""
        if not query:
            return []
        return self._execute_command(SemanticSearchMemoriesCommand(query=query, limit=10), timeout=10.0, default_fallback=[])
