"""
Local audio hardware probe for the desktop Limb settings UI.

The headless Brain uses MockAudio, so microphone enumeration and tests must run
locally in the GUI process — not via the HTTP command relay to server.py.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import sounddevice as sd

from src.infrastructure.repositories import ConfigRepository
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("AudioSettingsProbe")

SPEECH_LEVEL_THRESHOLD = 0.008  # RMS normalized — tuned for typical desktop mics
MAX_DICTATION_SECONDS = 120  # safety cap — force-stop a forgotten/orphaned recording
DICTATION_SILENCE_TIMEOUT = 5.0  # trailing silence (s) after speech that ends dictation
DICTATION_SILENCE_POLL = 0.15  # how often the monitor checks for silence / max duration


def _wasapi_host_index() -> Optional[int]:
    try:
        for i, hostapi in enumerate(sd.query_hostapis()):
            if "WASAPI" in hostapi.get("name", ""):
                return i
    except Exception:
        pass
    return None


def _resolve_current_input_index() -> int:
    config = ConfigManager.load_config()
    audio = config.get("audio", {})
    cached = audio.get("input_device_index")
    if cached is not None:
        try:
            info = sd.query_devices(int(cached))
            if info.get("max_input_channels", 0) > 0:
                return int(cached)
        except Exception:
            pass
    try:
        default_idx = sd.default.device[0]
        if default_idx is not None and int(default_idx) >= 0:
            return int(default_idx)
    except Exception:
        pass
    return -1


class AudioSettingsProbe:
    """Enumerate and test local microphone hardware from the Limb."""

    def __init__(self, window: Any = None):
        self._window = window
        self._config_repo = ConfigRepository()
        self._wasapi_index = _wasapi_host_index()
        self._wake_paused = False
        self._dictation_stream: Optional[sd.InputStream] = None
        self._dictation_chunks: List[bytes] = []
        self._dictation_session = 0
        self._dictation_speech_started = False
        self._dictation_last_voice_at = 0.0
        # Created lazily on the app loop — probe construction is sync.
        self._dictation_lock: Optional[asyncio.Lock] = None
        self._last_dictation_result: Optional[Dict[str, Any]] = None

    def _app(self):
        if self._window and getattr(self._window, "app_reference", None):
            return self._window.app_reference
        return None

    def _voice_host(self):
        app = self._app()
        if app and getattr(app, "voice_mode_host", None):
            return app.voice_mode_host
        return None

    def _live_audio_handler(self):
        host = self._voice_host()
        if host and getattr(host, "_audio_handler", None):
            handler = host._audio_handler
            if handler.input.is_active:
                return handler
        return None

    def list_input_devices(self) -> List[Dict[str, Any]]:
        """Return WASAPI input devices in the shape the settings UI expects."""
        # Prefer the LIVE stream index when voice mode is up — config can be stale
        # (AirPods shown active while wake listens on Realtek).
        current = _resolve_current_input_index()
        handler = self._live_audio_handler()
        if handler is not None:
            live = getattr(handler.input, "input_device_index", None)
            if live is None:
                try:
                    live = sd.default.device[0]
                except Exception:
                    live = None
            if live is not None and int(live) >= 0:
                current = int(live)

        devices: List[Dict[str, Any]] = []
        try:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                if self._wasapi_index is not None and dev.get("hostapi") != self._wasapi_index:
                    continue
                name = dev.get("name", "Unknown")
                # Demote Stereo-named AirPods endpoints in ranking for "active"
                # display when a Hands-Free sibling exists — still listed.
                devices.append(
                    {
                        "id": str(i),
                        "index": i,
                        "name": name,
                        "active": i == current,
                    }
                )
        except Exception as e:
            logger.error(f"Failed to enumerate input devices: {e}")
        return devices

    def set_input_device(self, device_index: int) -> bool:
        """Switch the active mic and persist the preference."""
        try:
            idx = int(device_index)
            info = sd.query_devices(idx)
            if info.get("max_input_channels", 0) <= 0:
                return False

            # If the user picked an AirPods Stereo endpoint, re-resolve to Hands-Free.
            raw_name = info.get("name", "Unknown")
            from src.core.audio.audio_handler import AudioHandler

            brand = AudioHandler._preferred_lock_name(raw_name)
            better = None
            if brand != raw_name:
                # Build a temporary finder using live handler if present.
                handler = self._live_audio_handler()
                if handler is not None:
                    better = handler._find_preferred_input_index(brand)
                else:
                    # Probe-only path: construct a lightweight finder.
                    better = self._find_preferred_input_index(brand)
                if better is not None:
                    idx = better
                    info = sd.query_devices(idx)
                    raw_name = info.get("name", raw_name)

            handler = self._live_audio_handler()
            if handler:
                handler.set_audio_device(idx)
            else:
                clean_name = "".join(char for char in raw_name if char.isascii())
                lock_name = AudioHandler._preferred_lock_name(clean_name)
                self._config_repo.update_preference("audio.input_device_index", idx)
                self._config_repo.update_preference("audio.preferred_device_name", lock_name)
                ConfigManager.load_config()
            return True
        except Exception as e:
            logger.error(f"Failed to set input device: {e}")
            return False

    def _find_preferred_input_index(self, preferred_name: str) -> Optional[int]:
        """Match preferred name without a live AudioHandler (settings-only path)."""
        from src.core.audio.audio_handler import AudioHandler

        norm_preferred = AudioHandler._normalize_device_name(preferred_name)
        if not norm_preferred:
            return None
        candidates = []
        try:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                if self._wasapi_index is not None and dev.get("hostapi") != self._wasapi_index:
                    continue
                name = dev.get("name") or ""
                if norm_preferred in AudioHandler._normalize_device_name(name):
                    candidates.append((AudioHandler._mic_endpoint_rank(name), i))
        except Exception:
            return None
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        return candidates[0][1]

    def get_mic_level(self) -> float:
        """Current RMS input level (0.0–1.0) from the live mic stream."""
        handler = self._live_audio_handler()
        if handler:
            return float(handler.input.get_current_input_level())
        return 0.0

    async def test_microphone(
        self, device_index: Optional[int] = None, duration: float = 3.0
    ) -> Dict[str, Any]:
        """
        Sample mic input for ``duration`` seconds and report whether speech-level
        audio was detected.
        """
        idx = int(device_index) if device_index is not None else _resolve_current_input_index()
        if idx < 0:
            return {
                "success": False,
                "heard": False,
                "peak_level": 0.0,
                "message": "No input device selected.",
            }

        handler = self._live_audio_handler()
        if handler:
            if handler.input.input_device_index != idx:
                handler.set_audio_device(idx)
            peak = 0.0
            samples: List[float] = []
            steps = max(1, int(duration / 0.1))
            for _ in range(steps):
                level = float(handler.input.get_current_input_level())
                peak = max(peak, level)
                samples.append(level)
                await asyncio.sleep(0.1)
            heard = peak >= SPEECH_LEVEL_THRESHOLD
            return {
                "success": True,
                "heard": heard,
                "peak_level": round(peak, 4),
                "avg_level": round(sum(samples) / len(samples), 4) if samples else 0.0,
                "message": (
                    "Microphone is working - we heard you."
                    if heard
                    else "No speech detected. Check your mic volume or try a different device."
                ),
            }

        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._test_microphone_blocking(idx, duration)
        )

    def _test_microphone_blocking(self, device_index: int, duration: float) -> Dict[str, Any]:
        peak = 0.0
        samples: List[float] = []

        def callback(indata, frames, time_info, status):
            nonlocal peak
            try:
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / 32768.0)
                peak = max(peak, rms)
                samples.append(rms)
            except Exception:
                pass

        stream = None
        try:
            stream = sd.InputStream(
                device=device_index,
                channels=1,
                samplerate=16000,
                blocksize=512,
                dtype="int16",
                callback=callback,
            )
            stream.start()
            time.sleep(duration)
        except Exception as e:
            return {
                "success": False,
                "heard": False,
                "peak_level": 0.0,
                "message": f"Could not open microphone: {e}",
            }
        finally:
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

        heard = peak >= SPEECH_LEVEL_THRESHOLD
        return {
            "success": True,
            "heard": heard,
            "peak_level": round(peak, 4),
            "avg_level": round(sum(samples) / len(samples), 4) if samples else 0.0,
            "message": (
                "Microphone is working — we heard you."
                if heard
                else "No speech detected. Check your mic volume or try a different device."
            ),
        }

    async def _pause_voice_mode(self) -> bool:
        host = self._voice_host()
        if not host or not host._controller:
            return False
        try:
            await host._controller.stop()
            self._wake_paused = True
            return True
        except Exception as e:
            logger.warning(f"Could not pause voice mode for wake-word test: {e}")
            return False

    async def _resume_voice_mode(self) -> None:
        if not self._wake_paused:
            return
        host = self._voice_host()
        self._wake_paused = False
        if host and host._controller:
            try:
                host._controller.start()
            except Exception as e:
                logger.warning(f"Could not resume voice mode after wake-word test: {e}")

    async def test_wake_word(
        self, device_index: Optional[int] = None, timeout: float = 10.0
    ) -> Dict[str, Any]:
        """Listen for the configured wake word (e.g. Hey Chad)."""
        from src.core.audio.wake_word import WakeWordDetector

        idx = int(device_index) if device_index is not None else _resolve_current_input_index()
        if idx < 0:
            return {
                "success": False,
                "detected": False,
                "message": "No input device selected.",
            }

        config = ConfigManager.load_config()
        wake_config = config.get("wake_word", {})
        audio_config = config.get("audio", {})
        sample_rate = audio_config.get("input_sample_rate", 16000)
        keyword = "Hey Chad"

        await self._pause_voice_mode()
        detector = None
        stream = None
        detected = False

        try:
            detector = WakeWordDetector(config=wake_config, audio_sample_rate=sample_rate)
            handler = self._live_audio_handler()

            if handler:
                if handler.input.input_device_index != idx:
                    handler.set_audio_device(idx)

                deadline = time.monotonic() + timeout
                async for chunk in handler.input.read_chunks():
                    if time.monotonic() >= deadline:
                        break
                    if detector.process_audio(chunk):
                        detected = True
                        break
            else:
                device_info = sd.query_devices(idx, "input")
                device_rate = int(device_info.get("default_samplerate", sample_rate))

                def callback(indata, frames, time_info, status):
                    nonlocal detected
                    if detected:
                        return
                    if device_rate != sample_rate:
                        data_int16 = indata.flatten()
                        duration = len(data_int16) / device_rate
                        num_samples = int(duration * sample_rate)
                        resampled = np.interp(
                            np.linspace(0, duration, num_samples),
                            np.linspace(0, duration, len(data_int16)),
                            data_int16
                        ).astype(np.int16)
                        pcm_bytes = resampled.tobytes()
                    else:
                        pcm_bytes = indata.tobytes()
                    if detector.process_audio(pcm_bytes):
                        detected = True

                stream = sd.InputStream(
                    device=idx,
                    channels=1,
                    samplerate=device_rate,
                    blocksize=512,
                    dtype="int16",
                    callback=callback,
                )
                stream.start()

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not detected:
                    await asyncio.sleep(0.05)

            peak = detector.peak_score if detector else 0.0
            thresh = detector.threshold if detector else wake_config.get("threshold", 0.45)
            if detected:
                message = f'Wake word detected — "{keyword}" was heard (score: {peak:.2f}).'
            else:
                score_hint = f" (peak: {peak:.2f}, threshold: {thresh:.2f})" if peak > 0 else ""
                message = f'No wake word detected{score_hint}. Say "{keyword}" clearly into your mic.'

            return {
                "success": True,
                "detected": detected,
                "peak_score": round(peak, 3),
                "threshold": thresh,
                "message": message,
            }
        except Exception as e:
            logger.error(f"Wake word test failed: {e}")
            return {
                "success": False,
                "detected": False,
                "message": f"Wake word test unavailable: {e}",
            }
        finally:
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            if detector:
                try:
                    await detector.cleanup()
                except Exception:
                    pass
            await self._resume_voice_mode()

    async def start_dictation(self, device_index: Optional[int] = None) -> Dict[str, Any]:
        """
        Begin a push-to-talk recording for one-shot dictation (mic button in the
        composer). Independent of the wake-word/VAD voice-mode pipeline — pauses
        it for the duration if it's running, so both don't fight over the device.

        Ends when the user clicks stop, after ``DICTATION_SILENCE_TIMEOUT`` of
        trailing silence once speech has been heard, or at ``MAX_DICTATION_SECONDS``.
        """
        if self._dictation_stream is not None:
            return {
                "success": True,
                "message": "Already recording.",
                "session": self._dictation_session,
            }

        idx = int(device_index) if device_index is not None else _resolve_current_input_index()
        if idx < 0:
            return {"success": False, "message": "No input device selected."}

        await self._pause_voice_mode()
        if self._dictation_lock is None:
            self._dictation_lock = asyncio.Lock()
        self._dictation_chunks = []
        self._dictation_session += 1
        session = self._dictation_session
        self._dictation_speech_started = False
        self._dictation_last_voice_at = time.monotonic()
        self._last_dictation_result = None

        def callback(indata, frames, time_info, status):
            self._dictation_chunks.append(indata.tobytes())
            try:
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / 32768.0)
                if rms >= SPEECH_LEVEL_THRESHOLD:
                    self._dictation_speech_started = True
                    self._dictation_last_voice_at = time.monotonic()
            except Exception:
                pass

        try:
            stream = sd.InputStream(
                device=idx,
                channels=1,
                samplerate=16000,
                blocksize=512,
                dtype="int16",
                callback=callback,
            )
            stream.start()
            self._dictation_stream = stream
            asyncio.get_running_loop().create_task(self._dictation_monitor(session))
            return {"success": True, "message": "Recording.", "session": session}
        except Exception as e:
            logger.error(f"Failed to start dictation recording: {e}")
            await self._resume_voice_mode()
            return {"success": False, "message": f"Could not open microphone: {e}"}

    async def _dictation_monitor(self, session: int) -> None:
        """
        End dictation on trailing silence (after speech) or the max-duration cap.

        No-op once this session's recording has already ended (manual stop or a
        newer session). Auto-stops push the transcript to the webview so the
        composer can leave the listening state without another mic click.
        """
        started_at = time.monotonic()
        while self._dictation_session == session and self._dictation_stream is not None:
            now = time.monotonic()
            if (now - started_at) >= MAX_DICTATION_SECONDS:
                logger.warning("Dictation recording exceeded max duration; auto-stopping.")
                await self._auto_stop_dictation("max_duration")
                return
            if (
                self._dictation_speech_started
                and (now - self._dictation_last_voice_at) >= DICTATION_SILENCE_TIMEOUT
            ):
                logger.info(
                    "Dictation silence timeout (%.1fs); auto-stopping.",
                    DICTATION_SILENCE_TIMEOUT,
                )
                await self._auto_stop_dictation("silence")
                return
            await asyncio.sleep(DICTATION_SILENCE_POLL)

    async def _auto_stop_dictation(self, reason: str) -> None:
        if self._dictation_stream is None:
            return
        session = self._dictation_session

        def _on_progress(payload: Dict[str, Any]) -> None:
            self._push_dictation_result(payload)

        result = await self.stop_dictation(reason=reason, on_progress=_on_progress)
        # Lost the race to a manual stop (or nothing to stop) — don't flash errors
        # or leave the composer spinning.
        if result.get("message") == "Not recording.":
            return
        if result.get("session") != session:
            return
        self._push_dictation_result(result)

    def _push_dictation_result(self, result: Dict[str, Any]) -> None:
        push = getattr(self._window, "push_dictation_result", None) if self._window else None
        if not callable(push):
            return
        try:
            push(result)
        except Exception as e:
            logger.error(f"Failed to push dictation result to UI: {e}")

    async def stop_dictation(
        self,
        reason: str = "manual",
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Stop the current dictation recording and transcribe it via faster-whisper.

        ``on_progress`` is invoked once the recorder has been claimed (stream
        closed) and before the blocking STT call — used by silence auto-stop to
        flip the composer into a transcribing spinner without racing a manual stop.
        """
        if self._dictation_lock is None:
            self._dictation_lock = asyncio.Lock()
        async with self._dictation_lock:
            stream = self._dictation_stream
            session = self._dictation_session

            if not stream:
                await self._resume_voice_mode()
                if self._last_dictation_result is not None:
                    return self._last_dictation_result
                return {
                    "success": False,
                    "text": "",
                    "message": "Not recording.",
                    "session": session,
                    "reason": reason,
                }

            self._dictation_stream = None

            try:
                stream.stop()
                stream.close()
            except Exception as e:
                logger.error(f"Failed to stop dictation stream: {e}")

            pcm = b"".join(self._dictation_chunks)
            self._dictation_chunks = []
            self._dictation_speech_started = False
            await self._resume_voice_mode()

            if callable(on_progress):
                try:
                    on_progress(
                        {
                            "success": True,
                            "text": "",
                            "message": "",
                            "session": session,
                            "reason": reason,
                            "status": "transcribing",
                        }
                    )
                except Exception as e:
                    logger.error(f"Dictation on_progress failed: {e}")

            if not pcm:
                result = {
                    "success": True,
                    "text": "",
                    "message": "No audio captured.",
                    "session": session,
                    "reason": reason,
                }
                self._last_dictation_result = result
                return result

            from src.core.stt.stt_factory import get_stt_engine

            engine = get_stt_engine()
            if not engine.is_available():
                result = {
                    "success": False,
                    "text": "",
                    "message": (
                        "Speech-to-text engine unavailable "
                        "(pip install -r requirements-voice.txt)."
                    ),
                    "session": session,
                    "reason": reason,
                }
                self._last_dictation_result = result
                return result

            text = await engine.transcribe(pcm, sample_rate=16000)
            result = {
                "success": True,
                "text": text,
                "message": "" if text else "No speech detected.",
                "session": session,
                "reason": reason,
            }
            self._last_dictation_result = result
            return result
