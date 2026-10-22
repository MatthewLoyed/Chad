"""
Memory UI section definitions.

Maps Supabase `memories.category` values and knowledge-graph sources to
human-readable labels for the Settings → Memory & Context view.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class MemorySectionDef(TypedDict, total=False):
    id: str
    label: str
    description: str
    category: str
    source: str
    always_show: bool


# Ordered catalog — primary sections stay visible even when empty.
MEMORY_SECTION_DEFS: List[MemorySectionDef] = [
    {
        "id": "knowledge_graph",
        "label": "User Facts",
        "description": "Personal knowledge graph — durable facts Chad uses in conversation.",
        "source": "graph",
        "always_show": True,
    },
    {
        "id": "relationships",
        "label": "Relationships",
        "description": "Connections between facts in your knowledge graph.",
        "source": "graph_edges",
        "always_show": True,
    },
    {
        "id": "preference",
        "label": "Preferences",
        "description": "Durable preferences learned from your sessions.",
        "category": "preference",
        "always_show": True,
    },
    {
        "id": "approved_lessons",
        "label": "Approved Lessons",
        "description": "Reflection lessons you approved (or Chad auto-applied at high confidence).",
        "source": "reflection_lessons",
        "always_show": True,
    },
    {
        "id": "personal_index",
        "label": "Activity",
        "description": "Indexed browser and app activity (searchable by Chad).",
        "category": "personal_index",
        "always_show": True,
    },
    {
        "id": "session_log",
        "label": "Session Summaries",
        "description": "Compaction summaries from past conversations.",
        "category": "session_log",
    },
    {
        "id": "pending_tasks",
        "label": "Pending Tasks",
        "description": "Open tasks captured during consolidation.",
        "category": "pending_tasks",
    },
    {
        "id": "file_operations",
        "label": "File Operations",
        "description": "Recent file changes Chad has tracked.",
        "category": "file_operations",
    },
    {
        "id": "errors",
        "label": "Lessons & Errors",
        "description": "Failure patterns and lessons from past runs.",
        "category": "errors",
    },
    {
        "id": "general",
        "label": "General",
        "description": "Miscellaneous stored memories.",
        "category": "general",
    },
    {
        "id": "autonomous",
        "label": "Autonomous",
        "description": "Heartbeat and background agent notes.",
        "category": "autonomous",
    },
]

_KNOWN_CATEGORIES = {d["category"] for d in MEMORY_SECTION_DEFS if d.get("category")}


def label_for_category(category: str) -> str:
    """Human label for an unknown Supabase category slug."""
    for d in MEMORY_SECTION_DEFS:
        if d.get("category") == category:
            return d["label"]
    return category.replace("_", " ").title()


def extra_sections_from_stats(categories: Dict[str, int]) -> List[MemorySectionDef]:
    """Build sections for categories present in DB but not in the static catalog."""
    extras: List[MemorySectionDef] = []
    for cat, count in sorted(categories.items()):
        if cat in _KNOWN_CATEGORIES or count <= 0:
            continue
        extras.append({
            "id": f"cat_{cat}",
            "label": label_for_category(cat),
            "description": f"Memories stored under category “{cat}”.",
            "category": cat,
        })
    return extras


def section_defs_for_catalog(categories: Dict[str, int]) -> List[MemorySectionDef]:
    """Full ordered section list including dynamic extras."""
    return list(MEMORY_SECTION_DEFS) + extra_sections_from_stats(categories)
