"""
Discovery Providers package.
"""

from src.core.discovery.providers.base import BaseDiscoveryProvider
from src.core.discovery.providers.youtube_shorts import YouTubeShortsProvider
from src.core.discovery.providers.tech_articles import TechArticlesProvider
from src.core.discovery.providers.github_repos import GitHubReposProvider
from src.core.discovery.providers.anime_releases import AnimeReleasesProvider

__all__ = [
    "BaseDiscoveryProvider",
    "YouTubeShortsProvider",
    "TechArticlesProvider",
    "GitHubReposProvider",
    "AnimeReleasesProvider",
]
