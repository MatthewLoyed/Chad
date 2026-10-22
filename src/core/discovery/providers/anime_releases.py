"""
Anime Releases Discovery Provider.

Discovers trending seasonal anime releases, newly dropping episodes,
and schedule updates via:
- AniList GraphQL API (Primary: fast, robust, no rate-limit hurdles)
- Jikan v4 MyAnimeList REST API (Fallback)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
import aiohttp

from src.core.discovery.models import ContentCategory, ContentSource, DiscoveryItem, InterestProfile
from src.core.discovery.providers.base import BaseDiscoveryProvider


ANILIST_API_URL = "https://graphql.anilist.co"
JIKAN_SEASONS_URL = "https://api.jikan.moe/v4/seasons/now"

ANILIST_QUERY = """
query ($perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(status: RELEASING, type: ANIME, sort: POPULARITY_DESC) {
      id
      title {
        english
        romaji
      }
      siteUrl
      description(asHtml: false)
      genres
      averageScore
      nextAiringEpisode {
        episode
        timeUntilAiring
      }
      coverImage {
        large
        medium
      }
    }
  }
}
"""


class AnimeReleasesProvider(BaseDiscoveryProvider):
    """Provider for discovering seasonal anime releases and airing updates."""

    def __init__(self):
        super().__init__(
            name="anime_releases",
            supported_categories=[
                ContentCategory.ANIME.value,
            ],
        )

    def _format_time_until(self, seconds: Optional[int]) -> str:
        """Format seconds countdown to human readable string."""
        if not seconds or seconds <= 0:
            return "airing now"
        hours = seconds // 3600
        days = hours // 24
        if days > 0:
            return f"airing in {days}d {hours % 24}h"
        return f"airing in {hours}h"

    def _clean_description(self, desc: Optional[str]) -> str:
        """Strip HTML tags and trim description."""
        if not desc:
            return ""
        clean = re.sub(r"<[^>]+>", "", desc)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:240]

    async def fetch(
        self,
        limit: int = 5,
        interest_profile: Optional[InterestProfile] = None,
        category: Optional[str] = None,
    ) -> List[DiscoveryItem]:
        """Fetch current anime releases, attempting AniList first, then Jikan fallback."""
        items = await self._fetch_anilist(limit=limit)
        if items:
            return items

        # Fallback to Jikan if AniList is unreachable
        self.logger.info("AniList returned no items; falling back to Jikan API.")
        return await self._fetch_jikan(limit=limit)

    async def _fetch_anilist(self, limit: int = 5) -> List[DiscoveryItem]:
        """Fetch currently releasing anime from AniList GraphQL API."""
        items: List[DiscoveryItem] = []
        timeout = aiohttp.ClientTimeout(total=4.0)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "query": ANILIST_QUERY,
                    "variables": {"perPage": max(limit * 2, 8)},
                }
                async with session.post(ANILIST_API_URL, json=payload) as resp:
                    if resp.status != 200:
                        return items

                    data = await resp.json()
                    media_list = data.get("data", {}).get("Page", {}).get("media") or []

                    for m in media_list:
                        title_info = m.get("title") or {}
                        title = title_info.get("english") or title_info.get("romaji") or "Anime Release"
                        site_url = m.get("siteUrl") or ""
                        if not site_url:
                            continue

                        next_ep = m.get("nextAiringEpisode")
                        ep_text = ""
                        time_text = ""
                        if next_ep and isinstance(next_ep, dict):
                            ep_num = next_ep.get("episode")
                            time_until = next_ep.get("timeUntilAiring")
                            time_text = self._format_time_until(time_until)
                            ep_text = f" (Episode {ep_num} • {time_text})"

                        clean_summary = self._clean_description(m.get("description"))
                        genres = m.get("genres") or []
                        score = m.get("averageScore")
                        cover = (m.get("coverImage") or {}).get("medium")

                        item = DiscoveryItem(
                            url=site_url,
                            title=f"{title}{ep_text}",
                            source=ContentSource.ANILIST.value,
                            category=ContentCategory.ANIME.value,
                            summary=clean_summary,
                            metadata={
                                "anilist_id": m.get("id"),
                                "english_title": title_info.get("english"),
                                "romaji_title": title_info.get("romaji"),
                                "genres": genres,
                                "average_score": score,
                                "cover_image": cover,
                                "tags": genres,
                            },
                            quality_score=min(1.0, 0.7 + ((score or 70) / 300.0)),
                        )
                        items.append(item)
                        if len(items) >= limit:
                            break

        except Exception as e:
            self.logger.warning(f"AniList query failed: {e}")

        return items

    async def _fetch_jikan(self, limit: int = 5) -> List[DiscoveryItem]:
        """Fetch current season anime from Jikan v4 REST API."""
        items: List[DiscoveryItem] = []
        timeout = aiohttp.ClientTimeout(total=4.0)
        headers = {"User-Agent": "Chad-Discovery-Pipeline/1.0"}

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                params = {"limit": limit}
                async with session.get(JIKAN_SEASONS_URL, params=params) as resp:
                    if resp.status != 200:
                        return items

                    data = await resp.json()
                    anime_list = data.get("data") or []

                    for a in anime_list:
                        title = a.get("title_english") or a.get("title") or "Anime Release"
                        url = a.get("url") or ""
                        if not url:
                            continue

                        synopsis = self._clean_description(a.get("synopsis"))
                        score = a.get("score")
                        genres = [g.get("name") for g in (a.get("genres") or []) if isinstance(g, dict)]
                        image = (a.get("images", {}).get("jpg") or {}).get("image_url")

                        item = DiscoveryItem(
                            url=url,
                            title=title,
                            source=ContentSource.JIKAN.value,
                            category=ContentCategory.ANIME.value,
                            summary=synopsis,
                            metadata={
                                "mal_id": a.get("mal_id"),
                                "genres": genres,
                                "score": score,
                                "cover_image": image,
                                "tags": genres,
                            },
                            quality_score=min(1.0, 0.7 + ((score or 7.0) / 30.0)),
                        )
                        items.append(item)
                        if len(items) >= limit:
                            break

        except Exception as e:
            self.logger.warning(f"Jikan fallback failed: {e}")

        return items
