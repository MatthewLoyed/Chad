"""
Unit Tests for Chad's Media & Content Discovery Pipeline.

Tests:
1. Discovery models and scoring (models.py)
2. Interest tuning, taste calibration, and sentiment feedback (interest_tuning.py)
3. Specialized providers (YouTube shorts, tech articles, GitHub repos, anime releases)
4. Pipeline orchestration, deduplication, verification, and stashing (pipeline.py)
5. ManageCuriosity tool discovery actions
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.discovery.interest_tuning import InterestTuningEngine
from src.core.discovery.models import (
    ContentCategory,
    ContentSource,
    DiscoveryItem,
    InterestProfile,
    hash_url,
)
from src.core.discovery.pipeline import ContentDiscoveryPipeline
from src.core.discovery.providers.anime_releases import AnimeReleasesProvider
from src.core.discovery.providers.base import BaseDiscoveryProvider
from src.core.discovery.providers.github_repos import GitHubReposProvider
from src.core.discovery.providers.tech_articles import TechArticlesProvider
from src.core.discovery.providers.youtube_shorts import YouTubeShortsProvider
from src.tools.system.utilities.manage_curiosity import ManageCuriosity


class InMemoryStateRepository:
    """Mock state repository for isolated testing."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def get_value(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set_value(self, key: str, value: Any) -> bool:
        self._data[key] = value
        return True


# --- Models & Scoring Tests -------------------------------------------------- #

def test_discovery_item_creation_and_hashing():
    url = "https://www.youtube.com/shorts/abc123xyz89"
    item = DiscoveryItem(
        url=url,
        title="Epic Spikeball Cut Shot",
        source=ContentSource.YOUTUBE.value,
        category=ContentCategory.SPIKEBALL.value,
        summary="Insane cut shot rally in roundnet nationals.",
        quality_score=0.9,
        affinity_score=1.4,
    )

    assert item.id == hash_url(url)
    assert len(item.id) == 16
    assert item.final_score == pytest.approx(1.26, 0.01)

    d = item.to_dict()
    assert d["url"] == url
    assert d["final_score"] == pytest.approx(1.26, 0.01)

    hydrated = DiscoveryItem.from_dict(d)
    assert hydrated.url == item.url
    assert hydrated.title == item.title
    assert hydrated.affinity_score == 1.4


def test_interest_profile_serialization():
    profile = InterestProfile()
    assert profile.categories[ContentCategory.SPIKEBALL.value] >= 1.0
    assert "local_llm" in profile.topics

    d = profile.to_dict()
    hydrated = InterestProfile.from_dict(d)
    assert hydrated.decay_rate == profile.decay_rate
    assert hydrated.topics["local_llm"] == profile.topics["local_llm"]


# --- Interest Tuning Engine Tests -------------------------------------------- #

def test_interest_tuning_weights_and_clamping():
    repo = InMemoryStateRepository()
    engine = InterestTuningEngine(state_repo=repo)

    # Test category tuning with clamping
    engine.tune_category("anime", 2.5)
    assert engine.get_profile().categories["anime"] == 2.5

    engine.tune_category("anime", 5.0)  # Should clamp to 3.0
    assert engine.get_profile().categories["anime"] == 3.0

    engine.tune_category("anime", -1.0)  # Should clamp to 0.1
    assert engine.get_profile().categories["anime"] == 0.1

    # Test topic tuning
    engine.tune_topic("local_llm", 2.0)
    assert engine.get_profile().topics["local_llm"] == 2.0


def test_interest_tuning_feedback_sentiment():
    repo = InMemoryStateRepository()
    engine = InterestTuningEngine(state_repo=repo)

    initial_anime = engine.get_profile().categories["anime"]

    # Positive feedback
    res_pos = engine.record_feedback("yo this is straight fire 🔥 based banger", category="anime", topic="attack_on_titan")
    assert res_pos["applied"] is True
    assert res_pos["sentiment"] == "positive"
    assert engine.get_profile().categories["anime"] == pytest.approx(initial_anime + 0.2, 0.01)
    assert "attack_on_titan" in engine.get_profile().topics

    # Negative feedback
    res_neg = engine.record_feedback("nah this is mid and cringe, boring skip", category="anime")
    assert res_neg["applied"] is True
    assert res_neg["sentiment"] == "negative"
    assert engine.get_profile().categories["anime"] == pytest.approx(initial_anime, 0.01)


