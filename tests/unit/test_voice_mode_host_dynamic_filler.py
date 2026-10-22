r"""
Unit tests for VoiceModeHost's dynamic-filler gate (issue #654 problem 3 follow-up).

Live testing showed the dynamic filler firing a real HTTP call to the 9B voice
model on EVERY voice turn, regardless of whether the fast lane would resolve it
instantly — spoken as unwanted "thinking about it..." chatter and contributing to
LM Studio OOM errors under VRAM pressure. `_make_dynamic_filler` now skips the
model call only for `open` (truly instant, tier 1 executes it directly with no
model call). `search` used to be gated the same way but no longer is: search now
always needs a real model round trip (tier 2 synthesis or tier 3 escalation) since
a canned "Searching for X." ack with no follow-up was itself a live bug — gating
search out of the dynamic filler meant it fell back to generic canned phrases right
when the wait is longest and a tailored filler matters most.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_mode_host_dynamic_filler.py -q
"""

from unittest.mock import MagicMock

import pytest

from src.clients.desktop.voice_mode_host import VoiceModeHost


def _host():
    config = {
        "local_models": {
            "category_models": {"voice": "qwen/qwen3.5-9b"},
            "providers": {"lmstudio": {"base_url": "http://localhost:1234/v1"}},
        }
    }
    return VoiceModeHost(
        uplink_limb=MagicMock(), window=MagicMock(), loop=MagicMock(), config=config,
    )


@pytest.mark.asyncio
async def test_open_match_skips_model_call(monkeypatch):
    host = _host()
    generate = host._make_dynamic_filler()
    assert generate is not None

    called = {"hit": False}

    async def fake_generate_dynamic_filler(text, model, base_url):
        called["hit"] = True
        return "should not be reached"

    monkeypatch.setattr(
        "src.core.tts.dynamic_filler.generate_dynamic_filler", fake_generate_dynamic_filler
    )

    result = await generate("open youtube")

    assert result is None
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_search_match_still_calls_model_for_a_tailored_filler(monkeypatch):
    """Regression: search used to be gated out the same way `open` is, which meant
    a search command — now always a multi-second tier-2/tier-3 wait — fell back to
    the generic canned rotation instead of a query-tailored dynamic phrase."""
    host = _host()
    generate = host._make_dynamic_filler()

    async def fake_generate_dynamic_filler(text, model, base_url):
        return "Looking that up for you"

    monkeypatch.setattr(
        "src.core.tts.dynamic_filler.generate_dynamic_filler", fake_generate_dynamic_filler
    )

    result = await generate("look up the best card in clash royale")

    assert result == "Looking that up for you"


@pytest.mark.asyncio
async def test_polite_open_also_skips_model_call(monkeypatch):
    host = _host()
    generate = host._make_dynamic_filler()

    called = {"hit": False}

    async def fake_generate_dynamic_filler(text, model, base_url):
        called["hit"] = True
        return "should not be reached"

    monkeypatch.setattr(
        "src.core.tts.dynamic_filler.generate_dynamic_filler", fake_generate_dynamic_filler
    )

    result = await generate("can you open youtube please")

    assert result is None
    assert called["hit"] is False
