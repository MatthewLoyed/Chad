"""Sync, mtime-cached reads of the always-on singleton graph nodes.

Split out of graph_rag_client.py, which owns the graph and had reached the
repo's module-size ceiling. These two nodes are the odd ones out in that module:
everything else there is async and goes through `initialize()`, while these are
read **synchronously on the wake path**, once per LLM call, straight off the
graph JSON.

  ## _core_profile  identity facts injected into every user-facing prompt
  ## _behavior      standing directives about how Chad should act

Both are injected verbatim and never retrieved, so they can't be ranked out or
truncated away. Going through `initialize()` here would re-parse the whole graph
on every prompt build, hence the file-mtime cache.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("GraphRAGClient")

CORE_PROFILE_NODE_ID = "core_profile:main"
BEHAVIOR_NODE_ID = "behavior:main"

# ((path, mtime), {node_id: value}) — module-level so every prompt build shares
# one parse. The path is part of the key so a second graph (tests, probes) can't
# be served a stale value from the first.
_CACHE: tuple[Optional[tuple[str, float]], dict] = (None, {})


def max_chars() -> int:
    """Hard cap on an always-on block, so it can't grow unbounded across cycles.

    This is always-on context — it is paid for on every single prompt build, not
    just when retrieval happens to select it, which is why it has a cap at all.
    """
    from src.shared.config_manager import ConfigManager

    return int(ConfigManager.get("knowledge_graph.core_profile_max_chars", 800) or 800)


def reset_cache() -> None:
    """Drop the cached parse. For tests/probes that swap the graph underneath."""
    global _CACHE
    _CACHE = (None, {})


def read_always_on(node_id: str) -> Optional[str]:
    """Read one always-on singleton node, or None if it was never written.

    None is the normal answer on a fresh install, or when personal_knowledge.md
    has no such section — callers treat it as "omit the block", never an error.
    """
    global _CACHE

    from src.shared.config_manager import ConfigManager
    from src.shared.paths import resolve_path

    if not ConfigManager.get("knowledge_graph.enabled", True):
        return None
    try:
        path = resolve_path(
            ConfigManager.get("knowledge_graph.graph_path", "data/knowledge_graph.json")
        )
        mtime = os.path.getmtime(path)
    except OSError:
        return None

    cached_key, cached_values = _CACHE
    if cached_key == (path, mtime):
        return cached_values.get(node_id)

    wanted = {CORE_PROFILE_NODE_ID, BEHAVIOR_NODE_ID}
    values: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for node in raw.get("nodes", []):
            nid = node.get("id")
            if nid not in wanted or not node.get("valid", True):
                continue
            values[nid] = (node.get("content") or "").strip() or None
    except (OSError, ValueError) as e:
        logger.debug(f"[ GRAPH ] Always-on node read failed: {e}")
        return None

    _CACHE = ((path, mtime), values)
    return values.get(node_id)
