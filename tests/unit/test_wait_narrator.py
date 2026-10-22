"""Unit tests for the resident-only wait-theater narrator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.wait_theater import narrator as mod


class TestParseLines:
    def test_json_array(self):
        raw = '["Checking your calendar…", "Pulling threads…", "Almost ready…"]'
        assert mod._parse_lines(raw) == [
            "Checking your calendar…",
            "Pulling threads…",
            "Almost ready…",
        ]

    def test_strips_markdown_fences(self):
        raw = '```json\n["First line", "Second line", "Third line"]\n```'
        assert mod._parse_lines(raw) == ["First line", "Second line", "Third line"]

    def test_deduplicates_and_caps(self):
        raw = json.dumps(["Alpha line here", "Alpha line here", "Beta line here", "Gamma", "Delta"])
        lines = mod._parse_lines(raw)
        assert lines[0] == "Alpha line here"
        assert len(lines) <= mod._MAX_LINES


@pytest.mark.asyncio
async def test_resolve_narrator_target_uses_selector_when_loaded():
    registry = MagicMock()
    registry._llm_selector = MagicMock(_model_id="qwen/qwen3.5-9b", _resolved_model_id=None)

    with patch.object(mod.ConfigManager, "get", return_value=True), patch.object(
        mod.ConfigManager, "get_local_provider_config", return_value={"base_url": "http://localhost:1234/v1"},
    ), patch.object(
        mod, "get_loaded_model_ids", new=AsyncMock(return_value=["qwen/qwen3.5-9b"]),
    ):
        target = await mod.resolve_narrator_target(registry)

    assert target == ("qwen/qwen3.5-9b", "http://localhost:1234/v1")


@pytest.mark.asyncio
async def test_resolve_narrator_target_ignores_voice_model_even_when_loaded():
    """Voice-lane models must not drive GUI wait narration."""
    registry = MagicMock()
    registry._llm_selector = MagicMock(_model_id="qwen/qwen3.5-9b", _resolved_model_id=None)

    with patch.object(mod.ConfigManager, "get", return_value=True), patch.object(
        mod.ConfigManager, "get_local_provider_config", return_value={"base_url": "http://localhost:1234/v1"},
    ), patch.object(
        mod, "get_loaded_model_ids", new=AsyncMock(return_value=["voice/fast-model"]),
    ):
        target = await mod.resolve_narrator_target(registry)

    assert target is None


@pytest.mark.asyncio
async def test_resolve_narrator_target_skips_when_nothing_loaded():
    registry = MagicMock()
    registry._llm_selector = MagicMock(_model_id="qwen/qwen3.5-9b", _resolved_model_id=None)

    with patch.object(mod.ConfigManager, "get", return_value=True), patch.object(
        mod.ConfigManager, "get_local_provider_config", return_value={"base_url": "http://localhost:1234/v1"},
    ), patch.object(
        mod, "get_loaded_model_ids", new=AsyncMock(return_value=[]),
    ):
        target = await mod.resolve_narrator_target(registry)

    assert target is None


@pytest.mark.asyncio
async def test_try_generate_never_calls_lifecycle():
    registry = MagicMock()
    registry._llm_selector = MagicMock(_model_id="qwen/qwen3.5-9b", _resolved_model_id=None)

    with patch.object(
        mod, "resolve_narrator_target", new=AsyncMock(return_value=("qwen/qwen3.5-9b", "http://localhost:1234/v1")),
    ), patch.object(
        mod, "generate_wait_narrator_lines", new=AsyncMock(return_value=["Line one", "Line two"]),
    ) as gen_mock, patch(
        "src.core.intelligence.model_lifecycle.ensure_resident", new=AsyncMock(),
    ) as ensure_mock:
        lines = await mod.try_generate_narrator_lines("find my emails", tool_registry=registry)

    assert lines == ["Line one", "Line two"]
    gen_mock.assert_awaited_once()
    ensure_mock.assert_not_called()


@pytest.mark.asyncio
async def test_try_generate_skips_without_resident_model():
    with patch.object(mod, "resolve_narrator_target", new=AsyncMock(return_value=None)), patch.object(
        mod, "generate_wait_narrator_lines", new=AsyncMock(),
    ) as gen_mock:
        lines = await mod.try_generate_narrator_lines("hello")

    assert lines is None
    gen_mock.assert_not_called()
