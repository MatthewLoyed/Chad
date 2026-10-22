r"""
Unit tests for input-device resolution when Windows has no default mic.

Windows can be left with no default recording device, in which case
``sd.default.device`` reports -1 for input and every InputStream open fails with
"Invalid device" even though usable microphones are enumerated. Fakes only — no
audio hardware. Run:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_audio_input_device.py -q
"""

from src.core.audio import audio_handler


def _patch_sd(monkeypatch, default_device, devices):
    class FakeDefault:
        device = default_device

    class FakeSd:
        default = FakeDefault()

        @staticmethod
        def query_devices():
            if isinstance(devices, Exception):
                raise devices
            return devices

    monkeypatch.setattr(audio_handler, "sd", FakeSd)


def test_returns_none_when_a_real_default_exists(monkeypatch):
    """A valid default is left alone so PortAudio honours the user's choice."""
    _patch_sd(monkeypatch, [3, 1], [{"name": "Mic", "max_input_channels": 2}])
    assert audio_handler._resolve_default_input_device() is None


def test_picks_first_input_capable_device_when_default_missing(monkeypatch):
    _patch_sd(
        monkeypatch,
        [-1, 1],
        [
            {"name": "Sound Mapper Output", "max_input_channels": 0},
            {"name": "Speakers", "max_input_channels": 0},
            {"name": "Microphone (Realtek)", "max_input_channels": 2},
            {"name": "Stereo Mix", "max_input_channels": 2},
        ],
    )
    assert audio_handler._resolve_default_input_device() == 2


def test_returns_none_when_no_input_devices_exist(monkeypatch):
    _patch_sd(
        monkeypatch,
        [-1, 1],
        [{"name": "Speakers", "max_input_channels": 0}],
    )
    assert audio_handler._resolve_default_input_device() is None


def test_enumeration_failure_degrades_to_none(monkeypatch):
    _patch_sd(monkeypatch, [-1, 1], RuntimeError("PortAudio blew up"))
    assert audio_handler._resolve_default_input_device() is None


def test_unreadable_default_degrades_to_none(monkeypatch):
    """sd.default.device itself can raise on a broken audio stack."""

    class FakeDefault:
        @property
        def device(self):
            raise RuntimeError("no host api")

    class FakeSd:
        default = FakeDefault()

    monkeypatch.setattr(audio_handler, "sd", FakeSd)
    assert audio_handler._resolve_default_input_device() is None
