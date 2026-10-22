"""
GitHub Repositories Discovery Provider.

Discovers trending, high-signal open-source repositories matching user interests:
- AI agents and orchestrators
- Local LLM tooling and inference
- Rust and Python developer utilities and CLI tools
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import aiohttp

from src.core.discovery.models import ContentCategory, ContentSource, DiscoveryItem, InterestProfile
from src.core.discovery.providers.base import BaseDiscoveryProvider
from src.shared.config_manager import ConfigManager


GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


class GitHubReposProvider(BaseDiscoveryProvider):
    """Provider for discovering emerging and trending GitHub repositories."""

    DEFAULT_QUERIES = [
        ("topic:ai-agent OR topic:agentic-ai stars:>50", ContentCategory.GITHUB.value),
        ("topic:llm OR topic:local-ai language:python stars:>100", ContentCategory.TECH.value),
        ("language:python topic:developer-tools OR topic:cli stars:>100", ContentCategory.GITHUB.value),
        ("language:typescript topic:ai-tools OR topic:developer-tools stars:>100", ContentCategory.TECH.value),
    ]

    def __init__(self):
        super().__init__(
            name="github_repos",
            supported_categories=[
                ContentCategory.GITHUB.value,
                ContentCategory.TECH.value,
            ],
        )

    def _get_headers(self) -> Dict[str, str]:
        """Prepare request headers with optional GitHub token for higher rate limits."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Chad-Discovery-Pipeline/1.0",
        }
        token = (
            os.getenv("GITHUB_TOKEN")
            or os.getenv("GH_TOKEN")
            or ConfigManager.get("github.token", None)
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def fetch(
        self,
        limit: int = 5,
        interest_profile: Optional[InterestProfile] = None,
        category: Optional[str] = None,
    ) -> List[DiscoveryItem]:
        """Fetch trending / recently updated GitHub repositories."""
        items: List[DiscoveryItem] = []
        seen_urls = set()

        # Compute 90 days ago cutoff for freshness
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")

        queries_to_run = list(self.DEFAULT_QUERIES)
        # Prioritize queries based on interest profile if available
        if interest_profile:
            queries_to_run.sort(
                key=lambda q: interest_profile.categories.get(q[1], 1.0),
                reverse=True,
            )

        headers = self._get_headers()
        timeout = aiohttp.ClientTimeout(total=5.0)

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                for base_query, cat in queries_to_run:
                    if len(items) >= limit:
                        break

                    # Append freshness filter
                    full_query = f"{base_query} pushed:>{cutoff_date}"
                    params = {
                        "q": full_query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": min(10, limit * 2),
                    }

                    try:
                        async with session.get(GITHUB_SEARCH_URL, params=params) as resp:
                            if resp.status == 403:
                                self.logger.warning("GitHub API rate limit reached.")
                                break
                            if resp.status != 200:
                                continue

                            data = await resp.json()
                            repo_list = data.get("items") or []

                            for r in repo_list:
                                html_url = r.get("html_url") or ""
                                if not html_url or html_url in seen_urls:
                                    continue
                                seen_urls.add(html_url)

                                full_name = r.get("full_name") or ""
                                desc = r.get("description") or "Open source project"
                                stars = r.get("stargazers_count", 0)
                                forks = r.get("forks_count", 0)
                                lang = r.get("language") or "Code"
                                topics = r.get("topics") or []

                                # Quality score scales from 0.7 to 1.0 based on star count
                                quality = min(1.0, 0.7 + (min(stars, 5000) / 16000.0))

                                item = DiscoveryItem(
                                    url=html_url,
                                    title=f"{full_name} ({lang})",
                                    source=ContentSource.GITHUB.value,
                                    category=cat,
                                    summary=f"⭐ {stars:,} | {desc}",
                                    metadata={
                                        "stars": stars,
                                        "forks": forks,
                                        "language": lang,
                                        "topics": topics[:5],
                                        "owner": r.get("owner", {}).get("login"),
                                    },
                                    quality_score=quality,
                                )
                                items.append(item)
                                if len(items) >= limit:
                                    break

                    except Exception as e:
                        self.logger.debug(f"GitHub query failed '{base_query}': {e}")

        except Exception as e:
            self.logger.warning(f"GitHub provider fetch error: {e}")

        return items[:limit]
