import asyncio
from typing import Any, Optional

from src.shared.config_manager import ConfigManager
from src.application.commands import GetVolumeCommand, SetVolumeCommand
from src.clients.desktop.audio_settings_probe import AudioSettingsProbe
from src.shared.logger import setup_logger

logger = setup_logger("AudioAPI")


class AudioAPIMixin:
    """Mixin for audio-related WebView API methods.

    Input device enumeration and mic/wake-word tests run locally in the Limb —
    the headless Brain uses MockAudio and cannot see real hardware.
    """

    def _audio_probe(self) -> AudioSettingsProbe:
        # Cached (not rebuilt per call): dictation start/stop needs the same
        # probe instance across both calls to track its in-flight recording.
        probe = getattr(self, "_audio_probe_instance", None)
        if probe is None:
            probe = AudioSettingsProbe(self.main_window_ref())
            self._audio_probe_instance = probe
        return probe

    def _run_on_app_loop(self, coro, timeout: float = 15.0, default: Any = None) -> Any:
        window = self.main_window_ref()
        if not window or not window.app_reference or not window.app_reference.loop:
            return default
        future = asyncio.run_coroutine_threadsafe(coro, window.app_reference.loop)
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            logger.error(f"Audio API async call failed: {e}")
            return default

    def get_audio_devices(self):
        """Return available local microphone input devices."""
        try:
            return self._audio_probe().list_input_devices()
        except Exception as e:
            logger.error(f"get_audio_devices failed: {e}")
            return []

    def set_audio_device(self, index):
        """Set the active microphone input device by sounddevice index."""
        try:
            return self._audio_probe().set_input_device(int(index))
        except Exception as e:
            logger.error(f"set_audio_device failed: {e}")
            return False

    def switch_audio_device_by_id(self, device_id):
        """Switch microphone — ``device_id`` is the sounddevice index as a string."""
        try:
            return self._audio_probe().set_input_device(int(device_id))
        except Exception as e:
            logger.error(f"switch_audio_device_by_id failed: {e}")
            return False

    def get_audio_level(self):
        """Current microphone RMS level (0.0–1.0) from local hardware."""
        try:
            return float(self._audio_probe().get_mic_level())
        except Exception:
            return 0.0

    def test_microphone(self, device_id=None, duration=3.0):
        """Sample the mic for a few seconds and report whether speech was heard."""
        idx = int(device_id) if device_id not in (None, "") else None
        return self._run_on_app_loop(
            self._audio_probe().test_microphone(device_index=idx, duration=float(duration)),
            timeout=float(duration) + 5.0,
            default={"success": False, "heard": False, "message": "Microphone test timed out."},
        )

    def start_dictation(self, device_id=None):
        """Begin push-to-talk dictation recording (mic button in the composer)."""
        idx = int(device_id) if device_id not in (None, "") else None
        return self._run_on_app_loop(
            self._audio_probe().start_dictation(device_index=idx),
            timeout=5.0,
            default={"success": False, "message": "Failed to start recording."},
        )

    def stop_dictation(self):
        """Stop dictation recording and return the transcribed text."""
        return self._run_on_app_loop(
            self._audio_probe().stop_dictation(),
            timeout=20.0,
            default={"success": False, "text": "", "message": "Transcription timed out."},
        )

    def test_wake_word(self, device_id=None, timeout=10.0):
        """Listen for the configured wake word (e.g. Hey Chad) for up to ``timeout`` s."""
        idx = int(device_id) if device_id not in (None, "") else None
        return self._run_on_app_loop(
            self._audio_probe().test_wake_word(device_index=idx, timeout=float(timeout)),
            timeout=float(timeout) + 8.0,
            default={"success": False, "detected": False, "message": "Wake word test timed out."},
        )

    def get_wake_word_mode(self):
        """Whether always-armed "Hey Chad" voice mode is on, plus why it isn't armed.

        Distinct from ``get_voice_mode_state`` in the system API, which is the
        Gemini Live session flag. This one is the local wake-word pipeline
        (``voice_mode.enabled``). The availability half is what makes a broken
        mic or a missing dependency visible in Settings instead of only in the
        logs (issue #654).
        """
        try:
            config = ConfigManager.load_config()
            window = self.main_window_ref()
            return {
                "enabled": bool(config.get("voice_mode", {}).get("enabled", False)),
                "availability": getattr(window, "last_voice_availability", None) if window else None,
            }
        except Exception as e:
            logger.error(f"get_wake_word_mode failed: {e}")
            return {"enabled": False, "availability": None}

    def set_wake_word_mode(self, enabled):
        """Turn always-armed "Hey Chad" voice mode on or off, and (dis)arm it live."""
        enabled = bool(enabled)
        try:
            self._config_repo.update_preference("voice_mode.enabled", enabled)
            ConfigManager.load_config()
        except Exception as e:
            logger.error(f"set_wake_word_mode failed to persist: {e}")
            return {"success": False, "armed": False}

        window = self.main_window_ref()
        app = window.app_reference if window else None
        host = getattr(app, "voice_mode_host", None)
        if not host:
            # Voice host was never constructed (e.g. it raised at boot). The
            # preference is saved; it takes effect on next launch.
            return {"success": True, "armed": False, "requires_restart": True}

        armed = self._run_on_app_loop(
            host.apply_enabled(enabled), timeout=30.0, default=False
        )
        return {"success": True, "armed": bool(armed)}

    def get_volume(self):
        """Get master output volume as 0–100 for the settings slider."""
        handler = self._audio_probe()._live_audio_handler()
        if handler:
            return round(float(handler.get_volume()) * 100)
        return round(float(self._execute_command(GetVolumeCommand(), timeout=2.0, default_fallback=1.0)) * 100)

    def set_volume(self, volume):
        """Set master output volume. Accepts 0–100 from the UI slider."""
        try:
            vol_pct = float(volume)
            vol_float = max(0.0, min(1.0, vol_pct / 100.0))
            handler = self._audio_probe()._live_audio_handler()
            if handler:
                handler.set_volume(vol_float)
                self._config_repo.update_preference("audio.volume", vol_float)
                ConfigManager.load_config()
                return True
            success = self._execute_command(
                SetVolumeCommand(volume=vol_float), timeout=2.0, default_fallback=False
            )
            if success:
                self._config_repo.update_preference("audio.volume", vol_float)
                ConfigManager.load_config()
            return success
        except Exception:
            return False

    def get_autonomous_announcements(self) -> bool:
        """Whether autonomous action announcements (spoken TTS) are enabled."""
        try:
            return bool(ConfigManager.get("tts.announce_autonomous", True))
        except Exception as e:
            logger.error(f"get_autonomous_announcements failed: {e}")
            return True

    def set_autonomous_announcements(self, enabled: bool) -> dict:
        """Toggle spoken TTS announcements for autonomous actions."""
        enabled = bool(enabled)
        try:
            self._config_repo.update_preference("tts.announce_autonomous", enabled)
            ConfigManager.load_config()
            return {"success": True, "enabled": enabled}
        except Exception as e:
            logger.error(f"set_autonomous_announcements failed: {e}")
            return {"success": False, "enabled": not enabled, "error": str(e)}
