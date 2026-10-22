"""Orpheus TTS via LM Studio completions API + local SNAC decode."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Iterator, List, Optional

from src.core.tts.base_tts_engine import BaseTTSEngine
from src.core.tts.orpheus.decoder import convert_frames_to_pcm, turn_token_into_id
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("OrpheusTTSEngine")

AVAILABLE_VOICES = ("tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe")
SAMPLE_RATE = 24000


def _match_model_id(model_id: str, loaded_ids: List[str]) -> Optional[str]:
    if model_id in loaded_ids:
        return model_id
    needle = model_id.lower().replace("_", "-")
    for loaded in loaded_ids:
        if loaded.lower() == needle:
            return loaded
        if needle in loaded.lower() or loaded.lower() in needle:
            return loaded
    base = model_id.split(":")[0]
    for loaded in loaded_ids:
        if loaded.split(":")[0] == base:
            return loaded
    return None


def _format_prompt(text: str, voice: str) -> str:
    if voice not in AVAILABLE_VOICES:
        logger.warning(f"Unknown Orpheus voice '{voice}', using 'tara'")
        voice = "tara"
    return f"<|audio|>{voice}: {text}<|eot_id|>"


class OrpheusTTSEngine(BaseTTSEngine):
    """
    LM Studio Orpheus GGUF (completions) + local SNAC decoder.
    Co-load mode: pass explicit model id so Qwen chat models can stay loaded.
    """

    def __init__(self):
        self._base_url = ConfigManager.get(
            "tts.orpheus.base_url",
            ConfigManager.get("local_models.providers.lmstudio.base_url", "http://localhost:1234/v1"),
        ).rstrip("/")
        self._model_id = ConfigManager.get("tts.orpheus.model_id", "orpheus-3b-0.1-ft")
        self._voice = ConfigManager.get("tts.voice", "leo")
        self._temperature = float(ConfigManager.get("tts.orpheus.temperature", 0.6))
        self._top_p = float(ConfigManager.get("tts.orpheus.top_p", 0.9))
        self._repetition_penalty = float(ConfigManager.get("tts.orpheus.repetition_penalty", 1.1))
        self._max_tokens = int(ConfigManager.get("tts.orpheus.max_tokens", 1200))
        self._resolved_model_id: Optional[str] = None
        self._unavailable_reason: Optional[str] = None

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._unavailable_reason

    def _models_url(self) -> str:
        return f"{self._base_url}/models"

    def _completions_url(self) -> str:
        return f"{self._base_url}/completions"

    def _list_loaded_models(self) -> List[str]:
        try:
            req = urllib.request.Request(self._models_url(), method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
        except Exception as e:
            logger.debug(f"Could not list LM Studio models: {e}")
            return []

    def _resolve_model(self) -> Optional[str]:
        loaded = self._list_loaded_models()
        matched = _match_model_id(self._model_id, loaded)
        if matched:
            self._resolved_model_id = matched
            self._unavailable_reason = None
            return matched
        self._resolved_model_id = None
        self._unavailable_reason = (
            f"orpheus_not_loaded: configured={self._model_id}, loaded={loaded}"
        )
        return None

    def is_available(self) -> bool:
        try:
            import snac  # noqa: F401
        except ImportError:
            self._unavailable_reason = "snac_not_installed"
            return False
        return self._resolve_model() is not None

    def _stream_tokens(self, text: str) -> Iterator[str]:
        model_id = self._resolved_model_id or self._resolve_model()
        if not model_id:
            return

        prompt = _format_prompt(text, self._voice)
        payload = {
            "model": model_id,
            "prompt": prompt,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "repeat_penalty": self._repetition_penalty,
            "stream": True,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._completions_url(),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    choices = parsed.get("choices") or []
                    if not choices:
                        continue
                    token_text = choices[0].get("text") or ""
                    if token_text:
                        yield token_text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            logger.error(f"Orpheus LM Studio HTTP {e.code}: {detail[:500]}")
            self._unavailable_reason = f"completions_http_{e.code}"
        except Exception as e:
            logger.error(f"Orpheus token stream failed: {e}")
            self._unavailable_reason = f"completions_error: {e}"

    def _synthesize_sync(self, text: str) -> bytes:
        if not self._resolve_model():
            return b""

        buffer: List[int] = []
        count = 0
        segments: List[bytes] = []

        for token_text in self._stream_tokens(text):
            token_id = turn_token_into_id(token_text, count)
            if token_id is not None and token_id > 0:
                buffer.append(token_id)
                count += 1
            if count % 7 == 0 and count > 27:
                pcm = convert_frames_to_pcm(buffer[-28:])
                if pcm:
                    segments.append(pcm)

        if not segments:
            logger.warning("Orpheus produced no audio segments")
            return b""
        return b"".join(segments)

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        if not self.is_available():
            logger.warning(f"Orpheus unavailable: {self._unavailable_reason}")
            return b""
        try:
            return await asyncio.to_thread(self._synthesize_sync, text)
        except Exception as e:
            logger.error(f"Orpheus synthesis failed: {e}")
            return b""
