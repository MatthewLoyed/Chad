"""Unit tests for RenderAnimationTool."""

import pytest
from src.tools.visual.render_animation import RenderAnimationTool, MODES, shutdown_render_animation


def test_render_animation_modes():
    assert "video" in MODES
    assert "mp4" in MODES
    assert "frame" in MODES
    assert "filmstrip" in MODES
    assert "diff" in MODES


def test_render_animation_schema():
    tool = RenderAnimationTool()
    schema = tool.get_schema()
    assert schema["name"] == "render_animation"
    mode_prop = schema["parameters"]["properties"]["mode"]
    assert "video" in mode_prop["enum"]
    assert "mp4" in mode_prop["enum"]


def test_format_action():
    tool = RenderAnimationTool()
    assert tool.format_action({"mode": "video", "title": "Intro"}) == 'Rendering animation video "Intro"'
    assert tool.format_action({"mode": "mp4", "title": "Chart"}) == 'Rendering animation video "Chart"'
    assert tool.format_action({"mode": "frame", "title": "Test"}) == 'Rendering animation frame "Test"'


@pytest.mark.asyncio
async def test_render_animation_video_mode():
    tool = RenderAnimationTool()
    html = """<!DOCTYPE html>
<html>
<body style="margin:0;background:#0f172a;display:flex;align-items:center;justify-content:center;height:100vh;color:white;font-family:sans-serif;">
  <h1 style="animation: pulse 0.5s infinite alternate;">Chad Motion</h1>
  <style>
    @keyframes pulse {
      from { transform: scale(1); }
      to { transform: scale(1.1); color: #38bdf8; }
    }
  </style>
</body>
</html>"""
    try:
        result = await tool.execute(
            html=html,
            mode="video",
            duration_ms=600,
            viewport_width=320,
            viewport_height=240,
            title="test_motion",
        )
        assert result.get("success") is True
        assert result.get("format") in {"mp4", "webm"}
        assert result.get("path") is not None
        assert result.get("filename", "").endswith(f".{result['format']}")
        assert "download_token" in result
    finally:
        await shutdown_render_animation()
