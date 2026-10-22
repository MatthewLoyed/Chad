"""
LiteLLM Adapter — thin routing layer over 100+ LLM providers.
Keeps message history intact (native tool roles) so that capable models
can use OpenAI-standard tool calling without any compatibility hacks.
"""

import asyncio
import json
import logging
import inspect
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

import litellm
from .ai_adapter_base import AIAdapter, GenerateResponse, TokenUsage
from src.core.intelligence.nudges import (
    NarrationWithoutActionGuard,
    NudgeManager,
    NudgeResult,
    ReasoningRunawayGuard,
)
from src.core.intelligence.provider_capabilities import get_capabilities_for_url
from src.core.orchestration.context import emit_node_progress, await_with_progress_heartbeat
from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager

logger = setup_logger("LiteLLMAdapter")


# ── Hybrid streaming toggle (revert lever) ───────────────────────────────────
# When true (default): keep stream=true even on tool / structured-output turns.
#   • reasoning_content deltas  → forwarded to on_thinking immediately (live UI)
#   • tool_calls + content      → buffered until finish_reason, then parsed once
# When false: legacy behaviour from b0c54b5 — non-streaming on tool/schema turns.
#   Set CHAD_HYBRID_STREAMING=false in the environment to roll back instantly.
def hybrid_streaming_enabled() -> bool:
    return os.getenv("CHAD_HYBRID_STREAMING", "true").lower() in ("1", "true", "yes")


def requires_legacy_nonstreaming_structured(model_name: str) -> bool:
    """
    Qwen3 on LM Studio binds json_schema to the *reasoning* channel, leaving content
    empty (lmstudio-bug-tracker #1773). Hybrid streaming cannot fix that — stay on the
    one-shot non-streaming path for Qwen3 + response_format only.
    """
    return "qwen3" in (model_name or "").lower()