def test_interest_tuning_domain_blocking():
    repo = InMemoryStateRepository()
    engine = InterestTuningEngine(state_repo=repo)

    assert engine.block_domain("clickbait-spam.com") is True
    assert engine.is_domain_blocked("https://clickbait-spam.com/article/123") is True
    assert engine.is_domain_blocked("https://github.com/astral-sh/uv") is False

    item = DiscoveryItem(
        url="https://clickbait-spam.com/story",
        title="Bad spam",
        source="web",
        category="tech",
    )
    assert engine.score_item(item) == 0.0

    engine.unblock_domain("clickbait-spam.com")
    assert engine.is_domain_blocked("https://clickbait-spam.com/story") is False


def test_interest_tuning_item_scoring():
    repo = InMemoryStateRepository()
    engine = InterestTuningEngine(state_repo=repo)

    engine.tune_category("tech", 1.5)
    engine.tune_topic("local_llm", 1.8)

    # Item matching both category and topic
    item = DiscoveryItem(
        url="https://github.com/test/local-llm-tool",
        title="Local LLM lightweight runner",
        source="github",
        category="tech",
        summary="Blazing fast local llm inference.",
    )
    score = engine.score_item(item)
    # Expected: category (1.5) * topic (1.8) = 2.7
    assert score == pytest.approx(2.7, 0.05)


def test_interest_tuning_time_decay():
    repo = InMemoryStateRepository()
    engine = InterestTuningEngine(state_repo=repo)

    engine.tune_category("anime", 2.0)
    assert engine.get_profile().categories["anime"] == 2.0

    engine.apply_time_decay(elapsed_days=14.0)
    decayed = engine.get_profile().categories["anime"]
    # Weight should move toward 1.0 (neutral)
    assert 1.0 < decayed < 2.0


# --- Provider Unit Tests ------------------------------------------------------ #

def test_youtube_shorts_provider_url_extraction():
    provider = YouTubeShortsProvider()
    assert provider.extract_short_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert provider.extract_short_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert provider.extract_short_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert provider.build_canonical_url("dQw4w9WgXcQ") == "https://www.youtube.com/shorts/dQw4w9WgXcQ"


@pytest.mark.asyncio
async def test_youtube_shorts_provider_fetch():
    provider = YouTubeShortsProvider()
    mock_search_res = {
        "success": True,
        "results": [
            {
                "sources": [
                    {
                        "url": "https://www.youtube.com/shorts/AbCdEfGhIjK",
                        "title": "Insane Spikeball Rally",
                        "snippet": "Crazy defense and trick shot finish.",
                    }
                ]
            }
        ]
    }

    mock_search_tool = MagicMock()
    mock_search_tool.execute = AsyncMock(return_value=mock_search_res)

    with patch("src.tools.web.web_search.WebSearchTool", return_value=mock_search_tool):
        items = await provider.fetch(limit=1, category="spikeball")
        assert len(items) == 1
        assert items[0].url == "https://www.youtube.com/shorts/AbCdEfGhIjK"
        assert items[0].metadata["video_id"] == "AbCdEfGhIjK"
        assert "thumbnail_url" in items[0].metadata


