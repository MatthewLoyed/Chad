"""
Domain models for Chad's Media & Content Discovery Pipeline.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ContentCategory(str, Enum):
    TECH = "tech"
    GITHUB = "github"
    ARTICLES = "articles"
    YOUTUBE_SHORTS = "youtube_shorts"
    ANIME = "anime"
    SPIKEBALL = "spikeball"
    MEME = "meme"
    GENERAL = "general"


class ContentSource(str, Enum):
    YOUTUBE = "youtube"
    GITHUB = "github"
    HACKERNEWS = "hackernews"
    RSS = "rss"
    ANILIST = "anilist"
    JIKAN = "jikan"
    WEB = "web"


def hash_url(url: str) -> str:
    """Generate deterministic 16-char identifier from a URL."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


@dataclass
class DiscoveryItem:
    """Represents a discovered and verified content item."""

    url: str
    title: str
    source: str
    category: str
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    discovered_at: float = field(default_factory=time.time)
    verified: bool = True
    quality_score: float = 1.0
    affinity_score: float = 1.0
    sent_at: Optional[float] = None
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = hash_url(self.url)
        self.url = self.url.strip()
        self.title = self.title.strip() or self.url
        self.summary = self.summary.strip()

    @property
    def final_score(self) -> float:
        """Composite ranking score: affinity_weight * quality_score."""
        return self.affinity_score * self.quality_score

    def to_dict(self) -> Dict[str, Any]:
        """Convert item to dictionary for serialization / persistence."""
        d = asdict(self)
        d["final_score"] = self.final_score
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DiscoveryItem:
        """Hydrate a DiscoveryItem from serialized dictionary."""
        cleaned = dict(data)
        cleaned.pop("final_score", None)
        return cls(
            id=cleaned.get("id", ""),
            url=cleaned.get("url", ""),
            title=cleaned.get("title", ""),
            source=cleaned.get("source", ContentSource.WEB.value),
            category=cleaned.get("category", ContentCategory.GENERAL.value),
            summary=cleaned.get("summary", ""),
            metadata=cleaned.get("metadata") or {},
            discovered_at=cleaned.get("discovered_at", time.time()),
            verified=cleaned.get("verified", True),
            quality_score=cleaned.get("quality_score", 1.0),
            affinity_score=cleaned.get("affinity_score", 1.0),
            sent_at=cleaned.get("sent_at"),
        )


@dataclass
class InterestProfile:
    """Represents user affinity preferences across categories and fine-grained topics."""

    categories: Dict[str, float] = field(default_factory=lambda: {
        ContentCategory.TECH.value: 1.0,
        ContentCategory.GITHUB.value: 1.2,
        ContentCategory.ARTICLES.value: 1.1,
        ContentCategory.YOUTUBE_SHORTS.value: 1.0,
        ContentCategory.ANIME.value: 1.1,
        ContentCategory.SPIKEBALL.value: 1.3,
        ContentCategory.MEME.value: 1.0,
        ContentCategory.GENERAL.value: 1.0,
    })
    topics: Dict[str, float] = field(default_factory=lambda: {
        "local_llm": 1.4,
        "ai_agents": 1.5,
        "spikeball": 1.4,
        "attack_on_titan": 1.2,
        "jujutsu_kaisen": 1.2,
        "rust": 1.2,
        "python": 1.1,
        "cli_tools": 1.2,
    })
    blocked_domains: List[str] = field(default_factory=list)
    decay_rate: float = 0.05
    last_updated_at: float = field(default_factory=time.time)
    feedback_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InterestProfile:
        if not data:
            return cls()
        return cls(
            categories=data.get("categories") or cls().categories,
            topics=data.get("topics") or cls().topics,
            blocked_domains=data.get("blocked_domains") or [],
            decay_rate=data.get("decay_rate", 0.05),
            last_updated_at=data.get("last_updated_at", time.time()),
            feedback_history=data.get("feedback_history") or [],
        )
