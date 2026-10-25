"""
Content Discovery Pipeline - Unified Multi-Source Ingestion & Recommendation Engine.

Coordinates discovery providers across:
- YouTube Shorts (Spikeball, tech tricks, anime edits)
- Tech Articles (Hacker News top stories, arXiv, tech blogs)
- GitHub Repos (trending agentic AI, local LLMs, Rust/Python tooling)
- Anime Releases (airing episodes, seasonal drops via AniList/Jikan)

Applies adaptive interest tuning, link verification, deduplication, and
pushes high-affinity discoveries to both Chad's Curiosity Stash and Dashboard Feed.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from src.core.discovery.interest_tuning import InterestTuningEngine
from src.core.discovery.models import (
    ContentCategory,
    DiscoveryItem,
    InterestProfile,
    hash_url,
)
from src.core.discovery.providers import (
    AnimeReleasesProvider,
    BaseDiscoveryProvider,
    GitHubReposProvider,
    TechArticlesProvider,
    YouTubeShortsProvider,
)
from src.infrastructure.repositories.state_repository import StateRepository
from src.shared.logger import setup_logger

logger = setup_logger("ContentDiscoveryPipeline")

STASH_KEY = "curiosity:stash"
SENT_HISTORY_KEY = "curiosity:sent_history"


class ContentDiscoveryPipeline:
    """Orchestrates multi-source content scavenging, scoring, and routing."""

    def __init__(
        self,
        tuning_engine: Optional[InterestTuningEngine] = None,
        state_repo: Optional[StateRepository] = None,
        providers: Optional[List[BaseDiscoveryProvider]] = None,
    ):
        self._state_repo = state_repo or StateRepository()
        self._tuning = tuning_engine or InterestTuningEngine(state_repo=self._state_repo)

        if providers is not None:
            self._providers = providers
        else:
            self._providers = [
                YouTubeShortsProvider(),
                TechArticlesProvider(),
                GitHubReposProvider(),
                AnimeReleasesProvider(),
            ]

    @property
    def tuning_engine(self) -> InterestTuningEngine:
        return self._tuning

    def _get_stash(self) -> List[Dict[str, Any]]:
        return self._state_repo.get_value(STASH_KEY, []) or []

    def _save_stash(self, stash: List[Dict[str, Any]]) -> None:
        self._state_repo.set_value(STASH_KEY, stash)

    def _get_sent_history(self) -> Dict[str, float]:
        return self._state_repo.get_value(SENT_HISTORY_KEY, {}) or {}

    def is_known_url(self, url: str) -> bool:
        """Check if URL was already sent or is already sitting in the stash."""
        clean_url = url.strip()
        url_id = hash_url(clean_url)
        if clean_url in self._get_sent_history():
            return True
        stash = self._get_stash()
        return any(item.get("id") == url_id or item.get("url") == clean_url for item in stash)

    async def discover(
        self,
        categories: Optional[List[str]] = None,
        limit_per_source: int = 4,
        max_total_items: int = 12,
        verify: bool = True,
        push_to_stash: bool = True,
        push_to_dashboard: bool = False,
    ) -> List[DiscoveryItem]:
        """
        Execute an end-to-end multi-source discovery cycle.

        Args:
            categories: Optional list of category filters.
            limit_per_source: Max raw candidates to fetch per provider.
            max_total_items: Upper bound on final accepted items.
            verify: Whether to perform live HEAD/GET HTTP verification.
            push_to_stash: Persist accepted items to Chad's Curiosity Stash.
            push_to_dashboard: Also post items to Supabase dashboard_entries.

        Returns:
            List of ranked and verified DiscoveryItem instances.
        """
        profile = self._tuning.get_profile()
        requested_cats = set(categories) if categories else None

        # Filter active providers matching requested categories
        active_providers = []
        for p in self._providers:
            if not requested_cats:
                active_providers.append(p)
            elif any(cat in requested_cats for cat in p.supported_categories):
                active_providers.append(p)

        logger.info(
            f"[ DISCOVERY ] Initiating pipeline pass across {len(active_providers)} provider(s) "
            f"(target categories: {categories or 'ALL'})."
        )

        # 1. Concurrent Ingestion from providers
        fetch_tasks = [
            p.fetch(
                limit=limit_per_source,
                interest_profile=profile,
                category=categories[0] if (categories and len(categories) == 1) else None,
            )
            for p in active_providers
        ]
        provider_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        candidates: List[DiscoveryItem] = []
        for res in provider_results:
            if isinstance(res, list):
                candidates.extend(res)
            elif isinstance(res, Exception):
                logger.warning(f"[ DISCOVERY ] Provider error: {res}")

        # 2. Deduplication & Domain Filtering
        fresh_candidates: List[DiscoveryItem] = []
        for item in candidates:
            if self._tuning.is_domain_blocked(item.url):
                logger.debug(f"[ DISCOVERY ] Dropping blocked domain: {item.url}")
                continue
            if self.is_known_url(item.url):
                logger.debug(f"[ DISCOVERY ] Dropping already known URL: {item.url}")
                continue
            fresh_candidates.append(item)

        # 3. Dynamic Interest Scoring & Ranking
        for item in fresh_candidates:
            self._tuning.score_item(item)

        # Sort descending by composite final_score (affinity * quality)
        fresh_candidates.sort(key=lambda it: it.final_score, reverse=True)

        # 4. Verification Check
        verified_items: List[DiscoveryItem] = []
        verify_tasks = []
        verifier_provider = self._providers[0] if self._providers else None

        for item in fresh_candidates:
            if len(verified_items) + len(verify_tasks) >= max_total_items:
                break
            if verify and verifier_provider:
                verify_tasks.append(self._verify_and_collect(verifier_provider, item))
            else:
                item.verified = True
                verified_items.append(item)

        if verify_tasks:
            verified_results = await asyncio.gather(*verify_tasks)
            verified_items.extend([it for it in verified_results if it is not None])

        # 5. Routing: Stash and/or Dashboard Feed
        if push_to_stash and verified_items:
            self._save_to_stash(verified_items)

        if push_to_dashboard and verified_items:
            await self._push_to_dashboard_feed(verified_items)

        logger.info(
            f"[ DISCOVERY ] Pipeline finished. Evaluated {len(candidates)} candidates, "
            f"verified and accepted {len(verified_items)} high-affinity items."
        )
        return verified_items

    async def _verify_and_collect(
        self,
        provider: BaseDiscoveryProvider,
        item: DiscoveryItem,
    ) -> Optional[DiscoveryItem]:
        """Verify URL and return item if valid, else None."""
        is_live = await provider.verify_url(item.url)
        if is_live:
            item.verified = True
            return item
        logger.warning(f"[ DISCOVERY ] Rejected non-responsive URL: {item.url}")
        return None

    def _save_to_stash(self, items: List[DiscoveryItem]) -> int:
        """Append fresh items to Chad's Curiosity Stash."""
        stash = self._get_stash()
        added_count = 0
        for item in items:
            url_hash = item.id or hash_url(item.url)
            if any(it.get("id") == url_hash or it.get("url") == item.url for it in stash):
                continue
            stash.append(item.to_dict())
            added_count += 1

        if added_count > 0:
            self._save_stash(stash)
            logger.info(f"[ DISCOVERY ] Persisted {added_count} items to curiosity stash.")
        return added_count

    async def _push_to_dashboard_feed(self, items: List[DiscoveryItem]) -> int:
        """Publish top items to Supabase dashboard_entries under kind='discovery'."""
        try:
            from src.core.intelligence.dashboard_feed import append_entry
            pushed = 0
            for it in items[:3]:  # Push top 3 to keep dashboard clean
                title = it.title
                from src.core.discovery.feed_preview import compact_preview
                body = compact_preview(it.summary)
                meta = {
                    **it.metadata,
                    "source": it.source,
                    "category": it.category,
                    "url": it.url,
                    "score": it.final_score,
                }
                action = {
                    "type": "open_url",
                    "url": it.url,
                    "label": "Open Link",
                }
                res = await append_entry(
                    kind="discovery",
                    title=title,
                    body=body,
                    metadata=meta,
                    action=action,
                    expires_in_hours=72.0,
                )
                if res:
                    pushed += 1
            logger.info(f"[ DISCOVERY ] Published {pushed} items to Dashboard Feed.")
            return pushed
        except Exception as e:
            logger.warning(f"[ DISCOVERY ] Failed to push to dashboard feed: {e}")
            return 0

    def get_pipeline_status(self) -> Dict[str, Any]:
        """Summary of current pipeline status, stash count, and interest tuning."""
        stash = self._get_stash()
        sent_history = self._get_sent_history()
        profile = self._tuning.get_profile()

        now = time.time()
        available = [
            it for it in stash
            if not it.get("sent_at") and it.get("url") not in sent_history and (now - it.get("discovered_at", 0)) < 172800
        ]

        cat_counts: Dict[str, int] = {}
        for it in available:
            cat = it.get("category", "general")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        return {
            "available_items_count": len(available),
            "total_stashed": len(stash),
            "sent_history_count": len(sent_history),
            "category_distribution": cat_counts,
            "interest_profile": {
                "categories": profile.categories,
                "top_topics": sorted(profile.topics.items(), key=lambda x: x[1], reverse=True)[:8],
                "blocked_domains": profile.blocked_domains,
            },
            "providers": [p.name for p in self._providers],
        }