def test_tech_articles_provider_rss_parsing():
    provider = TechArticlesProvider()
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Advances in Agentic LLM Reasoning</title>
          <link>https://arxiv.org/abs/2610.12345</link>
          <description>A new survey on multi-step reasoning.</description>
        </item>
      </channel>
    </rss>
    """
    parsed = provider._parse_feed_xml(sample_rss, {"name": "arXiv cs.AI", "category": "tech"})
    assert len(parsed) == 1
    assert parsed[0].title == "Advances in Agentic LLM Reasoning"
    assert parsed[0].url == "https://arxiv.org/abs/2610.12345"


@pytest.mark.asyncio
async def test_anime_releases_provider_formatting():
    provider = AnimeReleasesProvider()
    assert provider._format_time_until(7200) == "airing in 2h"
    assert provider._format_time_until(90000) == "airing in 1d 1h"
    assert provider._clean_description("<b>Epic</b> battle <br>starts now.") == "Epic battle starts now."


# --- Pipeline Orchestration Tests -------------------------------------------- #

class MockProvider(BaseDiscoveryProvider):
    def __init__(self, name: str, items: List[DiscoveryItem]):
        super().__init__(name=name, supported_categories=["tech", "anime"])
        self._mock_items = items

    async def fetch(self, limit: int = 5, interest_profile=None, category=None):
        return self._mock_items[:limit]


@pytest.mark.asyncio
async def test_pipeline_orchestration_flow():
    repo = InMemoryStateRepository()
    tuning = InterestTuningEngine(state_repo=repo)
    tuning.tune_category("tech", 1.5)

    item1 = DiscoveryItem(
        url="https://github.com/astral-sh/uv",
        title="uv - Python package manager",
        source="github",
        category="tech",
        summary="Extremely fast python manager.",
    )
    item2 = DiscoveryItem(
        url="https://github.com/spam/broken-repo",
        title="Broken link repo",
        source="github",
        category="tech",
    )

    mock_provider = MockProvider("mock_gh", [item1, item2])
    # Mock verify_url: item1 is live, item2 is broken
    mock_provider.verify_url = AsyncMock(side_effect=lambda url: "astral-sh" in url)

    pipeline = ContentDiscoveryPipeline(
        tuning_engine=tuning,
        state_repo=repo,
        providers=[mock_provider],
    )

    discovered = await pipeline.discover(limit_per_source=2, verify=True, push_to_stash=True)

    assert len(discovered) == 1
    assert discovered[0].url == "https://github.com/astral-sh/uv"
    # Matches category 'tech' (1.5) * topic 'python' (1.1) = 1.65
    assert discovered[0].affinity_score == pytest.approx(1.65, 0.05)

    # Check stash
    stash = repo.get_value("curiosity:stash", [])
    assert len(stash) == 1
    assert stash[0]["url"] == "https://github.com/astral-sh/uv"

    # Running pipeline again should skip duplicate item1
    discovered_again = await pipeline.discover(limit_per_source=2, verify=True, push_to_stash=True)
    assert len(discovered_again) == 0


# --- ManageCuriosity Tool Tests ----------------------------------------------- #

@pytest.mark.asyncio
async def test_manage_curiosity_tool_actions():
    tool = ManageCuriosity()
    tool.curiosity_service._state_repo = InMemoryStateRepository()
    tool.tuning_engine._state_repo = tool.curiosity_service._state_repo
    tool.pipeline._state_repo = tool.curiosity_service._state_repo

    # 1. Action: tune category
    res_tune = await tool.execute(action="tune", category="anime", weight=2.2)
    assert res_tune["success"] is True
    assert "2.2" in res_tune["message"]

    # 2. Action: get_interests
    res_interests = await tool.execute(action="get_interests")
    assert res_interests["success"] is True
    assert res_interests["categories"]["anime"] == 2.2

    # 3. Action: feedback
    res_fb = await tool.execute(action="feedback", feedback_text="that video was fire! 🔥", category="spikeball")
    assert res_fb["success"] is True
    assert res_fb["feedback_result"]["applied"] is True

    # 4. Action: status
    res_status = await tool.execute(action="status")
    assert res_status["success"] is True
    assert "pipeline_status" in res_status

@pytest.mark.asyncio
async def test_article_preview_reads_source_and_rejects_unavailable_page():
    provider = TechArticlesProvider()
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(return_value='<article><p>' + ('Useful source evidence about local note search. ' * 35) + '</p></article>')
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get.return_value = context
    preview = await provider._article_preview(session, 'https://example.com/article')
    assert 'Useful source evidence' in preview
    assert len(preview.split()) <= 91
    response.status = 403
    assert await provider._article_preview(session, 'https://example.com/blocked') == ''


def test_rss_preview_strips_markup_and_preserves_provenance():
    provider = TechArticlesProvider()
    text = '<rss><channel><item><title>Note search</title><link>https://example.com/notes</link><description>&lt;p&gt;Local &amp;amp; private note search with SQLite full text indexing.&lt;/p&gt;</description></item></channel></rss>'
    item = provider._parse_feed_xml(text, {'name': 'Example feed'})[0]
    assert item.summary == 'Local & private note search with SQLite full text indexing.'
    assert item.metadata['content_type'] == 'source_excerpt'


def test_feed_preview_rejects_comments_placeholder():
    assert TechArticlesProvider._feed_preview('Comments') == ''
    assert TechArticlesProvider._feed_preview('<p>Loading Loading Loading</p>') == ''
