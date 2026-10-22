"""
Memory-related commands
"""

from dataclasses import dataclass
from typing import Any, Optional, List, Dict
from .base_command import Command


@dataclass
class GetMemoriesCommand(Command):
    """Command to get recent memories."""

    limit: int = 1000

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate limit is positive."""
        if self.limit < 1:
            return (False, "Limit must be at least 1")
        return (True, None)

    async def execute(self, facade) -> List[Dict]:
        """Execute: get memories."""
        return facade.get_recent_memories(limit=self.limit)

    def __str__(self) -> str:
        return f"GetMemoriesCommand(limit={self.limit})"


@dataclass
class SearchMemoriesCommand(Command):
    """Command to search memories by query."""

    query: str
    limit: int = 100

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate query is not empty."""
        if not self.query or not self.query.strip():
            return (False, "Search query cannot be empty")
        if self.limit < 1:
            return (False, "Limit must be at least 1")
        return (True, None)

    async def execute(self, facade) -> List[Dict]:
        """Execute: search memories."""
        return facade.search_memories(self.query, limit=self.limit)

    def __str__(self) -> str:
        return f"SearchMemoriesCommand(query='{self.query[:20]}...', limit={self.limit})"


@dataclass
class DeleteMemoryCommand(Command):
    """Command to delete a specific memory."""

    memory_id: int

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate memory ID is positive."""
        if self.memory_id < 1:
            return (False, "Memory ID must be positive")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: delete memory."""
        return facade.delete_memory(self.memory_id)

    def __str__(self) -> str:
        return f"DeleteMemoryCommand(memory_id={self.memory_id})"


@dataclass
class DeleteLongTermMemoryCommand(Command):
    """Command to delete a long-term memory."""

    memory_id: int

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate memory ID is positive."""
        if self.memory_id < 1:
            return (False, "Memory ID must be positive")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: delete long-term memory."""
        return facade.delete_long_term_memory(self.memory_id)

    def __str__(self) -> str:
        return f"DeleteLongTermMemoryCommand(memory_id={self.memory_id})"


@dataclass
class ClearMemoriesCommand(Command):
    """Command to clear memories."""

    memory_type: str = "all"  # "short_term", "long_term", or "all"

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate memory type."""
        valid_types = ["short_term", "long_term", "all"]
        if self.memory_type not in valid_types:
            return (False, f"Memory type must be one of {valid_types}")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: clear memories."""
        if self.memory_type == "short_term":
            return facade.clear_short_term_memory()
        elif self.memory_type == "long_term":
            # Not exposed in facade yet - would need to add
            return False
        else:  # "all"
            return facade.clear_all_memories()

    def __str__(self) -> str:
        return f"ClearMemoriesCommand(memory_type='{self.memory_type}')"


@dataclass
class GetLongTermMemoriesCommand(Command):
    """Command to get all long-term memories."""

    async def execute(self, facade) -> List[Dict]:
        """Execute: get long-term memories."""
        return facade.get_all_long_term_memories()

    def __str__(self) -> str:
        return "GetLongTermMemoriesCommand()"


@dataclass
class GetMemorySummaryCommand(Command):
    """Command to get memory statistics."""

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: get memory summary."""
        return facade.get_memory_summary()

    def __str__(self) -> str:
        return "GetMemorySummaryCommand()"


@dataclass
class SaveMemoryCommand(Command):
    """Command to manually save a new memory."""

    content: str
    category: str = "general"
    importance: float = 0.5

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate content."""
        if not self.content or not self.content.strip():
            return (False, "Memory content cannot be empty")
        return (True, None)

    async def execute(self, facade) -> int:
        """Execute: save memory."""
        return facade.save_memory(content=self.content, category=self.category, importance=self.importance)

    def __str__(self) -> str:
        return f"SaveMemoryCommand(category='{self.category}')"


@dataclass
class UpdateMemoryCommand(Command):
    """Command to update an existing memory's content."""

    memory_id: int
    content: str = None
    category: str = None
    importance: float = None

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate memory ID and existence of updates."""
        if self.memory_id < 1:
            return (False, "Memory ID must be positive")
        if all(v is None for v in [self.content, self.category, self.importance]):
            return (False, "No updates specified")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: update memory."""
        return facade.update_memory_content(
            memory_id=self.memory_id,
            content=self.content,
            category=self.category,
            importance=self.importance
        )

    def __str__(self) -> str:
        return f"UpdateMemoryCommand(memory_id={self.memory_id})"


@dataclass
class SemanticSearchMemoriesCommand(Command):
    """Command to perform advanced search over memory system."""

    query: str
    limit: int = 5

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate search terms."""
        if not self.query or not self.query.strip():
            return (False, "Search query is required")
        return (True, None)

    async def execute(self, facade) -> List[Dict[str, Any]]:
        """Execute: search."""
        return facade.semantic_search_memories(query=self.query, limit=self.limit)

    def __str__(self) -> str:
        return f"SemanticSearchMemoriesCommand(query='{self.query[:20]}...')"


@dataclass
class GetStorageStatsCommand(Command):
    """Command to retrieve vector/disk storage size summaries."""

    async def execute(self, facade) -> Dict[str, Any]:
        """Execute: read disk stats."""
        return facade.get_storage_stats()

    def __str__(self) -> str:
        return "GetStorageStatsCommand()"


@dataclass
class GetMemoryCatalogCommand(Command):
    """Command to fetch grouped memory sections for the Settings UI."""

    preview_limit: int = 6

    def validate(self) -> tuple[bool, Optional[str]]:
        if self.preview_limit < 1:
            return (False, "preview_limit must be at least 1")
        return (True, None)

    async def execute(self, facade) -> Dict[str, Any]:
        return await facade.get_memory_catalog(preview_limit=self.preview_limit)

    def __str__(self) -> str:
        return f"GetMemoryCatalogCommand(preview_limit={self.preview_limit})"


@dataclass
class GetMemorySectionCommand(Command):
    """Command to paginate one memory section in the Settings UI."""

    section_id: str
    offset: int = 0
    limit: int = 25

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.section_id or not self.section_id.strip():
            return (False, "section_id is required")
        if self.offset < 0:
            return (False, "offset must be non-negative")
        if self.limit < 1:
            return (False, "limit must be at least 1")
        return (True, None)

    async def execute(self, facade) -> Dict[str, Any]:
        return await facade.get_memory_section(
            section_id=self.section_id,
            offset=self.offset,
            limit=self.limit,
        )

    def __str__(self) -> str:
        return f"GetMemorySectionCommand(section_id='{self.section_id}', offset={self.offset})"
