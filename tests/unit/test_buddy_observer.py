"""Tests for buddy persona, comments, and observer."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.application.events.event_bus import EventBus
from src.buddy import BuddyEngine
from src.buddy.capture import ScreenGrab
from src.buddy.observer import BuddyObserver
from src.buddy.persona import build_buddy_persona_prompt


def test_buddy_persona_fallback_without_registry():
    from src.infrastructure.repositories.prompt_registry import PromptRegistry

    registry = PromptRegistry.get_instance()
    registry._loaded = False
    prompt = build_buddy_persona_prompt()
    assert "Chad" in prompt
    assert "desktop buddy" in prompt.lower()


def test_buddy_persona_uses_identity_and_user_blocks():
    from src.infrastructure.repositories.prompt_registry import PromptRegistry

    registry = PromptRegistry.get_instance()
    registry._prompts = {
        "identity": "I am Chad, a purple AI buddy.",
        "user": "The user builds AI tools and drinks too much coffee.",
    }
    registry._scopes = {"identity": "always", "user": "always"}
    registry._loaded = True

    prompt = build_buddy_persona_prompt()
    assert "purple AI buddy" in prompt
    assert "AI tools" in prompt
    assert "desktop buddy glance" in prompt


def test_buddy_comment_in_snapshot():
    bus = EventBus()
    engine = BuddyEngine(bus)
    engine.set_comment("Still grinding on that refactor!", "timer", display_s=30.0)
    payload = engine.get_state()
    assert payload["comment"]["text"] == "Still grinding on that refactor!"
    assert payload["comment"]["rule"] == "timer"


@pytest.mark.asyncio
async def test_observer_sets_comment_from_vision():
    bus = EventBus()
    engine = BuddyEngine(bus)

    vision = AsyncMock()
    vision.model_id = "qwen/qwen3.5-9b"
    vision.comment_on_screen = AsyncMock(return_value="VS Code again — respect.")

    async def _fake_capture(**_kwargs):
        return ScreenGrab(image_b64="dGVzdA==", width=10, height=10, source="test")

    config = {
        "enabled": True,
        "cooldown_s": 0,
        "skip_unchanged": False,
        "comment_display_s": 20,
    }
    observer = BuddyObserver(bus, engine, config, vision_client=vision)

    with patch("src.buddy.observer.capture_desktop", _fake_capture):
        await observer.observe("test")

    snap = engine.get_snapshot()
    assert snap.comment is not None
    assert snap.comment.text == "VS Code again — respect."
    vision.comment_on_screen.assert_awaited_once()


def test_observer_respects_cooldown():
    async def _run():
        bus = EventBus()
        engine = BuddyEngine(bus)
        vision = AsyncMock()
        vision.model_id = "qwen/qwen3.5-9b"
        vision.comment_on_screen = AsyncMock(return_value="Hey.")

        async def _fake_capture(**_kwargs):
            return ScreenGrab(image_b64="x", width=1, height=1, source="t")

        config = {"enabled": True, "cooldown_s": 9999, "skip_unchanged": False}
        observer = BuddyObserver(bus, engine, config, vision_client=vision)
        observer._last_observe_at = __import__("time").monotonic()

        with patch("src.buddy.observer.capture_desktop", _fake_capture):
            await observer.observe("test")
        vision.comment_on_screen.assert_not_awaited()

    asyncio.run(_run())
