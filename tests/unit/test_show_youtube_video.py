"""Unit tests for show_youtube_video display tool."""

import pytest

from src.tools.media.show_youtube_video import (
    ShowYouTubeVideoTool,
    extract_youtube_video_id,
    youtube_display_payload,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_extract_youtube_video_id(value, expected):
    assert extract_youtube_video_id(value) == expected


@pytest.mark.asyncio
async def test_show_youtube_video_accepts_url():
    tool = ShowYouTubeVideoTool()
    result = await tool.execute(
        title="Python Tutorial Overview",
        url="https://www.youtube.com/watch?v=abc12345678",
    )
    assert result["success"] is True
    assert result["youtube_video_id"] == "abc12345678"
    assert result["youtube_title"] == "Python Tutorial Overview"
    assert youtube_display_payload(result) == {
        "youtube_video_id": "abc12345678",
        "youtube_title": "Python Tutorial Overview",
    }


@pytest.mark.asyncio
async def test_show_youtube_video_rejects_invalid():
    tool = ShowYouTubeVideoTool()
    result = await tool.execute(title="Bad", url="https://example.com/not-youtube")
    assert result["success"] is False