class LiteLLMAdapter(AIAdapter):
    """
    Routes requests to any LiteLLM-supported provider.
    Passes messages through without rewriting roles or flattening tool calls —
    modern models handle native tool calling correctly when given clean input.
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_instruction: str = "",
        supports_tools: bool = False,
        profile: Optional[Any] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.system_instruction = system_instruction
        self.supports_tools = supports_tools
        self.profile = profile  # Optional[ModelProfile] — drives quirk logic
        self.model_name = self._normalise_model_name(model_name, base_url)

        # LiteLLM global settings
        litellm.drop_params = True
        litellm.telemetry = False
        litellm.request_timeout = 300.0

        # Ensure a persistent global client session is configured to enable connection pooling
        if not getattr(litellm, "aclient_session", None):
            import httpx
            litellm.aclient_session = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
            )
            logger.info("[ LITELLM ] Global persistent httpx.AsyncClient session initialized for connection pooling")

        logger.info(f"[ LITELLM_MODEL ] Initialized {model_name}")

    # ── Model name normalisation ────────────────────────────────────────────────

    def _normalise_model_name(self, model_name: str, base_url: Optional[str]) -> str:
        """Add the minimal provider prefix LiteLLM needs to route correctly."""
        if not model_name:
            return model_name

        # Cloud Gemini — use the google genai prefix
        if "gemini" in model_name.lower() and "/" not in model_name:
            return f"gemini/{model_name}"

        # Local OpenAI-compatible endpoint (LM Studio / Ollama / vLLM)
        if base_url:
            known_prefixes = ("ollama/", "openai/", "lm_studio/", "huggingface/", "vllm/", "gemini/")
            if not any(model_name.startswith(p) for p in known_prefixes):
                prefix = "ollama/" if "ollama" in base_url.lower() else "openai/"
                model_name = f"{prefix}{model_name}"
                logger.info(f"[ LITELLM ] Auto-prefixed local model: {model_name}")

        return model_name

    # ── Readiness ────────────────────────────────────────────────────────────────

    async def is_ready(self) -> bool:
        try:
            if self.base_url and ("localhost" in self.base_url or "127.0.0.1" in self.base_url):
                import httpx
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get(f"{self.base_url}/models")
                    return r.status_code == 200
            return True
        except Exception:
            return False

    # ── Thinking helpers ─────────────────────────────────────────────────────────

    def _apply_profile_thinking(self, req: Dict[str, Any], kwargs: Dict[str, Any]) -> None:
        """Inject thinking params driven by self.profile (model is in registry)."""
        p = self.profile
        if not p.thinking_mode:
            return

        user_enabled = kwargs.get("thinking_enabled")
        thinking_level = kwargs.get("thinking_level") or p.thinking_effort

        if user_enabled is False or thinking_level == "off":
            if p.thinking_param_style == "qwen3":
                extra = req.get("extra_body") or {}
                extra["chat_template_kwargs"] = {"enable_thinking": False}
                extra["thinking_budget"] = 0
                req["extra_body"] = extra
                req["reasoning_effort"] = "none"
            elif p.thinking_param_style == "gpt_oss":
                req["reasoning_effort"] = "none"
                if get_capabilities_for_url(self.base_url).reasoning_effort_via_extra_body:
                    extra = req.get("extra_body") or {}
                    extra["reasoning_effort"] = "none"
                    req["extra_body"] = extra
            logger.debug(f"[ LITELLM ] Thinking explicitly disabled (level={thinking_level}, enabled={user_enabled})")
            return

        if p.thinking_param_style == "qwen3":
            level_map = {"minimal": "low", "medium": "medium", "high": "high"}
            reasoning_effort = level_map.get(thinking_level, "medium")
            req["reasoning_effort"] = reasoning_effort

            budget_map = {"minimal": 8192, "medium": 32768, "high": 262144}
            # Per-call thinking_level overrides profile default; profile default overrides hardcoded fallback
            if kwargs.get("thinking_level") and thinking_level in budget_map:
                budget = budget_map[thinking_level]
            else:
                budget = p.thinking_budget or budget_map.get(thinking_level, 32768)
            _enable = thinking_level not in ("minimal", "off")
            extra = req.get("extra_body") or {}
            extra["chat_template_kwargs"] = {"enable_thinking": _enable}
            extra["thinking_budget"] = budget
            req["extra_body"] = extra
            logger.debug(
                f"[ LITELLM ] Qwen3 thinking (profile): enable={_enable}, "
                f"budget={budget} tokens (level={thinking_level})"
            )
        elif p.thinking_param_style == "gpt_oss":
            # GPT-OSS (Harmony reasoning format) exposes reasoning depth via a
            # `reasoning_effort` jinja variable (see model.yaml customFields), not the
            # Qwen3 enable_thinking/thinking_budget pair. litellm has native
            # reasoning_effort passthrough for o-series-style reasoning models, so the
            # top-level param always goes out; backends whose endpoint only reads it
            # from extra_body (LM Studio — verified via evals/browser_playwright_probe.py
            # live testing) get it mirrored there too, per provider capability.
            level_map = {"minimal": "low", "medium": "medium", "high": "high"}
            reasoning_effort = level_map.get(thinking_level, "medium")
            req["reasoning_effort"] = reasoning_effort
            if get_capabilities_for_url(self.base_url).reasoning_effort_via_extra_body:
                extra = req.get("extra_body") or {}
                extra["reasoning_effort"] = reasoning_effort
                req["extra_body"] = extra
            logger.debug(f"[ LITELLM ] gpt-oss reasoning_effort={reasoning_effort} (level={thinking_level})")
        # style==None → model reasons on its own; no extra params needed

    def _apply_legacy_qwen3_thinking(self, req: Dict[str, Any], kwargs: Dict[str, Any]) -> None:
        """Qwen3 thinking injection for models NOT in the registry (config-file fallback)."""
        _model_cfg: Dict[str, Any] = {}
        try:
            local_cfg = ConfigManager.get("local_models") or {}
            _model_cfg = (local_cfg.get("model_settings") or {}).get(self.model_name, {})
        except Exception:
            pass

        _raw_thinking_level = kwargs.get("thinking_level")
        _thinking_cfg = _model_cfg.get("thinking")
        _user_thinking = kwargs.get("thinking_enabled")

        if _user_thinking is False:
            thinking_level = None
        elif _user_thinking is True:
            thinking_level = _raw_thinking_level or _model_cfg.get("thinking_level", "medium")
        elif _thinking_cfg is False:
            thinking_level = None
        elif _thinking_cfg is True and not _raw_thinking_level:
            thinking_level = _model_cfg.get("thinking_level", "medium")
        elif _thinking_cfg is None:
            thinking_level = None
        else:
            thinking_level = _raw_thinking_level

        if thinking_level:
            level_map = {"minimal": "low", "medium": "medium", "high": "high"}
            reasoning_effort = level_map.get(thinking_level)
            _cfg_effort = _model_cfg.get("reasoning_effort")
            if reasoning_effort:
                req["reasoning_effort"] = _cfg_effort or reasoning_effort
                logger.debug(f"[ LITELLM ] reasoning_effort={req['reasoning_effort']} (thinking_level={thinking_level})")

            budget_map = {"minimal": 8192, "medium": 32768, "high": 262144}
            budget = budget_map.get(thinking_level, 32768)
            _cfg_budget = _model_cfg.get("thinking_budget")
            _enable = thinking_level != "minimal"
            extra = req.get("extra_body") or {}
            extra["chat_template_kwargs"] = {"enable_thinking": _enable}
            extra["thinking_budget"] = _cfg_budget or budget
            req["extra_body"] = extra
            logger.debug(
                f"[ LITELLM ] Qwen3 thinking (legacy): enable={_enable}, "
                f"budget={extra['thinking_budget']} tokens (level={thinking_level})"
            )

    # ── Main generation ─────────────────────────────────────────────────────────

    def _gate_priority(self) -> str:
        """Priority tag sent to lmstudio-gate for this call. An explicit override
        (``_chad_priority``, set by e.g. the eval judge adapter) wins; otherwise use
        the turn's model-lifecycle priority (interactive vs background)."""
        override = getattr(self, "_chad_priority", None)
        if override:
            return str(override)
        try:
            from src.core.intelligence.model_lifecycle import current_priority
            return current_priority()
        except Exception:
            return "interactive"

    def _apply_gate_routing(self, req: Dict[str, Any]) -> None:
        """Point this request's api_base at lmstudio-gate (when up) and tag its
        priority. No-op fallback to the direct URL if the gate is disabled/down."""
        try:
            from src.infrastructure.llm.gate_client import inference_base_url
            routed = inference_base_url(self.base_url)
        except Exception:
            routed = self.base_url
        if routed and routed != self.base_url:
            req["api_base"] = routed
            req["base_url"] = routed
        headers = dict(req.get("extra_headers") or {})
        headers["X-Chad-Priority"] = self._gate_priority()
        req["extra_headers"] = headers

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[..., Any]] = None,
        on_token: Optional[Callable[..., Any]] = None,
        agent_name: Optional[str] = None,
        **kwargs,
    ) -> GenerateResponse:
        """Marks the model actively in-use for the full duration of the call (including the
        whole streaming loop) so the model-lifecycle coordinator's VRAM-budget eviction
        (model_lifecycle.py) can never unload it mid-generation. No-ops for cloud adapters
        (base_url is None) — see `mark_active`'s own falsy-arg guard."""
        from src.core.intelligence.model_lifecycle import mark_active, mark_inactive
        await mark_active(self.base_url, self.model_name)
        try:
            return await self._generate_impl(
                messages, tools, temperature, on_thinking, on_token, agent_name, **kwargs
            )
        finally:
            await mark_inactive(self.base_url, self.model_name)

    async def _generate_impl(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[..., Any]] = None,
        on_token: Optional[Callable[..., Any]] = None,
        agent_name: Optional[str] = None,
        **kwargs,
    ) -> GenerateResponse:
        # Eval dry-run hard gate (#632): a real LiteLLM call with the main chat model
        # (Gemma 26B) would JIT-load it in LM Studio alongside the 9B selector. Refuse
        # before the HTTP request so SpyAdapter-only evals cannot trigger that load.
        import os
        if os.getenv("CHAD_TESTING") == "true":
            from src.shared.config_manager import ConfigManager
            from src.core.intelligence.providers.base import same_model
            allowed = ConfigManager.get_llm_selector_model()
            if allowed and not same_model(self.model_name, allowed):
                logger.error(
                    f"[ LITELLM ] EVAL HARD GATE refused generate with '{self.model_name}' "
                    f"(only selector '{allowed}' allowed under CHAD_TESTING)."
                )
                return GenerateResponse(
                    text="[eval: non-selector model generate blocked]",
                    tool_calls=None,
                    finish_reason="stop",
                )

        logger.info(f"[ LITELLM ] Generating with model: {self.model_name}")

        # 1. Build final message list — prepend system instruction, transform Chad internals
        final_messages = self._build_messages(messages)

        logger.info(f"[ LITELLM ] -> Generating with {len(final_messages)} messages (Adapter: {self.model_name})")

        # 2. Convert tools to OpenAI function-calling format
        final_tools = self.convert_tool_schemas(tools) if tools else None

        # 3. Build request
        req = {
            "model": self.model_name,
            "messages": final_messages,
            "temperature": temperature,
            "stream": True,
            "api_key": self.api_key or (get_capabilities_for_url(self.base_url).default_api_key if self.base_url else None),
            "api_base": self.base_url,
            "base_url": self.base_url,
            "stream_options": {"include_usage": True}
        }
        if self.base_url:
            # Keep local model (LM Studio / Ollama) loaded indefinitely
            req["extra_body"] = {"keep_alive": -1}
            # Route local inference through lmstudio-gate (cross-process priority queue,
            # issue #478) when it's up; falls back to the direct base_url otherwise.
            # NOTE: self.base_url stays the REAL LM Studio URL so mark_active/mark_inactive
            # (above) keep keying the model-lifecycle VRAM guard against the true endpoint.
            self._apply_gate_routing(req)
        if final_tools:
            req["tools"] = final_tools
            # Allow the model to emit multiple independent tool calls in one turn.
            # The graph/tool coordinator already executes a batch concurrently
            # (asyncio.gather), so parallel emission collapses N sequential
            # model round-trips into one. litellm.drop_params=True makes this a
            # no-op on backends that don't support the flag. Callers can override
            # (e.g. force serial) by passing parallel_tool_calls=False.
            _parallel = kwargs.get("parallel_tool_calls", True)
            if _parallel is not None:
                req["parallel_tool_calls"] = _parallel

        # 3b. Thinking / reasoning injection for local models.
        # Profile-registered models use ModelProfile.thinking_param_style.
        # Models not in the registry fall back to config-based Qwen3 detection.
        if self.base_url:
            if kwargs.get("thinking_level") == "off" or kwargs.get("thinking_enabled") is False:
                req["reasoning_effort"] = "none"
                extra = req.get("extra_body") or {}
                extra["reasoning_effort"] = "none"
                extra["chat_template_kwargs"] = {"enable_thinking": False}
                extra["thinking_budget"] = 0
                req["extra_body"] = extra
                logger.debug(f"[ LITELLM ] Thinking disabled for {self.model_name}: reasoning_effort=none")
            elif self.profile is not None:
                self._apply_profile_thinking(req, kwargs)
            elif "qwen3" in self.model_name.lower():
                self._apply_legacy_qwen3_thinking(req, kwargs)

        _extra_litellm = kwargs.get("extra_litellm_body")
        if _extra_litellm:
            extra = req.get("extra_body") or {}
            extra.update(_extra_litellm)
            req["extra_body"] = extra

        _max_tokens = kwargs.get("max_tokens")
        if _max_tokens is not None:
            req["max_tokens"] = _max_tokens

        # 3c-1. Structured output (response_format). Forwarded explicitly because req is built
        # from a fixed key set — arbitrary kwargs are otherwise dropped. litellm.drop_params=True
        # makes this safe for providers that don't support it. Used by the planner to get a clean
        # JSON plan in `content` instead of parsing free-form text.
        # NOTE: on LM Studio, Qwen3 *thinking* models apply json_schema to the reasoning stream
        # (leaving content empty), so callers MUST disable thinking when using response_format.
        _response_format = kwargs.get("response_format")
        if _response_format:
            req["response_format"] = _response_format

        # 3c-2. Streaming mode selection (hybrid vs legacy).
        #
        # REVERT: set CHAD_HYBRID_STREAMING=false to restore pre-hybrid behaviour
        # (non-streaming on every tool-bound or response_format turn).
        #
        # Hybrid (default): stream=true always *except* Qwen3 + response_format.
        #   • Live UI: reasoning_content deltas fire on_thinking per chunk (below).
        #   • Reliability: tool_calls arguments and plan/content JSON are *buffered*
        #     during the stream and only parsed after finish_reason — same end result
        #     as the old non-streaming blob, without losing live reasoning.
        # Legacy: stream=false when tools or response_format are attached (b0c54b5).
        use_streaming = self._resolve_use_streaming(
            has_tools=bool(final_tools),
            has_response_format=bool(_response_format),
        )
        req["stream"] = use_streaming
        if use_streaming:
            req["stream_options"] = {"include_usage": True}
            if final_tools:
                # Grammar-constrained tool args where the provider supports it.
                req.setdefault("tool_choice", kwargs.get("tool_choice", "auto"))
            if final_tools or _response_format:
                logger.debug(
                    f"[ LITELLM ] Hybrid streaming ON "
                    f"(tools={bool(final_tools)}, schema={bool(_response_format)}, "
                    f"hybrid={hybrid_streaming_enabled()})"
                )
        else:
            req.pop("stream_options", None)
            if final_tools:
                req.setdefault("tool_choice", kwargs.get("tool_choice", "auto"))
            logger.debug(
                f"[ LITELLM ] Legacy non-streaming path "
                f"(tools={bool(final_tools)}, schema={bool(_response_format)})"
            )

        # Remove None values
        req = {k: v for k, v in req.items() if v is not None}

        # 4. Debug prompt dump
        self._dump_prompt(final_messages, final_tools)

        # 5. Context-window pre-flight: warn if estimated prompt is close to the model's limit
        try:
            model_info = litellm.get_model_info(self.model_name)
            ctx_limit = None
            if model_info:
                ctx_limit = model_info.get("max_input_tokens") or model_info.get("max_tokens")
            if ctx_limit:
                prompt_tokens = litellm.token_counter(model=self.model_name, messages=final_messages)
                pct = round(prompt_tokens / ctx_limit * 100)
                if pct >= 90:
                    logger.warning(
                        f"[ LITELLM ] ⚠️  CONTEXT WINDOW: {prompt_tokens:,} / {ctx_limit:,} tokens "
                        f"({pct}% full). Response may be truncated or the request may fail."
                    )
                else:
                    logger.debug(f"[ LITELLM ] Context window: {prompt_tokens:,} / {ctx_limit:,} ({pct}%)")
        except Exception:
            pass  # Model info unavailable for this provider — skip preflight

        # 5b. Legacy non-streaming path — kept for revert (CHAD_HYBRID_STREAMING=false)
        # and for Qwen3 + response_format where LM Studio cannot stream schema safely.
        if not use_streaming:
            return await self._generate_nonstreaming(req, on_thinking, agent_name)

        # 6. Hybrid streaming path
        # Reasoning → UI live. Tool args / structured content → buffer only, parse at end.
        full_content = ""
        full_reasoning = ""
        tool_calls_buffer: Dict[int, Dict] = {}
        finish_reason = "stop"
        last_obj = None
        response = None  # declared here so CancelledError handler can close it
        defer_content_tokens = bool(_response_format)
        # State for inline <think>...</think> reasoning (Gemma and similar models that embed
        # reasoning in content rather than a dedicated reasoning_content field).
        _in_think_block = False

        # Reasoning-token runaway guard (issue #245) — local models only, one retry max.
        # New nudge types register here without touching the streaming loop below.
        nudge_manager: Optional[NudgeManager] = None
        if self.base_url:
            from src.core.tools import limits
            from src.core.orchestration.context import agent_name_var

            curr_agent = (agent_name or agent_name_var.get("") or "").lower()
            # Narration guard only applies to execution nodes (Supervisor / execution agents).
            # Worker limbs like Scout and Planner report text briefs / plans and must not be nudged.
            is_gather_limb = curr_agent in ("scout", "planner")

            guards = [ReasoningRunawayGuard(model_name=self.model_name)]
            if not kwargs.get("_nudge_retried") and not is_gather_limb and bool(limits.get("narration_nudge_enabled")):
                guards.append(
                    NarrationWithoutActionGuard(
                        enabled=bool(limits.get("narration_nudge_enabled"))
                    )
                )
            nudge_manager = NudgeManager(guards)

        try:
            response = await await_with_progress_heartbeat(litellm.acompletion(**req))

            # Placeholder pulse only for models that never emit reasoning_content.
            if not self._model_emits_reasoning_stream() and on_thinking:
                await self._trigger(on_thinking, "...", type="thought", append=True, agent_name=agent_name)

            usage_obj = None
            # LM Studio/litellm send `usage` (incl. completion_tokens_details.reasoning_tokens)
            # in a trailing chunk that arrives AFTER the finish_reason chunk. Breaking the
            # instant finish_reason is seen (old behaviour) exits the loop before that trailing
            # chunk is ever read, silently dropping usage. Keep listening for a few more chunks
            # once finish_reason fires so the usage-only chunk gets a chance to arrive.
            finish_reason_hit = False
            post_finish_chunks = 0
            chunk_count = 0
            async for chunk in response:
                chunk_count += 1
                if chunk_count % 20 == 0:
                    emit_node_progress()
                last_obj = chunk

                # Capture usage from the dedicated usage chunk (which typically has empty choices)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_obj = chunk.usage
                    if finish_reason_hit:
                        break

                if not getattr(chunk, "choices", None) or not chunk.choices:
                    if finish_reason_hit:
                        post_finish_chunks += 1
                        if post_finish_chunks >= 3:
                            break
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                # ── LIVE PATH: reasoning → on_thinking immediately ──────────────
                reasoning_chunk = self._get_reasoning(delta)
                if reasoning_chunk:
                    full_reasoning += reasoning_chunk
                    if on_thinking:
                        await self._trigger(on_thinking, reasoning_chunk, type="reasoning", append=True, agent_name=agent_name)

                # ── BUFFER PATH: content (plan JSON / speech) — parse at stream end ──
                # Reset per-delta so nudges see only THIS chunk's visible text; a
                # stale value would let a content chunk be counted twice.
                content_delta = ""
                inline_think_chunk = ""
                if delta.content:
                    raw_chunk = delta.content
                    # Models like Gemma embed reasoning in <think>...</think> inside content
                    # rather than using a dedicated reasoning_content field.  Route those
                    # segments to on_thinking/full_reasoning and keep the rest as content.
                    if "<think>" in raw_chunk or _in_think_block:
                        content_part, think_part, _in_think_block = self._split_inline_thinking(
                            raw_chunk, _in_think_block
                        )
                        if think_part:
                            inline_think_chunk = think_part
                            full_reasoning += think_part
                            if on_thinking:
                                await self._trigger(on_thinking, think_part, type="reasoning", append=True, agent_name=agent_name)
                        raw_chunk = content_part
                    if raw_chunk:
                        full_content += raw_chunk
                        content_delta = raw_chunk
                        # On tool/schema turns, defer on_token so partial JSON never hits the UI.
                        if on_token and not defer_content_tokens:
                            await self._trigger(on_token, raw_chunk)

                # ── BUFFER PATH: tool_calls — assemble deltas, parse at stream end ──
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc.id:
                            tool_calls_buffer[idx]["id"] += tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buffer[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_calls_buffer[idx]["arguments"] += tc.function.arguments

                if nudge_manager:
                    nudge_result = nudge_manager.observe(
                        reasoning_chunk=reasoning_chunk or inline_think_chunk or "",
                        has_tool_call=bool(tool_calls_buffer),
                        # Visible answer text from THIS delta only, never reasoning.
                        content_chunk=content_delta,
                        tools_available=bool(tools),
                    )
                    if nudge_result:
                        logger.warning(
                            f"[ LITELLM ] Nudge '{nudge_result.reason}' triggered for "
                            f"{self.model_name} — cancelling stream and resending."
                        )
                        try:
                            await response.aclose()
                        except Exception as close_err:
                            logger.debug(f"[ LITELLM ] Could not close streaming response on nudge: {close_err}")
                        if kwargs.get("_nudge_retried"):
                            # The retry allowance is exhausted, not the watchdog.
                            # Stop honestly rather than leaving the retried stream
                            # unlimited or starting a third generation.
                            return GenerateResponse(text=None, tool_calls=None, finish_reason="error",
                                raw=f"Reasoning budget exceeded after corrective retry ({nudge_result.reason})")
                        return await self._resend_with_nudge(
                            messages, tools, temperature, on_thinking, on_token, agent_name, nudge_result, kwargs
                        )

                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                    finish_reason_hit = True
                    post_finish_chunks += 1
                    if usage_obj is not None or post_finish_chunks >= 3:
                        break

            # ── END-OF-STREAM PARSE: tool args + structured content (reliable blob) ──
            parsed_tool_calls = self._parse_tool_calls(tool_calls_buffer) if tool_calls_buffer else None

            # Flush deferred speech/content in one shot (planner JSON is read from
            # GenerateResponse.text by callers; on_token is for live speech UI only).
            if defer_content_tokens and full_content and on_token:
                await self._trigger(on_token, full_content)

            # Froggeric dirty parser: LM Studio sometimes streams XML tool calls as delta.content
            # instead of converting them to native delta.tool_calls.
            #
            # Two formats observed in the wild:
            #   Wrapped:  <tool_call><function=NAME>...</function></tool_call>
            #   Bare:     <function=NAME>...</function>   ← Qwen3.5-9B via LM Studio
            #
            # Gated on has_native_tool_parser=False (LM Studio) — other backends
            # (Ollama, vLLM, llama.cpp) resolve native tool_calls reliably and never
            # need this froggeric-template safety net.
            if (
                not parsed_tool_calls
                and full_content
                and "<function=" in full_content
                and not get_capabilities_for_url(self.base_url).has_native_tool_parser
            ):
                import re as _re
                known_names = [
                    t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
                    for t in (final_tools or [])
                ]
                known_names = [n for n in known_names if n]

                def _parse_froggeric_calls(pattern: str, text: str) -> list:
                    calls = []
                    for _match in _re.finditer(pattern, text, _re.DOTALL):
                        func_name = _match.group(1).strip()
                        if known_names and func_name not in known_names:
                            continue
                        args = {}
                        for _pm in _re.finditer(r'<parameter=([^>]+)>(.*?)</parameter>', _match.group(2), _re.DOTALL):
                            key, val = _pm.group(1).strip(), _pm.group(2).strip()
                            try:
                                args[key] = json.loads(val)
                            except Exception:
                                args[key] = val
                        calls.append({"id": f"froggeric_{int(time.time())}_{func_name}", "name": func_name, "args": args})
                    return calls

                # Try wrapped form first (<tool_call> wrapper present)
                _calls = _parse_froggeric_calls(
                    r'<tool_call>\s*<function=([^>]+)>(.*?)</function>\s*</tool_call>',
                    full_content,
                )
                _variant = "wrapped"
                # Fall through to bare form if wrapped produced nothing
                if not _calls:
                    _calls = _parse_froggeric_calls(
                        r'<function=([^>]+)>(.*?)</function>',
                        full_content,
                    )
                    _variant = "bare"

                if _calls:
                    logger.info(
                        f"[ LITELLM ] Froggeric {_variant} parser extracted "
                        f"{len(_calls)} tool call(s): {[c['name'] for c in _calls]}"
                    )
                    parsed_tool_calls = _calls
                    full_content = ""

            # Convert usage metric to standardized dataclass
            normalized_usage = self._normalize_usage(usage_obj)

            return GenerateResponse(
                text=full_content or None,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                reasoning=full_reasoning or None,
                raw=last_obj,
                usage=normalized_usage
            )

        except asyncio.CancelledError:
            logger.warning("[ LITELLM ] Generation cancelled mid-stream — closing streaming connection")
            if response is not None:
                try:
                    if hasattr(response, "aclose"):
                        await response.aclose()
                    elif hasattr(response, "close"):
                        response.close()
                except Exception as close_err:
                    logger.debug(f"[ LITELLM ] Could not close streaming response on cancel: {close_err}")
            raise
        except Exception as e:
            clean_err = self._clean_error_message(e)
            logger.error(f"[ LITELLM ] Generation failed: {clean_err}")
            return GenerateResponse(text=None, tool_calls=None, finish_reason="error", raw=str(e))

    async def _resend_with_nudge(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        on_thinking: Optional[Callable[..., Any]],
        on_token: Optional[Callable[..., Any]],
        agent_name: Optional[str],
        nudge_result: NudgeResult,
        kwargs: Dict[str, Any],
    ) -> GenerateResponse:
        """Append a nudge's corrective message and resend once, with thinking disabled per the nudge."""
        nudged_messages = list(messages) + [{"role": "user", "content": nudge_result.message}]
        retry_kwargs = {k: v for k, v in kwargs.items() if k not in ("thinking_enabled", "_nudge_retried")}
        if nudge_result.disable_thinking:
            retry_kwargs["thinking_enabled"] = False
        retry_kwargs["_nudge_retried"] = True  # one retry max — never nudge a nudge resend
        return await self.generate(
            nudged_messages,
            tools=tools,
            temperature=temperature,
            on_thinking=on_thinking,
            on_token=on_token,
            agent_name=agent_name,
            **retry_kwargs,
        )

    # ── Streaming mode helpers ───────────────────────────────────────────────────

    def _resolve_use_streaming(self, has_tools: bool, has_response_format: bool) -> bool:
        """
        Decide stream=true vs stream=false for this request.

        Hybrid (CHAD_HYBRID_STREAMING=true, default):
          stream on for tool + schema turns so reasoning_content can reach the UI live.
          Tool arguments and schema-bound content are still parsed only after the
          stream completes (see section 6 in generate()).

        Legacy (CHAD_HYBRID_STREAMING=false):
          Identical to b0c54b5 — non-streaming whenever tools or response_format
          are attached. Instant revert if hybrid causes regressions.

        Exception: Qwen3 + response_format always uses legacy non-streaming because
        LM Studio applies json_schema to the reasoning channel on that model family.
        """
        needs_structured_blob = has_tools or has_response_format
        if not needs_structured_blob:
            return True
        if not hybrid_streaming_enabled():
            return False
        if has_response_format and requires_legacy_nonstreaming_structured(self.model_name):
            return False
        return True

    def _model_emits_reasoning_stream(self) -> bool:
        """Models known to populate delta.reasoning_content during streaming."""
        name = self.model_name.lower()
        if self.profile is not None and getattr(self.profile, "thinking_mode", False):
            return True
        return any(token in name for token in ("o1", "o3", "thinking", "qwen3", "gemma"))

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _build_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Prepend system instruction if needed, then sanitise each message so it
        conforms to what OpenAI-compatible endpoints (LM Studio included) expect:
          - content_blocks  → transform to text/image_url content
          - tool_calls      → ensure each entry has 'type': 'function'
          - empty content   → replace with single space to pass validators
        """
        normalized = []
        for raw in messages:
            msg = dict(raw)

            # Transform Chad's internal content_blocks to OpenAI content format
            if "content_blocks" in msg:
                existing = msg.get("content", "")
                blocks = list(msg.pop("content_blocks"))
                if existing and str(existing).strip():
                    if not any(b.get("type") == "text" and b.get("text") == existing for b in blocks):
                        blocks.insert(0, {"type": "text", "text": str(existing)})
                msg["content"] = self.transform_content_blocks(blocks)

            # Ensure tool_calls have the required OpenAI format: {'id': ..., 'type': 'function', 'function': {'name': ..., 'arguments': ...}}
            if msg.get("tool_calls"):
                fixed = []
                for tc in msg["tool_calls"]:
                    entry = dict(tc)
                    entry["type"] = "function"
                    if "function" not in entry:
                        args = entry.get("args", {})
                        args_str = json.dumps(args) if isinstance(args, (dict, list)) else str(args or "{}")
                        entry["function"] = {
                            "name": entry.get("name", ""),
                            "arguments": args_str,
                        }
                    fixed.append(entry)
                msg["tool_calls"] = fixed

            # Ensure no null content (LM Studio requires content to be a string or array)
            if msg.get("content") is None and msg.get("role") != "tool":
                msg["content"] = ""
            elif not msg.get("content") and not msg.get("tool_calls") and msg.get("role") != "tool":
                msg["content"] = " "

            normalized.append(msg)

        # Prepend system instruction only if no SystemMessage was already provided.
        #
        # The supervisor (LangGraph path) builds a rich, turn-specific system prompt via
        # SystemPromptBuilder.build() and passes it as a SystemMessage in the message list.
        # We MUST NOT overwrite or merge that with self.system_instruction (which is a
        # static adapter-level fallback set at init time).
        #
        # self.system_instruction is only used as a last-resort fallback for non-LangGraph
        # code paths (e.g. direct adapter calls without a supervisor) that send no system message.
        first = normalized[0] if normalized else None
        if first and first.get("role") == "system":
            # A dynamic SystemMessage already exists — trust it completely, do not touch it.
            logger.debug(f"[ LITELLM ] Dynamic SystemMessage present ({len(str(first.get('content', '')))} chars). Trusting LangGraph path.")
            return normalized

        # No SystemMessage in the list — fall back to the static adapter-level instruction.
        if self.system_instruction:
            logger.debug(f"[ LITELLM ] No SystemMessage found. Prepending static system_instruction as fallback.")
            return [{"role": "system", "content": self.system_instruction}] + normalized

        return normalized


    async def _generate_nonstreaming(
        self,
        req: Dict[str, Any],
        on_thinking: Optional[Callable[..., Any]],
        agent_name: Optional[str],
    ) -> GenerateResponse:
        """
        Non-streaming generation for tool-calling turns.

        LM Studio (and other OpenAI-compatible endpoints) return a single, fully-formed
        message with a structured `tool_calls` array — no delta concatenation, so tool
        arguments arrive as complete JSON. This eliminates the streaming failure mode where
        split argument tokens produced malformed JSON that was silently coerced to `{}`.
        """
        response = None
        try:
            response = await await_with_progress_heartbeat(litellm.acompletion(**req))
            choice = response.choices[0]
            msg = choice.message

            content = getattr(msg, "content", None)
            reasoning = self._get_reasoning(msg)

            # Gemma and similar models embed reasoning as <think>...</think> in content
            # rather than a dedicated field.  Extract it when no native reasoning field found.
            if not reasoning and content and "<think>" in content:
                content, reasoning = self._strip_inline_thinking_from_content(content)

            # Surface reasoning once (no streaming on tool turns) so the UI still shows it.
            if reasoning and on_thinking:
                await self._trigger(on_thinking, reasoning, type="reasoning", append=True, agent_name=agent_name)

            tool_calls = None
            raw_tcs = getattr(msg, "tool_calls", None)
            if raw_tcs:
                tool_calls = []
                for i, tc in enumerate(raw_tcs):
                    fn = getattr(tc, "function", None)
                    if not fn or not getattr(fn, "name", None):
                        continue
                    args = self._repair_args(getattr(fn, "arguments", None))
                    tool_calls.append({
                        "id": getattr(tc, "id", None) or f"call_{i}",
                        "name": fn.name,
                        "args": args,
                    })

            return GenerateResponse(
                text=content or None,
                tool_calls=tool_calls or None,
                finish_reason=choice.finish_reason or "stop",
                reasoning=reasoning or None,
                raw=response,
                usage=self._normalize_usage(getattr(response, "usage", None)),
            )
        except asyncio.CancelledError:
            logger.warning("[ LITELLM ] Non-streaming generation cancelled")
            raise
        except Exception as e:
            clean_err = self._clean_error_message(e)
            logger.error(f"[ LITELLM ] Non-streaming generation failed: {clean_err}")
            return GenerateResponse(text=None, tool_calls=None, finish_reason="error", raw=str(e))

    def _normalize_usage(self, usage_obj) -> Optional[TokenUsage]:
        """Convert a provider usage object into Chad's standardized TokenUsage."""
        if not usage_obj:
            return None
        # Reasoning-token detail (present on reasoning models / LM Studio Qwen3.x)
        reasoning_tokens = None
        details = getattr(usage_obj, "completion_tokens_details", None)
        if details:
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
        # Dynamic context-window lookup via LiteLLM mapping
        context_window = None
        try:
            model_info = litellm.get_model_info(self.model_name)
            if model_info and "max_input_tokens" in model_info:
                context_window = model_info["max_input_tokens"]
            elif model_info and "max_tokens" in model_info:
                context_window = model_info["max_tokens"]
        except Exception:
            pass
        return TokenUsage(
            prompt_tokens=getattr(usage_obj, "prompt_tokens", 0),
            completion_tokens=getattr(usage_obj, "completion_tokens", 0),
            total_tokens=getattr(usage_obj, "total_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            context_window=context_window,
        )

    @staticmethod
    def _sanitize_token_bleed(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generic protocol normalizer: cleans ChatML/Qwen/XML control token artifacts
        (e.g. </parameter>, <parameter=...>) leaked into argument values.
        """
        import re
        cleaned: Dict[str, Any] = {}
        for key, val in args.items():
            if isinstance(val, str) and ("</parameter>" in val or "<parameter=" in val):
                # Check if this value actually contains multiple parameters packed together:
                # e.g. "</parameter>\n<parameter=boxes>\ntrue"
                parts = re.findall(r'<parameter=([^>]+)>(.*?)(?:</parameter>|$)', val, re.DOTALL)
                if parts:
                    for p_key, p_val in parts:
                        p_val_clean = p_val.strip()
                        if p_val_clean.lower() == "true":
                            cleaned[p_key.strip()] = True
                        elif p_val_clean.lower() == "false":
                            cleaned[p_key.strip()] = False
                        else:
                            try:
                                cleaned[p_key.strip()] = json.loads(p_val_clean)
                            except Exception:
                                cleaned[p_key.strip()] = p_val_clean
                    # Strip any trailing parameter tags from the original key's value
                    val_prefix = val.split("</parameter>")[0].strip()
                    val_prefix = re.sub(r'<parameter=[^>]+>', '', val_prefix).strip()
                    if val_prefix:
                        cleaned[key] = val_prefix
                    continue
                else:
                    # Just strip orphan tags
                    val_cleaned = re.sub(r'</?parameter(?:=[^>]+)?>', '', val).strip()
                    cleaned[key] = val_cleaned
            else:
                cleaned[key] = val
        return cleaned

    @staticmethod
    def _repair_args(raw: Any) -> Dict[str, Any]:
        """
        Parse tool-call arguments, repairing malformed JSON instead of silently dropping it.

        Order: already-a-dict → raw XML check → strict json.loads → json_repair → raw passthrough.
        We never return an empty dict on parse failure: an empty-args tool call looks
        valid but executes with no parameters (the original 'mangled tool call' bug).
        Surfacing {"_raw": ...} lets the model see its own malformed output and self-correct.
        """
        if not raw:
            return {}
        if isinstance(raw, dict):
            return LiteLLMAdapter._sanitize_token_bleed(raw)

        # Handle raw XML parameter bleed before JSON parsing (Qwen ChatML format)
        if isinstance(raw, str) and "<parameter=" in raw and not raw.strip().startswith("{"):
            import re
            extracted = {}
            for match in re.finditer(r'<parameter=([^>]+)>(.*?)(?:</parameter>|$)', raw, re.DOTALL):
                k, v = match.group(1).strip(), match.group(2).strip()
                try:
                    extracted[k] = json.loads(v)
                except Exception:
                    extracted[k] = v
            if extracted:
                return LiteLLMAdapter._sanitize_token_bleed(extracted)

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return LiteLLMAdapter._sanitize_token_bleed(parsed)
            return {"_raw": parsed}
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            from json_repair import repair_json
            repaired = repair_json(raw, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                logger.warning(f"[ LITELLM ] Repaired malformed tool args via json_repair: {str(raw)[:120]!r}")
                return LiteLLMAdapter._sanitize_token_bleed(repaired)
        except ImportError:
            logger.warning("[ LITELLM ] json_repair not installed — cannot repair malformed tool args (pip install json-repair).")
        except Exception as e:
            logger.warning(f"[ LITELLM ] json_repair failed on tool args: {e}")
        logger.error(f"[ LITELLM ] Tool args unparseable; passing raw to model for self-correction: {str(raw)[:200]!r}")
        return {"_raw": raw}

    def _parse_tool_calls(self, buffer: Dict[int, Dict]) -> List[Dict[str, Any]]:
        result = []
        for idx in sorted(buffer.keys()):
            tc = buffer[idx]
            args = self._repair_args(tc["arguments"])
            result.append({"id": tc["id"] or f"call_{idx}", "name": tc["name"], "args": args})
        return result

    @staticmethod
    def _get_reasoning(delta) -> Optional[str]:
        """Extract reasoning/thinking tokens from a streaming delta (provider-agnostic)."""
        for attr in ("reasoning_content", "thought", "reasoning"):
            val = getattr(delta, attr, None) if not isinstance(delta, dict) else delta.get(attr)
            if val:
                return val
        if hasattr(delta, "model_dump"):
            dump = delta.model_dump()
            for key in ("reasoning_content", "thought", "reasoning"):
                if dump.get(key):
                    return dump[key]
        return None

    @staticmethod
    def _split_inline_thinking(text: str, in_think: bool) -> tuple:
        """
        Split a streaming content chunk that may contain <think>...</think> tags.

        Returns (content_text, thinking_text, new_in_think_state).
        Tags can span chunk boundaries — in_think tracks whether we entered a
        think block in a previous chunk and haven't seen </think> yet.
        """
        content_out = ""
        thinking_out = ""
        remaining = text
        while remaining:
            if not in_think:
                idx = remaining.find("<think>")
                if idx == -1:
                    content_out += remaining
                    break
                content_out += remaining[:idx]
                remaining = remaining[idx + 7:]  # len("<think>") == 7
                in_think = True
            else:
                idx = remaining.find("</think>")
                if idx == -1:
                    thinking_out += remaining
                    break
                thinking_out += remaining[:idx]
                remaining = remaining[idx + 8:]  # len("</think>") == 8
                in_think = False
        return content_out, thinking_out, in_think

    @staticmethod
    def _strip_inline_thinking_from_content(content: str) -> tuple:
        """
        Extract all <think>...</think> blocks from a complete (non-streaming) content string.

        Also captures unclosed trailing <think> blocks (stream cut short).
        Returns (cleaned_content, reasoning_text).
        """
        import re as _re
        thinking_parts = _re.findall(r"<think>(.*?)</think>", content, _re.DOTALL)
        cleaned = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL)
        # Capture and strip any unclosed trailing <think> block
        unclosed = _re.search(r"<think>(.*?)$", cleaned, _re.DOTALL)
        if unclosed:
            thinking_parts.append(unclosed.group(1))
            cleaned = cleaned[:unclosed.start()]
        cleaned = cleaned.strip() or None
        reasoning = "\n".join(thinking_parts).strip() or None
        return cleaned, reasoning

    def _dump_prompt(self, messages, tools):
        """Write outbound prompt to disk for diagnostics. Appends so supervisor + sub-agents all visible."""
        try:
            import datetime
            path = Path("docs/logs/litellm_prompt_dump.txt")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n\n{'='*60}\n")
                f.write(f"[ LITELLM PROMPT DUMP ] {datetime.datetime.now().strftime('%H:%M:%S')}\n")
                f.write(f"MODEL: {self.model_name}\n")
                f.write(f"BASE_URL: {self.base_url}\n")
                f.write("--- MESSAGES ---\n")
                f.write(json.dumps(messages, indent=2))
                f.write("\n--- TOOLS (count) ---\n")
                f.write(f"{len(tools) if tools else 0} tools bound\n")
                f.write(f"{'='*60}\n")
            logger.info(f"[ LITELLM ] Outbound prompt appended to {path}")
        except Exception as e:
            logger.debug(f"[ LITELLM ] Prompt dump failed: {e}")


    async def _trigger(self, callback, *args, **kwargs):
        """Call a callback, filtering kwargs to its actual signature."""
        emit_node_progress()
        if not callback:
            return
        try:
            sig = inspect.signature(callback)
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if not has_var_kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            res = callback(*args, **kwargs)
            if res is not None and inspect.isawaitable(res):
                await res
        except Exception as e:
            logger.error(f"[ LITELLM ] Callback failed: {e}", exc_info=True)

    # ── AIAdapter protocol ───────────────────────────────────────────────────────

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Chad's tool schema list to OpenAI function-calling format."""
        converted = []
        for s in schemas:
            if isinstance(s, dict) and "function" in s and isinstance(s["function"], dict):
                converted.append(s)
            elif isinstance(s, dict) and s.get("name"):
                converted.append({
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s.get("description", ""),
                        "parameters": s.get("parameters", {"type": "object", "properties": {}}),
                    },
                })
        return converted

    def build_tool_result_message(self, tool_call_id: str, name: str, result: Any) -> Dict[str, Any]:
        content = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}

    def build_assistant_tool_call_message(self, tool_calls: List[Dict[str, Any]], content: Optional[str] = None) -> Dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))}}
                for tc in tool_calls
            ],
        }

    @staticmethod
    def transform_content_blocks(content_blocks: List[Dict[str, Any]]) -> Any:
        """Convert Chad's multimodal content blocks to OpenAI message parts."""
        text_parts, other_parts = [], []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    other_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"},
                    })
        if other_parts:
            parts = [{"type": "text", "text": "\n\n".join(text_parts)}] if text_parts else []
            return parts + other_parts
        return "\n\n".join(text_parts)

    @staticmethod
    def _clean_error_message(e: Exception) -> str:
        """Extract a concise, readable summary of the exception, especially API rate limits."""
        err_str = str(e)
        
        # 1. Detect common Rate Limit / Quota signatures
        is_rate_limit = any(term in err_str.lower() for term in ["429", "rate limit", "quota exceeded"])
        
        if is_rate_limit:
            import re
            # Try to extract the core message from embedded JSON bodies (e.g. Vertex AI/OpenAI payload)
            # Look for "message": "..." inside the raw text
            message_match = re.search(r'"message"\s*:\s*"([^"]+)"', err_str)
            if message_match:
                clean_msg = message_match.group(1).replace('\\n', ' ').strip()
                # Standardise long URL references for cleaner terminal logs
                clean_msg = re.sub(r'https?://\S+', '[URL]', clean_msg)
                return f"RATE LIMIT / QUOTA EXCEEDED (429): {clean_msg}"
                
            return "RATE LIMIT / QUOTA EXCEEDED (429): Cloud API has rejected the request due to active rate limits or exhausted quota tier."

        # 2. Clean up other long/bloated errors (strip out stack trace lines if excessive)
        if len(err_str) > 300:
            return err_str[:300] + "... [truncated]"
            
        return err_str
