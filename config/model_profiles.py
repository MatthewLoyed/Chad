"""
ModelProfile — per-model capability and call-parameter declaration.

Add a new entry to model_registry.py to declare any model's quirks once.
The LiteLLMAdapter reads the profile instead of name-sniffing at call time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelProfile:
    # ── Identity ──────────────────────────────────────────────────────────────
    model_id: str           # Exact ID as reported by LM Studio / provider
    provider: str           # "lmstudio" | "anthropic" | "openai" | etc.
    base_url: str           # e.g. "http://localhost:1234/v1"

    # ── Message format ────────────────────────────────────────────────────────
    supports_system_message: bool = True
    supports_native_tool_calls: bool = True

    # ── Thinking / reasoning ──────────────────────────────────────────────────
    # thinking_mode: model produces reasoning tokens (<think> / reasoning_content)
    # thinking_param_style: how to enable it via API params
    #   "qwen3"  → inject reasoning_effort + extra_body.chat_template_kwargs
    #   None     → no special params; model thinks on its own (e.g. Gemma 4)
    thinking_mode: bool = False
    thinking_param_style: Optional[str] = None
    thinking_budget: int = 32768        # token budget when style=="qwen3"
    thinking_effort: str = "medium"     # "minimal" | "medium" | "high"

    # ── Sampling defaults ─────────────────────────────────────────────────────
    temperature: float = 0.6
    top_p: float = 0.95
    max_tokens: int = 8192
    context_length: int = 32768

    # ── Arbitrary LiteLLM overrides ───────────────────────────────────────────
    litellm_extra_params: dict = field(default_factory=dict)
