"""Composer dictation silence / max-duration auto-stop (no real mic)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.clients.desktop.audio_settings_probe as probe_mod
from src.clients.desktop.audio_settings_probe import AudioSettingsProbe


@pytest.mark.asyncio
async def test_dictation_monitor_auto_stops_after_silence(monkeypatch):
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_TIMEOUT", 0.05)
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_POLL", 0.01)
    monkeypatch.setattr(probe_mod, "MAX_DICTATION_SECONDS", 30.0)

    probe = AudioSettingsProbe(window=None)
    probe._dictation_session = 7
    probe._dictation_stream = object()  # truthy sentinel
    probe._dictation_speech_started = True
    probe._dictation_last_voice_at = 0.0  # already "quiet" forever

    auto = AsyncMock()
    probe._auto_stop_dictation = auto  # type: ignore[method-assign]

    await asyncio.wait_for(probe._dictation_monitor(7), timeout=2.0)

    auto.assert_awaited_once_with("silence")


@pytest.mark.asyncio
async def test_dictation_monitor_ignores_silence_before_speech(monkeypatch):
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_TIMEOUT", 0.05)
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_POLL", 0.01)
    monkeypatch.setattr(probe_mod, "MAX_DICTATION_SECONDS", 0.12)

    probe = AudioSettingsProbe(window=None)
    probe._dictation_session = 3
    probe._dictation_stream = object()
    probe._dictation_speech_started = False
    probe._dictation_last_voice_at = 0.0

    auto = AsyncMock()
    probe._auto_stop_dictation = auto  # type: ignore[method-assign]

    await asyncio.wait_for(probe._dictation_monitor(3), timeout=2.0)

    auto.assert_awaited_once_with("max_duration")


@pytest.mark.asyncio
async def test_dictation_monitor_noop_when_session_ends(monkeypatch):
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_TIMEOUT", 5.0)
    monkeypatch.setattr(probe_mod, "DICTATION_SILENCE_POLL", 0.01)
    monkeypatch.setattr(probe_mod, "MAX_DICTATION_SECONDS", 30.0)

    probe = AudioSettingsProbe(window=None)
    probe._dictation_session = 1
    probe._dictation_stream = object()
    probe._dictation_speech_started = True
    probe._dictation_last_voice_at = time.monotonic()

    auto = AsyncMock()
    probe._auto_stop_dictation = auto  # type: ignore[method-assign]

    async def clear_stream():
        await asyncio.sleep(0.03)
        probe._dictation_stream = None

    await asyncio.gather(probe._dictation_monitor(1), clear_stream())
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_stop_pushes_transcribing_then_result():
    pushed = []
    window = SimpleNamespace(push_dictation_result=pushed.append)
    probe = AudioSettingsProbe(window=window)
    probe._dictation_session = 9
    probe._dictation_stream = object()

    final = {
        "success": True,
        "text": "hello there",
        "message": "",
        "session": 9,
        "reason": "silence",
    }

    async def fake_stop(*, reason: str = "manual", on_progress=None):
        probe._dictation_stream = None
        if on_progress:
            on_progress(
                {
                    "success": True,
                    "text": "",
                    "message": "",
                    "session": 9,
                    "reason": reason,
                    "status": "transcribing",
                }
            )
        return final

    probe.stop_dictation = fake_stop  # type: ignore[method-assign]

    await probe._auto_stop_dictation("silence")

    assert len(pushed) == 2
    assert pushed[0]["status"] == "transcribing"
    assert pushed[0]["session"] == 9
    assert pushed[1] == final


@pytest.mark.asyncio
async def test_auto_stop_skips_push_when_not_recording():
    pushed = []
    window = SimpleNamespace(push_dictation_result=pushed.append)
    probe = AudioSettingsProbe(window=window)
    probe._dictation_session = 4
    probe._dictation_stream = None

    await probe._auto_stop_dictation("silence")
    assert pushed == []
