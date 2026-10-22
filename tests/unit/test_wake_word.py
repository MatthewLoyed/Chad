r"""
Unit tests for wake word detection (#649: openWakeWord replacing Porcupine).

Fakes only — no audio hardware, no ONNX runtime, no vendor account. Run:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_wake_word.py -q
"""

import struct

import pytest

from src.core.audio.wake_word import WakeWordDetector
from src.shared.exceptions import WakeWordError


def _pcm_frame(n_samples: int, level: int = 100) -> bytes:
    return struct.pack("<%dh" % n_samples, *([level] * n_samples))


class FakeModel:
    """Stand-in for openwakeword.model.Model: fires on the Nth predict() call."""

    def __init__(self, wakeword_models, fire_on_call: int = 2, score: float = 0.9):
        self.model_key = wakeword_models[0]
        self._fire_on_call = fire_on_call
        self._score = score
        self.calls = 0

    def predict(self, frame):
        self.calls += 1
        hit = self._score if self.calls >= self._fire_on_call else 0.01
        return {self.model_key: hit}


def _detector(config=None, **model_kwargs):
    holder = {}

    def factory(wakeword_models):
        holder["model"] = FakeModel(wakeword_models, **model_kwargs)
        return holder["model"]

    detector = WakeWordDetector(config or {}, model_factory=factory)
    return detector, holder["model"]


def test_detects_above_threshold():
    detector, fake_model = _detector({"keyword": "hey_jarvis", "threshold": 0.5}, fire_on_call=1, score=0.9)

    assert detector.process_audio(_pcm_frame(1280)) is True
    assert fake_model.calls == 1


def test_below_threshold_does_not_fire():
    detector, _ = _detector({"keyword": "hey_jarvis", "threshold": 0.5}, fire_on_call=1, score=0.3)

    assert detector.process_audio(_pcm_frame(1280)) is False


def test_buffers_partial_frames():
    """Sub-frame chunks accumulate; predict() only fires once a full frame exists."""
    detector, fake_model = _detector({"keyword": "hey_jarvis"}, fire_on_call=1, score=0.9)

    half = _pcm_frame(640)
    assert detector.process_audio(half) is False
    assert fake_model.calls == 0
    assert detector.process_audio(half) is True
    assert fake_model.calls == 1


def test_sample_rate_is_16k():
    detector, _ = _detector()
    assert detector.sample_rate == 16000


def test_missing_custom_model_raises():
    with pytest.raises(WakeWordError):
        WakeWordDetector(
            {"keyword_path": "does/not/exist.onnx"},
            model_factory=lambda wakeword_models: FakeModel(wakeword_models),
        )


def test_missing_package_raises_wake_word_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openwakeword":
            raise ImportError("no module named openwakeword")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(WakeWordError):
        WakeWordDetector({"keyword": "hey_jarvis"})


def test_process_audio_delegates_and_stops():
    detector, _ = _detector({"keyword": "hey_jarvis"}, fire_on_call=1, score=0.9)
    assert detector.process_audio(_pcm_frame(1280)) is True


@pytest.mark.asyncio
async def test_cleanup_stops_and_drops_model():
    detector, _ = _detector()
    detector.is_active = True
    await detector.cleanup()
    assert detector.is_active is False
    assert detector._model is None


@pytest.mark.asyncio
async def test_wait_for_activation_fires_callback_and_stops():
    class FakeStream:
        async def read_chunks(self):
            yield _pcm_frame(1280)
            yield _pcm_frame(1280)

    detector, _ = _detector({"keyword": "hey_jarvis"}, fire_on_call=1, score=0.9)

    fired = []

    async def callback():
        fired.append(True)

    await detector.wait_for_activation(FakeStream(), callback=callback)
    assert fired == [True]


# --------------------------------------------------------------------------- #
# Model reset after detection (#654 — repeated-earcon wake loop)
# --------------------------------------------------------------------------- #
class ResettableFakeModel(FakeModel):
    """FakeModel that records reset() calls, like the real openWakeWord Model."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resets = 0

    def reset(self):
        self.resets += 1


class ResetlessFakeModel(FakeModel):
    """A model object with no reset() — an older engine or a test fake."""


def _resettable_detector(config=None, **model_kwargs):
    holder = {}

    def factory(wakeword_models):
        holder["model"] = ResettableFakeModel(wakeword_models, **model_kwargs)
        return holder["model"]

    return WakeWordDetector(config or {}, model_factory=factory), holder["model"]


def test_detection_resets_the_model_feature_window():
    """openWakeWord scores a rolling ~1.5s window of audio features. Without
    resetting it after a hit, the wake phrase is still inside that window when
    the loop re-arms and fires again immediately — the earcon spam loop."""
    detector, model = _resettable_detector(
        {"keyword": "hey_jarvis", "threshold": 0.5}, fire_on_call=1, score=0.9
    )

    assert detector.process_audio(_pcm_frame(1280)) is True
    assert model.resets == 1


def test_no_reset_when_below_threshold():
    """A non-detection must not disturb the rolling window — that would throw
    away the very context the model needs to recognise the phrase."""
    detector, model = _resettable_detector(
        {"keyword": "hey_jarvis", "threshold": 0.5}, fire_on_call=99, score=0.9
    )

    assert detector.process_audio(_pcm_frame(1280)) is False
    assert model.resets == 0


def test_detection_still_fires_when_model_has_no_reset():
    """Graceful degradation: a model without reset() must not break detection."""
    holder = {}

    def factory(wakeword_models):
        holder["model"] = ResetlessFakeModel(wakeword_models, fire_on_call=1, score=0.9)
        return holder["model"]

    detector = WakeWordDetector({"keyword": "hey_jarvis", "threshold": 0.5}, model_factory=factory)
    assert detector.process_audio(_pcm_frame(1280)) is True


def test_reset_failure_does_not_swallow_the_detection():
    """A throwing reset() must not turn a real wake into a miss."""

    class ExplodingModel(FakeModel):
        def reset(self):
            raise RuntimeError("onnx session gone")

    holder = {}

    def factory(wakeword_models):
        holder["model"] = ExplodingModel(wakeword_models, fire_on_call=1, score=0.9)
        return holder["model"]

    detector = WakeWordDetector({"keyword": "hey_jarvis", "threshold": 0.5}, model_factory=factory)
    assert detector.process_audio(_pcm_frame(1280)) is True


def test_reset_clears_buffered_partial_frame():
    """Leftover bytes from before the hit must not seed the next arming cycle."""
    detector, _ = _resettable_detector(
        {"keyword": "hey_jarvis", "threshold": 0.5}, fire_on_call=1, score=0.9
    )
    # A frame and a half: the hit fires on the first frame, leaving a remainder.
    detector.process_audio(_pcm_frame(1280 + 640))
    assert len(detector._audio_buffer) == 0


def test_tracks_peak_score_and_last_score():
    detector, _ = _detector({"keyword": "hey_jarvis", "threshold": 0.8}, fire_on_call=2, score=0.6)
    assert detector.peak_score == 0.0
    detector.process_audio(_pcm_frame(1280))
    assert detector.last_score == 0.01
    assert detector.peak_score == 0.01
    detector.process_audio(_pcm_frame(1280))
    assert detector.last_score == 0.6
    assert detector.peak_score == 0.6
