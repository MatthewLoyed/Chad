"""
Model registry — declare a model here once and every Chad subsystem picks it up.

To add a new model:
  1. Add an entry to MODELS with its ModelProfile.
  2. Update model_capabilities.yaml with context_window and support flags
     (used by ModelCapabilityRegistry for context-budget and tool-result sizing).

To switch the active local model, change local_models.active_model in config.yaml
(or preferences.yaml) — the registry is looked up by whatever name LM Studio reports.
"""
from __future__ import annotations

from typing import Optional

from config.model_profiles import ModelProfile

MODELS: dict[str, ModelProfile] = {
    # ── Qwen 3.6 35B ─────────────────────────────────────────────────────────
    "qwen/qwen3.6-35b-a3b": ModelProfile(
        model_id="qwen/qwen3.6-35b-a3b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="qwen3",
        thinking_budget=32768,
        thinking_effort="medium",
        temperature=0.6,
        top_p=0.95,
        context_length=131072,
    ),

    # ── Qwen 3.6 27B ─────────────────────────────────────────────────────────
    "qwen/qwen3.6-27b": ModelProfile(
        model_id="qwen/qwen3.6-27b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="qwen3",
        thinking_budget=16384,
        thinking_effort="medium",
        temperature=0.6,
        top_p=0.95,
        context_length=32768,
    ),

    # ── Qwen 3.5 9B (tool selector + eval judge) ──────────────────────────────
    "qwen/qwen3.5-9b": ModelProfile(
        model_id="qwen/qwen3.5-9b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="qwen3",
        thinking_budget=8192,
        thinking_effort="minimal",
        temperature=0.0,
        top_p=0.8,
        context_length=32768,
        max_tokens=512,
    ),

    # ── Qwen 3 VL 8B (browser_agent GUI-grounding model, epic #274) ──────────
    # Vision-language model pinned to browser_agent. It reasons in the Qwen3
    # thinking channel, so it needs the qwen3 param style — without a profile the
    # adapter's legacy path leaves thinking OFF and reasoning_content never
    # surfaces to the UI (the thoughts vanish / leak inline as suppressed content).
    "qwen/qwen3-vl-8b": ModelProfile(
        model_id="qwen/qwen3-vl-8b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="qwen3",
        thinking_budget=8192,
        thinking_effort="medium",
        temperature=0.6,
        top_p=0.95,
        context_length=131072,
    ),

    # ── GPT-OSS 20B (browser_agent, issue #287) ───────────────────────────────
    # Pinned to browser_agent in place of qwen/qwen3-vl-8b: that model's LM Studio
    # build is the Instruct checkpoint with metadataOverrides.reasoning=false baked
    # in, so thinking_level requests were silently dropped and the model had zero
    # deliberation between actions — it retried an identical failing Playwright call
    # 10+ times in a row without ever re-checking page state. gpt-oss-20b actually
    # reasons (LM Studio model.yaml: reasoning=true, trainedForToolUse=true) and, in
    # a live test on the same task, adapted strategy after failures instead of
    # looping. It's text-only (no vision) — fine for the current DOM-first loop;
    # revisit with Qwen3-VL-8B-Thinking once vision grounding (#278) actually lands.
    "openai/gpt-oss-20b": ModelProfile(
        model_id="openai/gpt-oss-20b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="gpt_oss",
        thinking_effort="high",
        temperature=0.8,
        top_p=0.8,
        context_length=131072,
    ),

    # ── Fara-7B (screenshot-native CUA, issue #288 spike) ─────────────────────
    # Microsoft computer-use agent (Qwen2.5-VL base). Predicts coordinate actions
    # via <tool_call> computer_use JSON — NOT OpenAI-native tool_calls. Temp 0 and
    # context ≥15k per Fara README. GGUF id in LM Studio: microsoft_fara-7b.
    "microsoft_fara-7b": ModelProfile(
        model_id="microsoft_fara-7b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=False,
        thinking_mode=False,
        thinking_param_style=None,
        temperature=0.0,
        top_p=0.95,
        max_tokens=1024,
        context_length=16384,
    ),

    # ── Gemma 4 31B ───────────────────────────────────────────────────────────
    # Gemma 4 produces interleaved reasoning tokens but has no API toggle.
    # thinking_param_style=None → adapter skips the Qwen3-style param injection;
    # reasoning_content is still read from delta.reasoning_content if present.
    "google/gemma-4-31b-it-qat-q4_0": ModelProfile(
        model_id="google/gemma-4-31b-it-qat-q4_0",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style=None,
        temperature=0.7,
        top_p=0.95,
        context_length=32768,
    ),

    # ── Liquid Foundation Model (LFM) 2.5 24B ─────────────────────────────────
    "lfm2.5-24b-a2b": ModelProfile(
        model_id="lfm2.5-24b-a2b",
        provider="lmstudio",
        base_url="http://localhost:1234/v1",
        supports_system_message=True,
        supports_native_tool_calls=True,
        thinking_mode=True,
        thinking_param_style="qwen3",
        thinking_budget=8192,
        thinking_effort="medium",
        temperature=0.6,
        top_p=0.95,
        context_length=32000,
    ),
}

# Common LM Studio quantised-version suffixes (":2", ":3", …).  get_profile()
# strips these automatically so aliases aren't needed here.


def get_profile(model_id: str) -> Optional[ModelProfile]:
    """
    Look up a ModelProfile by model ID.

    Tries exact match first, then strips the quantisation-version suffix
    that LM Studio appends (e.g. "qwen/qwen3.6-35b-a3b:2" → "qwen/qwen3.6-35b-a3b").
    Returns None if the model is not in the registry (legacy path still handles it).
    """
    if model_id in MODELS:
        return MODELS[model_id]
    base = model_id.split(":")[0]
    return MODELS.get(base)
