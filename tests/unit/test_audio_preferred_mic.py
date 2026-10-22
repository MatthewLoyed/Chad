r"""
Unit tests for preferred-mic matching / AirPods Hands-Free ranking.

Fakes only — no audio hardware. Run:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_audio_preferred_mic.py -q
"""

from unittest.mock import MagicMock

from src.core.audio.audio_handler import AudioHandler


def _bare_handler(monkeypatch, devices, wasapi_index=0, preferred="AirPods Pro"):
    class FakeSd:
        @staticmethod
        def query_devices(*_args, **_kwargs):
            return devices

    monkeypatch.setattr("src.core.audio.audio_handler.sd", FakeSd)
    handler = object.__new__(AudioHandler)
    handler.config = {"preferred_device_name": preferred}
    handler.loop = None
    handler.wasapi_index = wasapi_index
    handler.preferred_device_name = preferred
    handler.input = MagicMock(input_device_index=None)
    handler.cached_devices = []
    handler.ui_callback = None
    handler._preferred_retry_handles = []
    return handler


def test_prefers_hands_free_over_stereo_named_endpoint(monkeypatch):
    devices = [
        {"name": "Speakers (AirPods Pro)", "max_input_channels": 0, "hostapi": 0},
        {"name": "AirPods Pro Stereo", "max_input_channels": 1, "hostapi": 0},
        {"name": "Headset (AirPods Pro Hands-Free AG Audio)", "max_input_channels": 1, "hostapi": 0},
        {"name": "Microphone Array (Realtek)", "max_input_channels": 2, "hostapi": 0},
    ]
    handler = _bare_handler(monkeypatch, devices)
    idx = handler._find_preferred_input_index("AirPods Pro")
    assert idx == 2


def test_normalize_fuzzy_match_ignores_punctuation(monkeypatch):
    devices = [
        {"name": "Headset (AirPods Pro)", "max_input_channels": 1, "hostapi": 0},
    ]
    handler = _bare_handler(monkeypatch, devices)
    assert handler._find_preferred_input_index("airpods-pro") == 0


def test_no_match_returns_none(monkeypatch):
    devices = [
        {"name": "Microphone Array (Realtek)", "max_input_channels": 2, "hostapi": 0},
    ]
    handler = _bare_handler(monkeypatch, devices)
    assert handler._find_preferred_input_index("AirPods Pro") is None


def test_mic_endpoint_rank_orders_hands_free_first():
    assert AudioHandler._mic_endpoint_rank("Hands-Free AG Audio") < AudioHandler._mic_endpoint_rank(
        "AirPods Pro"
    )
    assert AudioHandler._mic_endpoint_rank("AirPods Pro") < AudioHandler._mic_endpoint_rank(
        "AirPods Pro Stereo"
    )


def test_preferred_lock_name_strips_stereo_and_hands_free():
    assert AudioHandler._preferred_lock_name("AirPods Pro Stereo") == "AirPods Pro"
    assert AudioHandler._preferred_lock_name(
        "Headset (AirPods Pro Hands-Free AG Audio)"
    ) == "AirPods Pro"
