"""Unit tests for play_youtube_music MVP tool."""

from unittest.mock import patch

import pytest

from src.tools.media.youtube_music import (
    PlayYouTubeMusicTool,
    music_playlist_url,
    music_watch_url,
    normalize_playlist_id,
    summarize_result,
)


def test_url_helpers():
    assert music_watch_url("dQw4w9WgXcQ") == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
    assert music_playlist_url("PLtest") == "https://music.youtube.com/playlist?list=PLtest"
    assert normalize_playlist_id("VLPLtest") == "PLtest"
    assert normalize_playlist_id("PLtest") == "PLtest"
    assert normalize_playlist_id(None) is None


def test_summarize_song():
    summary = summarize_result({
        "resultType": "song",
        "title": "Gurenge",
        "videoId": "CwkzjZ2ebZk",
        "artists": [{"name": "LiSA"}],
        "duration": "3:55",
    })
    assert summary["kind"] == "song"
    assert summary["video_id"] == "CwkzjZ2ebZk"
    assert summary["artists"] == "LiSA"
    assert summary["url"].endswith("CwkzjZ2ebZk")


def test_summarize_playlist():
    summary = summarize_result({
        "resultType": "playlist",
        "title": "Anime Mix",
        "browseId": "VLPLanime123",
        "artists": [],
    })
    assert summary["kind"] == "playlist"
    assert summary["playlist_id"] == "PLanime123"
    assert "playlist?list=PLanime123" in summary["url"]


def test_validate_requires_target():
    tool = PlayYouTubeMusicTool()
    ok, err = tool.validate_params()
    assert ok is False
    assert err


def test_validate_video_id_length():
    tool = PlayYouTubeMusicTool()
    ok, err = tool.validate_params(video_id="short")
    assert ok is False
    assert "11-character" in (err or "")


@pytest.mark.asyncio
async def test_play_opens_top_search_hit():
    tool = PlayYouTubeMusicTool()
    fake_hits = [{
        "resultType": "song",
        "title": "Anime Song",
        "videoId": "abcdefghijk",
        "artists": [{"name": "Artist"}],
    }]
    with patch.object(tool, "_search", return_value=fake_hits), \
         patch.object(tool, "_open_url", return_value=True) as open_url:
        result = await tool.execute(query="anime music", mode="play")
    assert result["success"] is True
    assert result["video_id"] == "abcdefghijk"
    assert "Anime Song" in result["result"]
    open_url.assert_awaited_once_with("https://music.youtube.com/watch?v=abcdefghijk")


@pytest.mark.asyncio
async def test_search_mode_does_not_open_browser():
    tool = PlayYouTubeMusicTool()
    fake_hits = [{
        "resultType": "song",
        "title": "Track A",
        "videoId": "abcdefghijk",
        "artists": [{"name": "A"}],
    }]
    with patch.object(tool, "_search", return_value=fake_hits), \
         patch.object(tool, "_open_url", return_value=True) as open_url:
        result = await tool.execute(query="lofi", mode="search")
    assert result["success"] is True
    assert "results" in result
    open_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_playlist_open():
    tool = PlayYouTubeMusicTool()
    with patch.object(tool, "_open_url", return_value=True) as open_url:
        result = await tool.execute(playlist_id="PLdirect99")
    assert result["success"] is True
    open_url.assert_awaited_once_with("https://music.youtube.com/playlist?list=PLdirect99")


@pytest.mark.asyncio
async def test_missing_ytmusicapi_import():
    tool = PlayYouTubeMusicTool()
    with patch.object(tool, "_search", side_effect=ImportError("nope")):
        result = await tool.execute(query="anime", mode="play")
    assert result["success"] is False
    assert "ytmusicapi" in result["error"]
