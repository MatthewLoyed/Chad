"""
YouTube Shorts Discovery Provider.

Discovers viral, high-energy YouTube Shorts across Spikeball, Tech/AI,
Anime highlights, and humor matching user interest tuning.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.core.discovery.models import ContentCategory, ContentSource, DiscoveryItem, InterestProfile
from src.core.discovery.providers.base import BaseDiscoveryProvider


SHORTS_PATTERN = re.compile(r"(?:shorts\/|v=|youtu\.be\/)([a-zA-Z0-9_-]{11})")


class YouTubeShortsProvider(BaseDiscoveryProvider):
    """Provider for discovering curated YouTube Shorts."""

    DEFAULT_TOPICS = [
        ("site:youtube.com/shorts spikeball crazy rally trick shot", ContentCategory.SPIKEBALL.value),
        ("site:youtube.com/shorts roundnet tournament highlights", ContentCategory.SPIKEBALL.value),
        ("site:youtube.com/shorts death note reaction funny OR hype", ContentCategory.ANIME.value),
        ("site:youtube.com/shorts my hero academia reaction clip", ContentCategory.ANIME.value),
        ("site:youtube.com/shorts attack on titan viral clip OR jujutsu kaisen edit", ContentCategory.ANIME.value),
        ("site:youtube.com/shorts python ai agent terminal trick", ContentCategory.TECH.value),
    ]

    def __init__(self):
        super().__init__(
            name="youtube_shorts",
            supported_categories=[
                ContentCategory.YOUTUBE_SHORTS.value,
                ContentCategory.SPIKEBALL.value,
            ],
        )

    def extract_short_id(self, url: str) -> Optional[str]:
        """Extract 11-char YouTube ID from URL."""
        if not url:
            return None
        match = SHORTS_PATTERN.search(url)
        return match.group(1) if match else None

    def build_canonical_url(self, video_id: str) -> str:
        """Construct canonical shorts URL."""
        return f"https://www.youtube.com/shorts/{video_id}"

    async def fetch(
        self,
        limit: int = 5,
        interest_profile: Optional[InterestProfile] = None,
        category: Optional[str] = None,
    ) -> List[DiscoveryItem]:
        """Fetch candidate YouTube shorts using web search integration."""
        items: List[DiscoveryItem] = []
        seen_ids = set()

        topics_to_query = []
        if category:
            # Filter default topics by requested category or generate a targeted query
            for q, cat in self.DEFAULT_TOPICS:
                if cat == category:
                    topics_to_query.append((q, cat))
            if not topics_to_query:
                topics_to_query.append((f"site:youtube.com/shorts {category}", category))
        else:
            topics_to_query = list(self.DEFAULT_TOPICS)

        # Re-rank queries if interest profile is provided
        if interest_profile and not category:
            topics_to_query.sort(
                key=lambda t: interest_profile.categories.get(t[1], 1.0),
                reverse=True,
            )

        try:
            from src.tools.web.web_search import WebSearchTool
            search_tool = WebSearchTool()
        except Exception as e:
            self.logger.warning(f"WebSearchTool unavailable: {e}")
            return items

        for query, cat in topics_to_query:
            if len(items) >= limit:
                break

            try:
                res = await search_tool.execute(query=query, limit=3, fetch_content=False)
                if res.get("success") is False:
                    self.logger.debug(f"Search backend unavailable: {res.get('error')}")
                    break
                raw_results = res.get("results") or []
                sources = []
                for entry in raw_results:
                    if isinstance(entry, dict) and "sources" in entry:
                        sources.extend(entry.get("sources") or [])
                    elif isinstance(entry, dict):
                        sources.append(entry)

                for src in sources:
                    raw_url = src.get("url") or ""
                    title = src.get("title") or "YouTube Short"
                    snippet = src.get("snippet") or src.get("content") or ""

                    video_id = self.extract_short_id(raw_url)
                    if not video_id or video_id in seen_ids:
                        continue
                    seen_ids.add(video_id)

                    canonical_url = self.build_canonical_url(video_id)
                    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                    item = DiscoveryItem(
                        url=canonical_url,
                        title=title,
                        source=ContentSource.YOUTUBE.value,
                        category=cat,
                        summary=snippet[:240],
                        metadata={
                            "video_id": video_id,
                            "thumbnail_url": thumbnail_url,
                            "embed_url": f"https://www.youtube.com/embed/{video_id}",
                            "is_short": True,
                        },
                        quality_score=0.9,
                    )
                    items.append(item)
                    if len(items) >= limit:
                        break

            except Exception as e:
                self.logger.debug(f"Search failed for query '{query}': {e}")

        return items
