"""
Wake Word Detection.

Uses openWakeWord for local "Hey Chad" detection: free, open source, ONNX-on-CPU,
no API key and no vendor account. Replaces Picovoice Porcupine (#649) — that
account was deleted and signup now requires a company email, a dead end for this
project. See docs/features/VOICE_MODE.md.

openWakeWord expects 16kHz mono int16 frames, which is exactly what AudioInput
already broadcasts, so no resampling is needed.
"""

import os
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from src.shared.debug_helper import debug_print
from src.shared.exceptions import WakeWordError
from src.shared.logger import setup_logger

logger = setup_logger("WakeWord")

# openWakeWord processes audio in multiples of 80ms; 1280 samples @ 16kHz is
# its documented minimum chunk size.
_FRAME_SAMPLES = 1280
_SAMPLE_RATE = 16000
_DEFAULT_THRESHOLD = 0.5


class WakeWordDetector:
    """
    Wake word detector using openWakeWord.
    Listens for a specific keyword to activate the assistant.
    """

    def __init__(
        self,
        config: dict,
        audio_sample_rate: Optional[int] = None,
        model_factory: Optional[Callable[..., Any]] = None,
    ):
        """
        Initialize wake word detector.

        Args:
            config: Wake word configuration dictionary
            audio_sample_rate: Audio sample rate from audio handler (for validation)
            model_factory: optional override for constructing the openWakeWord
                ``Model`` (used by tests to inject a fake without the real
                dependency installed). Defaults to the real
                ``openwakeword.model.Model``.
        """
        self.keyword_path = config.get('keyword_path')
        self.keyword = config.get('keyword', 'hey_jarvis')
        self.threshold = config.get('threshold', _DEFAULT_THRESHOLD)
        self.is_active = False
        self._audio_buffer = bytearray()
        self.last_score = 0.0
        self.peak_score = 0.0

        if self.keyword_path:
            model_path = os.path.abspath(self.keyword_path)
            if not os.path.exists(model_path):
                raise WakeWordError(f"Custom wake word model not found: {model_path}")
            model_names = [model_path]
            self._model_key = Path(model_path).stem
        else:
            model_names = [self.keyword]
            self._model_key = self.keyword

        self._model = (
            model_factory(wakeword_models=model_names)
            if model_factory is not None
            else self._load_real_model(model_names)
        )

        if audio_sample_rate is not None and audio_sample_rate != _SAMPLE_RATE:
            logger.warning(
                f"Sample rate mismatch! Audio handler: {audio_sample_rate} Hz, "
                f"openWakeWord requires: {_SAMPLE_RATE} Hz. "
                f"Wake word detection may not work correctly!"
            )

    def _load_real_model(self, model_names: list) -> Any:
        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError as e:
            raise WakeWordError(
                "openwakeword is not installed. Run: pip install openwakeword "
                "(see requirements-voice.txt)."
            ) from e

        if not self.keyword_path:
            # Pretrained models are downloaded on first use, not bundled with the package.
            try:
                openwakeword.utils.download_models(model_names=[self.keyword])
            except Exception as e:
                logger.warning(
                    f"Could not verify/download openWakeWord model '{self.keyword}': {e}"
                )

        try:
            return Model(wakeword_models=model_names, inference_framework="onnx")
        except Exception as e:
            raise WakeWordError(f"Failed to initialize openWakeWord: {e}") from e

    def process_audio(self, audio_data: bytes) -> bool:
        """
        Process audio data and check for wake word.
        Buffers incoming audio and processes it in exact frame-length chunks.

        Args:
            audio_data: Raw audio bytes (16-bit PCM, mono)

        Returns:
            True if wake word detected, False otherwise
        """
        try:
            self._audio_buffer.extend(audio_data)
            frame_bytes = _FRAME_SAMPLES * 2

            while len(self._audio_buffer) >= frame_bytes:
                frame_data = bytes(self._audio_buffer[:frame_bytes])
                self._audio_buffer = self._audio_buffer[frame_bytes:]

                pcm = np.frombuffer(frame_data, dtype=np.int16)
                predictions = self._model.predict(pcm)

                score = predictions.get(self._model_key)
                if score is None and predictions:
                    # Custom model keys are sometimes normalized (e.g. path stem
                    # vs. the internal model name); fall back to the top score.
                    score = max(predictions.values())

                if score is not None:
                    self.last_score = float(score)
                    if self.last_score > self.peak_score:
                        self.peak_score = self.last_score
                    if score >= 0.2:
                        logger.debug(
                            f"[ WAKE ] : Score for '{self._model_key}': {score:.3f} "
                            f"(threshold: {self.threshold})"
                        )

                if score is not None and score >= self.threshold:
                    # Reset the MODEL too, not just our byte buffer. openWakeWord
                    # scores a rolling window of audio features (~1.5s of
                    # context), so without this the wake phrase is still inside
                    # that window when the loop re-arms and immediately scores
                    # above threshold again — the repeated-earcon loop in #654.
                    self.reset()
                    return True

            return False

        except Exception as e:
            logger.error(f"Error processing audio for wake word: {e}", exc_info=True)
            self._audio_buffer.clear()
            return False

    async def wait_for_activation(
        self,
        audio_stream,
        callback: Optional[Callable] = None
    ) -> None:
        """
        Wait for wake word detection in audio stream.

        Args:
            audio_stream: Audio input stream to monitor
            callback: Optional callback to call when wake word detected
        """
        self.is_active = True
        chunk_count = 0

        try:
            async for audio_chunk in audio_stream.read_chunks():
                if not self.is_active:
                    debug_print(f"DEBUG: WakeWord exit - is_active=False after {chunk_count} chunks")
                    break

                chunk_count += 1

                if self.process_audio(audio_chunk):
                    debug_print(f"DEBUG: Wake word DETECTED after {chunk_count} chunks!")
                    if callback:
                        await callback()
                    break

            if chunk_count == 0:
                debug_print("DEBUG: WakeWord exit - received 0 chunks (generator exited immediately)")

        except Exception as e:
            logger.error(f"Error in wake word detection loop: {e}")
            debug_print(f"DEBUG: WakeWord exception: {e}")
            raise WakeWordError(f"Wake word detection failed: {e}")

    def reset(self) -> None:
        """Drop all buffered audio and the model's rolling feature window.

        Called after every detection so the next arming cycle starts from
        silence. ``openwakeword.model.Model.reset()`` clears the prediction
        buffer and the preprocessor's mel/embedding buffers; it is tolerated
        (not required) for a model object that lacks it, so fakes and future
        engines stay usable.
        """
        self._audio_buffer.clear()
        self.last_score = 0.0
        model_reset = getattr(self._model, "reset", None)
        if not callable(model_reset):
            return
        try:
            model_reset()
        except Exception as e:
            # Never let a reset failure swallow a real detection.
            logger.warning(f"Wake word model reset failed: {e}")

    def stop(self) -> None:
        """Stop wake word detection."""
        self.is_active = False
        self._audio_buffer.clear()

    async def cleanup(self) -> None:
        """Clean up resources (Async)."""
        self.stop()
        self._model = None

    @property
    def sample_rate(self) -> int:
        """Get the required sample rate for wake word detection."""
        return _SAMPLE_RATE

    def __del__(self):
        """Destructor to ensure the detector stops listening."""
        try:
            self.stop()
        except Exception:
            pass
