"""
Tech Articles Discovery Provider.

Discovers high-signal tech articles, papers, and blog posts from:
- Hacker News (Firebase REST API: topstories & beststories)
- Curated RSS/Atom feeds (arXiv cs.AI, Lobsters, Simon Willison)
"""

from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
import aiohttp

from src.core.discovery.models import ContentCategory, ContentSource, DiscoveryItem, InterestProfile
from src.core.discovery.providers.base import BaseDiscoveryProvider


HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_BEST_URL = "https://hacker-news.firebaseio.com/v0/beststories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

CURATED_FEEDS = [
    {
        "name": "arXiv cs.AI",
        "url": "https://rss.arxiv.org/rss/cs.AI",
        "category": ContentCategory.TECH.value,
        "format": "rss",
    },
    {
        "name": "Lobsters",
        "url": "https://lobste.rs/rss",
        "category": ContentCategory.ARTICLES.value,
        "format": "rss",
    },
    {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/entries/",
        "category": ContentCategory.TECH.value,
        "format": "atom",
    },
]


class TechArticlesProvider(BaseDiscoveryProvider):
    """Provider for discovering tech articles from Hacker News and RSS."""

    def __init__(self):
        super().__init__(
            name="tech_articles",
            supported_categories=[
                ContentCategory.ARTICLES.value,
                ContentCategory.TECH.value,
            ],
        )

    async def fetch(
        self,
        limit: int = 5,
        interest_profile: Optional[InterestProfile] = None,
        category: Optional[str] = None,
    ) -> List[DiscoveryItem]:
        """Fetch tech articles across Hacker News and RSS feeds."""
        items: List[DiscoveryItem] = []

        # 1. Fetch from Hacker News
        hn_limit = max(2, limit // 2)
        hn_items = await self._fetch_hacker_news(limit=hn_limit)
        items.extend(hn_items)

        # 2. Fetch from RSS Feeds if needed
        remaining = limit - len(items)
        if remaining > 0:
            rss_items = await self._fetch_rss_feeds(limit=remaining)
            items.extend(rss_items)

        return items[:limit]

    async def _fetch_hacker_news(self, limit: int = 3) -> List[DiscoveryItem]:
        """Fetch top Hacker News articles with scores >= 30."""
        items: List[DiscoveryItem] = []
        timeout = aiohttp.ClientTimeout(total=5.0)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(HN_TOP_URL) as resp:
                    if resp.status != 200:
                        return items
                    story_ids = await resp.json()
                    if not isinstance(story_ids, list):
                        return items

                # Fetch details for the first 15 candidate IDs in parallel
                candidate_ids = story_ids[:15]
                fetch_tasks = [self._fetch_hn_item(session, sid) for sid in candidate_ids]
                details = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                for d in details:
                    if isinstance(d, dict) and d.get("url"):
                        score = d.get("score", 0)
                        if score < 30:
                            continue

                        title = d.get("title") or "Tech Story"
                        item_id = d.get("id")
                        hn_url = f"https://news.ycombinator.com/item?id={item_id}"
                        comments = d.get("descendants", 0)

                        media = {}
                        preview = await self._article_preview(session, d["url"], media)
                        if not preview:
                            continue

                        item = DiscoveryItem(
                            url=d["url"],
                            title=title,
                            source=ContentSource.HACKERNEWS.value,
                            category=ContentCategory.ARTICLES.value,
                            summary=preview,
                            metadata={
                                **media,
                                "hn_id": item_id,
                                "hn_discussion_url": hn_url,
                                "points": score,
                                "comments_count": comments,
                                "author": d.get("by"),
                                "content_type": "source_excerpt",
                            },
                            quality_score=min(1.0, 0.5 + (score / 400.0)),
                        )
                        items.append(item)
                        if len(items) >= limit:
                            break

        except Exception as e:
            self.logger.warning(f"Hacker News fetch failed: {e}")

        return items

    async def _article_preview(self, session: aiohttp.ClientSession, url: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Read the source; popularity is discovery evidence, not article content."""
        from src.core.discovery.feed_preview import compact_preview, source_image
        from src.tools.web.content_extraction import extract_from_html

        if not url.startswith(("https://", "http://")):
            return ""
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    return ""
                markup = await response.text()
                extracted = extract_from_html(markup)
            if not extracted.ok:
                return ""
            if metadata is not None:
                metadata["image_url"] = source_image(markup, url)
            return compact_preview(extracted.content)
        except Exception as exc:
            self.logger.debug("Article preview unavailable for %s: %s", url, exc)
            return ""

    @staticmethod
    def _feed_preview(text: str) -> str:
        from src.core.discovery.feed_preview import compact_preview

        plain = html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()
        from src.tools.web.content_extraction import passes_quality_gate
        if not passes_quality_gate(plain)[0]:
            return ""
        return compact_preview(plain)

    async def _fetch_hn_item(self, session: aiohttp.ClientSession, item_id: int) -> Optional[Dict[str, Any]]:
        """Fetch single HN story payload."""
        try:
            url = HN_ITEM_URL.format(id=item_id)
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return None

    async def _fetch_rss_feeds(self, limit: int = 3) -> List[DiscoveryItem]:
        """Fetch and parse curated RSS/Atom entries."""
        items: List[DiscoveryItem] = []
        timeout = aiohttp.ClientTimeout(total=4.0)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for feed in CURATED_FEEDS:
                if len(items) >= limit:
                    break
                try:
                    async with session.get(feed["url"]) as resp:
                        if resp.status != 200:
                            continue
                        content = await resp.text()

                    feed_items = self._parse_feed_xml(content, feed)
                    for item in feed_items:
                        if not item.summary:
                            item.summary = await self._article_preview(session, item.url, item.metadata)
                        if item.summary:
                            items.append(item)
                except Exception as e:
                    self.logger.debug(f"Feed {feed['name']} fetch failed: {e}")

        return items[:limit]

    def _parse_feed_xml(self, xml_text: str, feed_meta: Dict[str, Any]) -> List[DiscoveryItem]:
        """Parse RSS or Atom XML content safely."""
        from src.core.discovery.feed_preview import source_image
        parsed_items: List[DiscoveryItem] = []
        try:
            root = ET.fromstring(xml_text)
            # RSS 2.0 format (<channel><item>...</item></channel>)
            for item_elem in root.findall(".//item")[:3]:
                title = item_elem.findtext("title") or ""
                link = item_elem.findtext("link") or ""
                desc = item_elem.findtext("description") or ""

                if link and title:
                    parsed_items.append(
                        DiscoveryItem(
                            url=link.strip(),
                            title=title.strip(),
                            source=ContentSource.RSS.value,
                            category=feed_meta.get("category", ContentCategory.ARTICLES.value),
                            summary=self._feed_preview(desc),
                            metadata={"feed_name": feed_meta.get("name"), "content_type": "source_excerpt", "image_url": source_image(desc, link, embedded=True)},
                            quality_score=0.85,
                        )
                    )

            # Atom format (<entry>...</entry>)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry_elem in root.findall(".//atom:entry", ns)[:3]:
                title = entry_elem.findtext("atom:title", namespaces=ns) or ""
                link_elem = entry_elem.find("atom:link", namespaces=ns)
                link = link_elem.get("href") if link_elem is not None else ""
                summary = entry_elem.findtext("atom:summary", namespaces=ns) or ""

                if link and title:
                    parsed_items.append(
                        DiscoveryItem(
                            url=link.strip(),
                            title=title.strip(),
                            source=ContentSource.RSS.value,
                            category=feed_meta.get("category", ContentCategory.ARTICLES.value),
                            summary=self._feed_preview(summary),
                            metadata={"feed_name": feed_meta.get("name"), "content_type": "source_excerpt", "image_url": source_image(summary, link, embedded=True)},
                            quality_score=0.85,
                        )
                    )
        except Exception as e:
            self.logger.debug(f"XML parse error for feed {feed_meta.get('name')}: {e}")

        return parsed_items
