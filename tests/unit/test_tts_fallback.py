r"""
Unit tests for TTS engine fallback and per-engine voice resolution.

Fakes only — no audio hardware, no model weights, no network. Run:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_tts_fallback.py -q
"""

import pytest

from src.core.tts.base_tts_engine import BaseTTSEngine
from src.core.tts.tts_player import FallbackTTSEngine


class FakeEngine(BaseTTSEngine):
    """Engine that returns a fixed payload, or empty bytes to signal failure."""

    def __init__(self, audio: bytes = b"audio", available: bool = True, raises: bool = False):
        self._audio = audio
        self._available = available
        self._raises = raises
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    async def synthesize(self, text: str) -> bytes:
        self.calls += 1
        if self._raises:
            raise RuntimeError("engine exploded")
        return self._audio


@pytest.fixture
def patch_loader(monkeypatch):
    """Patch _load_engine so the fallback is a fake instead of real weights."""

    def _apply(engine: FakeEngine):
        created = []

        def fake_load(name):
            created.append(name)
            return engine

        monkeypatch.setattr("src.core.tts.tts_player._load_engine", fake_load)
        return created

    return _apply


@pytest.mark.asyncio
async def test_primary_audio_is_used_and_fallback_never_loads(patch_loader):
    primary = FakeEngine(b"primary-audio")
    fallback = FakeEngine(b"fallback-audio")
    created = patch_loader(fallback)

    engine = FallbackTTSEngine(primary, "kokoro")
    assert await engine.synthesize("hello") == b"primary-audio"
    assert fallback.calls == 0
    # Lazy construction: a healthy primary must not pay to load fallback weights.
    assert created == []


@pytest.mark.asyncio
async def test_empty_primary_falls_back(patch_loader):
    """Engines signal failure with empty bytes, which otherwise means silence."""
    primary = FakeEngine(b"")
    fallback = FakeEngine(b"fallback-audio")
    patch_loader(fallback)

    engine = FallbackTTSEngine(primary, "kokoro")
    assert await engine.synthesize("hello") == b"fallback-audio"
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_raising_primary_falls_back(patch_loader):
    primary = FakeEngine(raises=True)
    fallback = FakeEngine(b"fallback-audio")
    patch_loader(fallback)

    engine = FallbackTTSEngine(primary, "kokoro")
    assert await engine.synthesize("hello") == b"fallback-audio"


@pytest.mark.asyncio
async def test_both_engines_failing_returns_empty(patch_loader):
    primary = FakeEngine(b"")
    fallback = FakeEngine(b"")
    patch_loader(fallback)

    engine = FallbackTTSEngine(primary, "kokoro")
    assert await engine.synthesize("hello") == b""


@pytest.mark.asyncio
async def test_unloadable_fallback_does_not_raise(monkeypatch):
    def boom(name):
        raise RuntimeError("no such engine")

    monkeypatch.setattr("src.core.tts.tts_player._load_engine", boom)

    engine = FallbackTTSEngine(FakeEngine(b""), "nonexistent")
    assert await engine.synthesize("hello") == b""


def test_is_available_true_when_only_fallback_available(patch_loader):
    primary = FakeEngine(available=False)
    patch_loader(FakeEngine(available=True))

    assert FallbackTTSEngine(primary, "kokoro").is_available() is True


def test_is_available_false_when_both_unavailable(patch_loader):
    primary = FakeEngine(available=False)
    patch_loader(FakeEngine(available=False))

    assert FallbackTTSEngine(primary, "kokoro").is_available() is False


# --------------------------------------------------------------------------- #
# Per-engine voice namespaces


@pytest.mark.parametrize(
    "kokoro_voice,tts_voice,expected",
    [
        # Orpheus/Gemini names must not leak into Kokoro (404s -> silence).
        (None, "leo", "am_michael"),
        (None, "Puck", "am_michael"),
        # Explicit Kokoro override wins.
        ("af_heart", "leo", "af_heart"),
        # A Kokoro-shaped tts.voice is still honoured for back-compat.
        (None, "am_adam", "am_adam"),
        # Nothing configured at all.
        (None, None, "am_michael"),
    ],
)
def test_kokoro_voice_resolution(monkeypatch, kokoro_voice, tts_voice, expected):
    from src.core.tts.engines import kokoro_engine

    values = {"tts.kokoro_voice": kokoro_voice, "tts.voice": tts_voice}

    def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(kokoro_engine.ConfigManager, "get", staticmethod(fake_get))
    assert kokoro_engine._resolve_voice() == expected
