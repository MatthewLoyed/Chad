"""
Curiosity Service - Autonomous Scavenger & Discovery Stash

Maintains a curated buffer of verified links (YouTube shorts, trending GitHub repos,
anime releases, and niche tech finds) discovered during background/nightly passes.
Provides fresh, verified content for Chad's spontaneous Telegram messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Dict, List, Optional
import aiohttp

from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.logger import setup_logger

logger = setup_logger("CuriosityService")

STASH_KEY = "curiosity:stash"
SENT_HISTORY_KEY = "curiosity:sent_history"
AFFINITY_KEY = "curiosity:affinity"
LAST_SCAVENGE_KEY = "curiosity:last_scavenge_ts"

DEFAULT_AFFINITY: Dict[str, float] = {
    "tech": 1.0,
    "anime": 1.0,
    "spikeball": 1.0,
    "meme": 1.0,
    "general": 1.0,
}


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


class CuriosityService:
    """Manages discovery, verification, and dispatch of interesting finds."""

    def __init__(self, state_repo: Optional[StateRepository] = None):
        self._state_repo = state_repo or StateRepository()
        from src.core.discovery.interest_tuning import InterestTuningEngine
        self._tuning = InterestTuningEngine(state_repo=self._state_repo)

    def _get_stash(self) -> List[Dict[str, Any]]:
        return self._state_repo.get_value(STASH_KEY, []) or []

    def _save_stash(self, stash: List[Dict[str, Any]]) -> None:
        self._state_repo.set_value(STASH_KEY, stash)

    def _get_sent_history(self) -> Dict[str, float]:
        return self._state_repo.get_value(SENT_HISTORY_KEY, {}) or {}

    def _save_sent_history(self, history: Dict[str, float]) -> None:
        self._state_repo.set_value(SENT_HISTORY_KEY, history)

    def get_affinity(self) -> Dict[str, float]:
        profile = self._tuning.get_profile()
        merged = dict(DEFAULT_AFFINITY)
        merged.update(profile.categories)
        return merged

    def record_feedback(self, text_or_reaction: str, category: Optional[str] = None) -> None:
        """Update category affinity weights based on user replies/reactions."""
        self._tuning.record_feedback(feedback_text=text_or_reaction, category=category)

    async def verify_url(self, url: str, timeout: float = 4.0) -> bool:
        """Verify an HTTP link exists and does not return 404 or connection error."""
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return False

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                # First try lightweight HEAD
                try:
                    async with session.head(url, headers=headers, allow_redirects=True) as resp:
                        if resp.status < 400:
                            return True
                        if resp.status == 405:  # Method Not Allowed -> fallback to GET
                            pass
                        elif resp.status in (404, 410):
                            return False
                except Exception:
                    pass

                # Fallback to streaming GET with small chunk read
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    return resp.status < 400

        except Exception as e:
            logger.debug(f"[ CURIOSITY ] URL verification failed for {url}: {e}")
            return False

    async def add_item(
        self,
        url: str,
        title: str,
        source: str = "web",
        category: str = "tech",
        summary: str = "",
        verify: bool = True,
    ) -> bool:
        """Add a discovered item to the stash after verification."""
        clean_url = url.strip()
        if not clean_url:
            return False

        sent_history = self._get_sent_history()
        if clean_url in sent_history:
            logger.debug(f"[ CURIOSITY ] Skipping already sent URL: {clean_url}")
            return False

        stash = self._get_stash()
        url_hash = _hash_url(clean_url)
        if any(item.get("id") == url_hash or item.get("url") == clean_url for item in stash):
            logger.debug(f"[ CURIOSITY ] Item already in stash: {clean_url}")
            return False

        if verify:
            is_valid = await self.verify_url(clean_url)
            if not is_valid:
                logger.warning(f"[ CURIOSITY ] Rejected invalid/404 URL: {clean_url}")
                return False

        new_item = {
            "id": url_hash,
            "url": clean_url,
            "title": title.strip() or clean_url,
            "source": source,
            "category": category,
            "summary": summary.strip(),
            "discovered_at": time.time(),
            "verified": True,
            "sent_at": None,
        }

        stash.append(new_item)
        self._save_stash(stash)
        logger.info(f"[ CURIOSITY ] Added item to stash: {title} ({clean_url})")
        return True

    def get_available_items(self, category: Optional[str] = None, max_age_hours: float = 48.0) -> List[Dict[str, Any]]:
        """Return verified, unsent items that have not expired."""
        now = time.time()
        max_age_seconds = max_age_hours * 3600.0
        stash = self._get_stash()
        sent_history = self._get_sent_history()

        valid_items = []
        for item in stash:
            url = item.get("url", "")
            sent_at = item.get("sent_at")
            discovered_at = item.get("discovered_at", 0.0)

            # Check if sent or expired
            if sent_at or url in sent_history:
                continue
            if now - discovered_at > max_age_seconds:
                continue
            if category and item.get("category") != category:
                continue

            valid_items.append(item)

        return valid_items

    def pop_next_curiosity(self, category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Select, mark as sent, and return the next highest-affinity curiosity item."""
        items = self.get_available_items(category=category)
        if not items:
            return None

        affinity = self.get_affinity()
        # Sort by category weight * recency
        items.sort(
            key=lambda x: affinity.get(x.get("category", "general"), 1.0),
            reverse=True,
        )

        selected = items[0]
        url = selected.get("url", "")

        # Mark sent in stash and history
        stash = self._get_stash()
        now = time.time()
        for it in stash:
            if it.get("url") == url:
                it["sent_at"] = now
                break
        self._save_stash(stash)

        sent_history = self._get_sent_history()
        sent_history[url] = now
        self._save_sent_history(sent_history)

        logger.info(f"[ CURIOSITY ] Popped curiosity item for delivery: {selected.get('title')}")
        return selected

    def prune_expired(self, max_age_hours: float = 48.0) -> int:
        """Remove old unsent items or items already sent older than 7 days."""
        now = time.time()
        stash = self._get_stash()
        max_age_seconds = max_age_hours * 3600.0
        keep_history_seconds = 7 * 86400.0

        pruned = 0
        new_stash = []
        for it in stash:
            sent_at = it.get("sent_at")
            disc = it.get("discovered_at", 0.0)
            if sent_at:
                if now - sent_at > keep_history_seconds:
                    pruned += 1
                    continue
            elif now - disc > max_age_seconds:
                pruned += 1
                continue
            new_stash.append(it)

        if pruned > 0:
            self._save_stash(new_stash)
            logger.info(f"[ CURIOSITY ] Pruned {pruned} expired items from stash")
        return pruned

    def get_status(self) -> Dict[str, Any]:
        """Summary of current curiosity buffer."""
        available = self.get_available_items()
        stash = self._get_stash()
        return {
            "available_count": len(available),
            "total_in_stash": len(stash),
            "affinity": self.get_affinity(),
            "categories": {it.get("category"): sum(1 for x in available if x.get("category") == it.get("category")) for it in available},
        }

    async def scavenge(self, custom_topics: Optional[List[Tuple[str, str, str]]] = None) -> int:
        """
        Execute an autonomous scavenging pass to find and verify fresh items.
        
        Leverages the multi-source ContentDiscoveryPipeline (YouTube shorts, tech articles,
        GitHub repos, anime releases) with interest tuning, link verification, and deduplication.
        """
        self.prune_expired()

        if custom_topics:
            from src.tools.web.web_search import WebSearchTool
            search_tool = WebSearchTool()
            added_count = 0

            for query, category, source in custom_topics:
                if len(self.get_available_items()) >= 10:
                    break

                try:
                    res = await search_tool.execute(query=query, limit=3, fetch_content=False)
                    raw_results = res.get("results") or []
                    results = []
                    for item in raw_results:
                        if isinstance(item, dict) and "sources" in item:
                            results.extend(item.get("sources") or [])
                        elif isinstance(item, dict):
                            results.append(item)

                    for r in results:
                        url = r.get("url") or ""
                        title = r.get("title") or ""
                        snippet = r.get("snippet") or r.get("content") or ""
                        if not url or "google.com" in url:
                            continue

                        if source == "github" and "github.com/" in url:
                            parts = url.split("github.com/")[-1].split("/")
                            if len(parts) >= 2:
                                url = f"https://github.com/{parts[0]}/{parts[1]}"
                        elif source == "youtube" and "/shorts/" in url:
                            url = url.split("&")[0].split("?")[0]

                        added = await self.add_item(
                            url=url,
                            title=title,
                            source=source,
                            category=category,
                            summary=snippet[:200],
                            verify=True,
                        )
                        if added:
                            added_count += 1
                            if len(self.get_available_items()) >= 10:
                                break
                except Exception as e:
                    logger.warning(f"[ CURIOSITY ] Custom scavenge query failed for '{query}': {e}")

            logger.info(f"[ CURIOSITY ] Custom scavenger pass completed. Added {added_count} items.")
            return added_count

        # Default: Use unified ContentDiscoveryPipeline
        from src.core.discovery.pipeline import ContentDiscoveryPipeline
        pipeline = ContentDiscoveryPipeline(tuning_engine=self._tuning, state_repo=self._state_repo)

        needed = max(1, 10 - len(self.get_available_items()))
        discovered = await pipeline.discover(
            limit_per_source=3,
            max_total_items=needed,
            verify=True,
            push_to_stash=True,
            push_to_dashboard=False,
        )
        logger.info(f"[ CURIOSITY ] Discovery pipeline run completed. Added {len(discovered)} items.")
        return len(discovered)

