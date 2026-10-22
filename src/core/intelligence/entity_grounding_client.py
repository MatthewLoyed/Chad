"""
Entity Grounding Client — Perplexity-style pre-turn grounding of external web entities.

Wired into the knowledge layer (GraphRAG injector) to run in parallel with
GraphRAG, ActivityRAG, and DriveCatalogClient on every turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.intelligence.entity_classifier import EntityClassifier, EntityClassification
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("EntityGroundingClient")

# Default persistent entity cache path
_DEFAULT_CACHE_PATH = Path("data/grounded_entities.json")

# Pre-seeded common SaaS / web services to avoid even a single network call for standard tools
_DEFAULT_GROUNDED_ENTITIES: Dict[str, Dict[str, Any]] = {
    "gemini notebook": {
        "title": "Google NotebookLM",
        "url": "https://notebooklm.google.com",
        "description": "AI-powered notebook and research workspace by Google (formerly Project Tailwind / Gemini Notebook / NotebookLM).",
        "is_web_service": True,
    },
    "gemini notebook .com": {
        "title": "Google NotebookLM",
        "url": "https://notebooklm.google.com",
        "description": "AI-powered notebook and research workspace by Google (formerly Project Tailwind / Gemini Notebook / NotebookLM).",
        "is_web_service": True,
    },
    "notebooklm": {
        "title": "Google NotebookLM",
        "url": "https://notebooklm.google.com",
        "description": "AI-powered research notebook app by Google for uploading sources and notes.",
        "is_web_service": True,
    },
    "supabase": {
        "title": "Supabase Dashboard",
        "url": "https://supabase.com/dashboard",
        "description": "Cloud platform dashboard for Postgres database, auth, and storage usage.",
        "is_web_service": True,
    },
    "notion": {
        "title": "Notion",
        "url": "https://www.notion.so",
        "description": "Connected workspace for notes, docs, and project management.",
        "is_web_service": True,
    },
    "chatgpt": {
        "title": "ChatGPT",
        "url": "https://chatgpt.com",
        "description": "OpenAI conversational AI web interface and workspaces.",
        "is_web_service": True,
    },
    "perplexity": {
        "title": "Perplexity AI",
        "url": "https://www.perplexity.ai",
        "description": "AI conversational search and answer engine.",
        "is_web_service": True,
    },
    "linear": {
        "title": "Linear",
        "url": "https://linear.app",
        "description": "Issue tracking and project planning tool for software teams.",
        "is_web_service": True,
    },
}


class EntityGroundingClient:
    """Async client that grounds candidate entities using local cache or fast web search."""

    def __init__(
        self,
        tool_registry: Optional[Any] = None,
        cache_path: Optional[Path] = None,
    ):
        self._tool_registry = tool_registry
        self._cache_path = cache_path or _DEFAULT_CACHE_PATH
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()

    def set_tool_registry(self, registry: Any) -> None:
        """Bind or update the tool registry instance."""
        self._tool_registry = registry

    def _load_cache(self) -> None:
        """Load persistent cache from disk, initializing with pre-seeded entities."""
        self._cache = dict(_DEFAULT_GROUNDED_ENTITIES)
        try:
            if self._cache_path.exists():
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                    if isinstance(stored, dict):
                        self._cache.update(stored)
        except Exception as exc:
            logger.warning(f"Could not load entity cache from {self._cache_path}: {exc}")

    def _save_cache(self) -> None:
        """Persist current entity cache to disk."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as exc:
            logger.warning(f"Could not save entity cache to {self._cache_path}: {exc}")

    async def search(self, query: str, domain: Optional[str] = None) -> Optional[str]:
        """
        Classify the query and return grounded entity facts if an external service is referenced.

        Returns a formatted bullet string or None.
        Runs in parallel with GraphRAG, DriveCatalog, and ActivityRAG.
        """
        if not ConfigManager.get("entity_grounding.enabled", True):
            return None

        classification = EntityClassifier.classify(query)
        if not classification.should_ground or not classification.entity:
            return None

        entity_key = classification.entity.lower().strip()
        logger.info(f"[ ENTITY_GROUNDING ] Detected web entity candidate: '{entity_key}' (reason: {classification.reason})")

        # 1. Cache hit check
        cached_info = self._cache.get(entity_key)
        if not cached_info:
            # Check without trailing .com / domains if present
            base_key = entity_key.split(".")[0].strip()
            cached_info = self._cache.get(base_key)

        if cached_info:
            logger.info(f"[ ENTITY_GROUNDING ] Cache hit for '{entity_key}'")
            return self._format_entity(classification.entity, cached_info)

        # 2. Fast web search fallback
        search_query = classification.search_query or classification.entity
        grounded_data = await self._fetch_live_grounding(entity_key, search_query)
        if grounded_data:
            self._cache[entity_key] = grounded_data
            self._save_cache()
            return self._format_entity(classification.entity, grounded_data)

        return None

    async def _fetch_live_grounding(self, entity_key: str, search_query: str) -> Optional[Dict[str, Any]]:
        """Run a fast, lightweight search (snippets only, 2 results max) to identify the entity."""
        if not self._tool_registry:
            logger.debug("[ ENTITY_GROUNDING ] No tool_registry available for live web search.")
            return None

        try:
            logger.info(f"[ ENTITY_GROUNDING ] Performing live pre-turn lookup for: '{search_query}'")
            # Run quick search without page extraction to keep latency under ~350ms
            res = await self._tool_registry.execute_tool(
                "web_search",
                {
                    "queries": [search_query],
                    "results_per_query": 2,
                    "fetch_content": False,
                },
            )
            if not isinstance(res, dict) or not res.get("success"):
                return None

            results_list = res.get("results", [])
            if not results_list or not isinstance(results_list[0], dict):
                return None

            sources = results_list[0].get("sources", [])
            if not sources:
                return None

            top = sources[0]
            title = top.get("title") or "Official Site"
            url = top.get("url") or ""
            desc = top.get("snippet") or top.get("description") or ""

            # Check second source if first is generic or search engine internal
            if ("google.com/search" in url or "bing.com" in url) and len(sources) > 1:
                top = sources[1]
                title = top.get("title") or title
                url = top.get("url") or url
                desc = top.get("snippet") or desc

            clean_desc = (desc[:160] + "...") if len(desc) > 160 else desc
            return {
                "title": title,
                "url": url,
                "description": clean_desc,
                "is_web_service": True,
            }
        except Exception as exc:
            logger.warning(f"[ ENTITY_GROUNDING ] Live lookup failed for '{search_query}': {exc}")
            return None

    def _format_entity(self, raw_entity: str, info: Dict[str, Any]) -> str:
        """Format the grounded entity context line."""
        title = info.get("title", "Web Service")
        url = info.get("url", "")
        desc = info.get("description", "")
        url_part = f" ({url})" if url else ""
        desc_part = f" — {desc}" if desc else ""
        return (
            f"- \"{raw_entity}\": {title}{url_part}{desc_part}\n"
            f"  (Note: This is an external web application/service, NOT a local file or Google Drive document.)"
        )
