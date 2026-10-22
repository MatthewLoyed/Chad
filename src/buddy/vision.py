"""One-shot vision comment via LM Studio — bypasses the main agent turn."""

from __future__ import annotations

import re

import litellm

from config.model_registry import get_profile
from src.buddy.persona import build_buddy_persona_prompt
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("BuddyVision")

_USER_GLANCE = "Glance at the screen and say one short personal thing as Chad."


def _normalise_model(model_id: str, base_url: str) -> str:
    if not any(model_id.startswith(p) for p in ("openai/", "ollama/", "lm_studio/")):
        prefix = "ollama/" if "ollama" in base_url.lower() else "openai/"
        return f"{prefix}{model_id}"
    return model_id


_THINK_OPEN = "<" + "think" + ">"
_THINK_CLOSE = "</" + "think" + ">"


def _strip_thinking(text: str) -> str:
    text = re.sub(
        r"<think>[\s\S]*?</think>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    while _THINK_OPEN in text:
        start = text.find(_THINK_OPEN)
        close = text.find(_THINK_CLOSE, start)
        if close == -1:
            text = text[:start]
            break
        text = text[:start] + text[close + len(_THINK_CLOSE) :]
    return text.strip().strip('"').strip("'")


class BuddyVisionClient:
    """Minimal multimodal client for ambient desk comments."""

    def __init__(self, model_id: str | None = None) -> None:
        cfg = ConfigManager.get("buddy.observer") or {}
        self.model_id = model_id or cfg.get("model", "qwen/qwen3.5-9b")
        self.profile = get_profile(self.model_id)
        provider_cfg = ConfigManager.get_local_provider_config("lmstudio")
        self.base_url = (
            self.profile.base_url if self.profile else provider_cfg.get("base_url", "http://localhost:1234/v1")
        )
        self.api_key = provider_cfg.get("api_key", "lm-studio")
        self.max_tokens = int(cfg.get("max_tokens", 64))
        self.temperature = float(cfg.get("temperature", 0.7))

    async def comment_on_screen(self, image_b64: str, mime: str = "image/jpeg") -> str:
        system_prompt = build_buddy_persona_prompt()
        model = _normalise_model(self.model_id, self.base_url)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _USER_GLANCE},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            },
        ]

        # Route through lmstudio-gate (issue #478) at "autonomous" priority so the
        # ambient buddy glance queues behind real turns instead of contending for the
        # one LM Studio slot. Falls back to the direct URL when the gate is off/down.
        try:
            from src.infrastructure.llm.gate_client import inference_base_url
            routed = inference_base_url(self.base_url)
        except Exception:
            routed = self.base_url

        req: dict = {
            "model": model,
            "messages": messages,
            "api_base": routed,
            "api_key": self.api_key,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
            "extra_headers": {"X-Chad-Priority": "autonomous"},
        }

        # Qwen3: disable thinking for a one-liner
        if self.profile and self.profile.thinking_param_style == "qwen3":
            req["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False},
            }

        litellm.drop_params = True
        response = await litellm.acompletion(**req)
        raw = (response.choices[0].message.content or "").strip()
        comment = _strip_thinking(raw)
        if not comment:
            raise RuntimeError("Vision model returned empty buddy comment.")
        logger.info("[ BUDDY ] Vision comment: %s", comment[:80])
        return comment
