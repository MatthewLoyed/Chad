"""
Base class and utilities for discovery content providers.
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Dict, List, Optional
import aiohttp

from src.core.discovery.models import DiscoveryItem, InterestProfile
from src.shared.logger import setup_logger

logger = setup_logger("BaseDiscoveryProvider")


class BaseDiscoveryProvider(abc.ABC):
    """Abstract base class for content discovery providers."""

    def __init__(self, name: str, supported_categories: List[str]):
        self.name = name
        self.supported_categories = supported_categories
        self.logger = setup_logger(f"Provider:{name}")

    @abc.abstractmethod
    async def fetch(
        self,
        limit: int = 5,
        interest_profile: Optional[InterestProfile] = None,
        category: Optional[str] = None,
    ) -> List[DiscoveryItem]:
        """Fetch candidates from this content source."""
        pass

    async def verify_url(self, url: str, timeout: float = 4.0) -> bool:
        """
        Verify that an HTTP link exists and does not return 404 or connection error.
        Uses lightweight HEAD request with fallback to streaming GET.
        """
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
                # 1. Try lightweight HEAD
                try:
                    async with session.head(url, headers=headers, allow_redirects=True) as resp:
                        if resp.status < 400:
                            return True
                        if resp.status == 405:  # Method Not Allowed -> fall through to GET
                            pass
                        elif resp.status in (404, 410):
                            return False
                except Exception:
                    pass

                # 2. Fallback to streaming GET with small chunk read
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    return resp.status < 400

        except Exception as e:
            self.logger.debug(f"URL verification failed for {url}: {e}")
            return False
