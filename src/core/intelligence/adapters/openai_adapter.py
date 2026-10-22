"""
OpenAI Adapter - Implementation for local models and OpenAI-compatible APIs.
"""

import asyncio
import json
import logging
import re
import time
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from .ai_adapter_base import AIAdapter, GenerateResponse

from src.shared.logger import setup_logger
logger = setup_logger("OpenAIAdapter")
from src.shared.config_manager import ConfigManager
from src.core.intelligence.provider_capabilities import get_capabilities_for_url

class OpenAIAdapter(AIAdapter):
    """
    Adapter for any OpenAI-compatible local model API.
    """

    def __init__(self, base_url: str, api_key: str, model_name: str, system_instruction: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.system_instruction = system_instruction
        self._client = None

    @property
    def provider(self) -> str:
        """Human-readable provider label, derived from the shared resolved-provider
        identity (providers.resolve_provider_name) so this and the lifecycle
        coordinator never disagree about which backend a base_url points at."""
        from src.core.intelligence.providers import resolve_provider_name
        resolved = resolve_provider_name(self.base_url)
        if resolved == "lmstudio":
            return "LM Studio"
        if resolved == "ollama":
            return "Ollama"
        url_lower = self.base_url.lower()
        if "localhost" in url_lower or "127.0.0.1" in url_lower:
            return "Local API"
        return "External API"

    @property
    def capabilities(self):
        """Provider capability flags for this adapter's base_url (see provider_capabilities.py)."""
        return get_capabilities_for_url(self.base_url)

    @property
    def _is_qwen3(self) -> bool:
        """True for any Qwen 3.x family model (qwen3.5, qwen3.6, qwen3-235b, etc.)."""
        return "qwen3" in self.model_name.lower()

    def _get_model_settings(self) -> Dict[str, Any]:
        """
        Load per-model generation settings from config.yaml local_models.model_settings.
        Returns the entry for self.model_name, or {} if not configured.
        """
        try:
            local_cfg = ConfigManager.get("local_models") or {}
            per_model = local_cfg.get("model_settings") or {}
            return per_model.get(self.model_name, {})
        except Exception:
            return {}

    def _extract_markdown_tool_calls(self, content: str, known_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Dirty Parser: Force-extract tool calls from plain text when model fails JSON mode.
        Looks for patterns like tool_name(arg="val") or JSON blocks.

        This is a FALLBACK — it only runs when the native OpenAI tool_calls field is empty.
        That can happen if LM Studio fails to parse the model's raw output into the OpenAI
        tool_calls response format (e.g. during a backend hiccup or unsupported format edge case).

        Supported formats (tried in order):
          0a. Froggeric/Qwen fixed-template format — <tool_call><function=name><parameter=x>v</parameter></function></tool_call>
              Used when running Qwen3.x with the froggeric community chat template
              (config/chat_template_qwen36_35b.jinja). This template uses a custom XML-style
              parameter syntax instead of JSON, which the original pattern 0 cannot parse.
          0b. Standard Qwen JSON format — <tool_call>{"name": ..., "arguments": {...}}</tool_call>
          1.  Python-style calls — name(arg="val")
          2.  JSON call block — {"call": "name", "args": {...}}
          3.  Markdown JSON block — ```json {"name": ..., "arguments": {...}} ```
          4.  Chad <tool_calls> tag — <tool_calls>[...]</tool_calls>

        Args:
            content: The text to parse.
            known_tools: Optional list of valid tool names to prevent false positives.
        """
        calls = []

        # 0a. Froggeric fixed-template format (Qwen3.x on LM Studio with custom jinja).
        # Gated on has_native_tool_parser=False — only LM Studio needs this safety net;
        # other backends resolve native tool_calls reliably and skip straight to 0b/1-4.
        #
        #     The froggeric template (config/chat_template_qwen36_35b.jinja) instructs the model
        #     to output tool calls in this custom XML-like format instead of JSON:
        #
        #       <tool_call>
        #       <function=my_tool>
        #       <parameter=location>London</parameter>
        #       <parameter=units>metric</parameter>
        #       </function>
        #       </tool_call>
        #
        #     LM Studio normally parses this and converts it to OpenAI tool_calls in the API
        #     response, so the native path (delta.tool_calls) handles it transparently.
        #     This dirty-parser sub-pattern exists as a safety net for cases where LM Studio
        #     fails to do that conversion and the raw model output leaks through as plain text.
        #
        #     NOTE: The original pattern 0b (JSON inside <tool_call>) will NOT match this format
        #     because json.loads() throws on <function=...> syntax. We must check this FIRST.
        if not self.capabilities.has_native_tool_parser:
            froggeric_tc_pattern = r'<tool_call>\s*<function=([^>]+)>(.*?)</function>\s*</tool_call>'
            for match in re.finditer(froggeric_tc_pattern, content, re.DOTALL):
                func_name = match.group(1).strip()
                params_block = match.group(2)
                args = {}
                # Extract each <parameter=key>value</parameter> pair
                param_pattern = r'<parameter=([^>]+)>(.*?)</parameter>'
                for pm in re.finditer(param_pattern, params_block, re.DOTALL):
                    key = pm.group(1).strip()
                    value = pm.group(2).strip()
                    # Values may themselves be JSON (objects/arrays) — try to parse, fallback to str
                    try:
                        args[key] = json.loads(value)
                    except Exception:
                        args[key] = value
                if known_tools and func_name not in known_tools:
                    continue
                calls.append({
                    "id": f"froggeric_{int(time.time())}_{func_name}",
                    "name": func_name,
                    "args": args,
                })
            if calls:
                logger.debug(f"[ OPENAI ] Froggeric <function=...> parser extracted {len(calls)} call(s).")
                return calls

        # 0b. Standard Qwen/other JSON-inside-<tool_call> format.
        #     Qwen3 (official template) and many other models emit:
        #       <tool_call>\n{"name": "...", "arguments": {...}}\n</tool_call>
        xml_pattern = r'<tool_call>\s*(.*?)\s*</tool_call>'
        for match in re.finditer(xml_pattern, content, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                name = data.get("name")
                args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"_raw": args}
                if name:
                    if known_tools and name not in known_tools:
                        continue
                    calls.append({
                        "id": f"xml_{int(time.time())}_{name}",
                        "name": name,
                        "args": args,
                    })
            except Exception:
                continue
        if calls:
            logger.debug(f"[ OPENAI ] XML <tool_call> JSON parser extracted {len(calls)} call(s).")
            return calls

        # 1. Look for Python-style calls: name(arg="val", ...)
        # Matches alphanumeric name followed by (...)
        # We require it to be at the start of a line or preceded by specific markers to be 'non-destructive'
        pattern = r"(?:^|\n|Action:|[Tt]ool:)\s*(\w+)\(([^)]*)\)"
        matches = re.finditer(pattern, content)
        for match in matches:
            name = match.group(1)
            raw_args = match.group(2)
            
            # Validation: If we have a list of known tools, skip unknown ones
            if known_tools and name not in known_tools:
                continue
                
            # Very basic argument parser for key="val" or key=val
            args = {}
            arg_pattern = r"(\w+)\s*=\s*(?:[\"']([^\"']*)[\"']|(\w+))"
            for am in re.finditer(arg_pattern, raw_args):
                k = am.group(1)
                v = am.group(2) or am.group(3)
                args[k] = v
            
            calls.append({
                "id": f"dirty_{int(time.time())}_{name}",
                "name": name,
                "args": args
            })

        # 2. Look for JSON-like blocks: {"call": "name", "args": {...}}
        if not calls:
            json_pattern = r'\{[\s\n]*"call"[\s\n]*:[\s\n]*"([^"]+)"[\s\n]*,[\s\n]*"args"[\s\n]*:[\s\n]*(\{.*?\})[\s\n]*\}'
            for jm in re.finditer(json_pattern, content, re.DOTALL):
                name = jm.group(1)
                if known_tools and name not in known_tools:
                    continue
                try:
                    args = json.loads(jm.group(2))
                    calls.append({
                        "id": f"dirty_json_{int(time.time())}_{name}",
                        "name": name,
                        "args": args
                    })
                except:
                    continue

        # 3. Look for Markdown JSON blocks that look like OpenAI tool calls
        if not calls:
            md_json_pattern = r'```json[\s\n]*(\{.*?\})[\s\n]*```'
            for mj in re.finditer(md_json_pattern, content, re.DOTALL):
                try:
                    data = json.loads(mj.group(1))
                    name = data.get("name") or data.get("tool") or data.get("function")
                    args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
                    if name:
                        if known_tools and name not in known_tools:
                            continue
                        calls.append({
                            "id": f"dirty_md_{int(time.time())}_{name}",
                            "name": name,
                            "args": args
                        })
                except:
                    continue

        # 4. Look for <tool_calls> tags (Standard Chad format)
        if not calls:
            tc_pattern = r"<tool_calls>(.*?)</tool_calls>"
            for match in re.finditer(tc_pattern, content, re.DOTALL):
                inner = match.group(1).strip()
                try:
                    data = json.loads(inner)
                    if isinstance(data, list):
                        for tc in data:
                            # 4a. OpenAI-style nested function
                            if 'function' in tc and isinstance(tc['function'], dict):
                                name = tc['function'].get('name')
                                args_raw = tc['function'].get('arguments')
                                if isinstance(args_raw, str):
                                    try:
                                        args = json.loads(args_raw)
                                    except:
                                        args = {} # TODO: Robust arg parsing
                                else:
                                    args = args_raw or {}
                            # 4b. Flat format
                            else:
                                name = tc.get('name')
                                args = tc.get('args') or tc.get('arguments') or {}
                            
                            if name:
                                if known_tools and name not in known_tools:
                                    continue
                                calls.append({
                                    "id": tc.get('id') or f"call_{int(time.time())}_{name}",
                                    "name": name,
                                    "args": args
                                })
                except Exception as e:
                    logger.debug(f"[ OPENAI ] Failed to parse <tool_calls> block: {e}")

        # 5. Safety: Ensure all 'args' are dictionaries
        for tc in calls:
            args = tc.get("args")
            if isinstance(args, str):
                try:
                    tc["args"] = json.loads(args)
                except Exception:
                    # If it's not valid JSON, maybe it's just a raw value
                    tc["args"] = {"_raw": args}
            elif not isinstance(args, dict):
                tc["args"] = {"_raw": str(args)}

        return calls

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            from httpx import Timeout
            # Use a robust timeout for local models which can be slow to wake up
            self._client = AsyncOpenAI(
                base_url=self.base_url, 
                api_key=self.api_key,
                timeout=Timeout(60.0, connect=10.0, read=60.0)
            )
        return self._client

    async def close(self):
        """Release the underlying HTTP client and connection pool."""
        if self._client is not None:
            try:
                await self._client.close()
                logger.info(f"[ OPENAI ] Closed connection pool for {self.model_name}")
            except Exception as e:
                logger.debug(f"Error closing OpenAI client: {e}")
            finally:
                self._client = None

    async def is_ready(self) -> bool:
        """
        Check if the server is reachable AND the expected model is loaded.
        """
        try:
            models_response = await self.client.models.list()
            loaded_ids = [m.id for m in models_response.data]
            if self.model_name not in loaded_ids:
                logger.warning(
                    f"[ OPENAI ] Model mismatch: expected '{self.model_name}' "
                    f"but LM Studio has loaded: {loaded_ids}"
                )
                return False
            return True
        except Exception as e:
            logger.debug(f"[ OPENAI ] Readiness check failed for {self.model_name}: {e}")
            return False

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> GenerateResponse:
        """
        Send a request to the local model / OpenAI API.
        Supports multimodal content blocks.
        """
        logger.info(f"[ OPENAI ] Generating with model: {self.model_name}")

        # UI HINT: ModelLoading event is now handled by the orchestrator and frontend status
        is_local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        timeout = kwargs.get('timeout', 300.0)

        # Per-model settings from config.yaml local_models.model_settings
        _model_cfg = self._get_model_settings()
        
        # 1. Transform internal messages to OpenAI multimodal format if needed
        transformed_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            content_blocks = msg.get("content_blocks")
            image_base64 = msg.get("image_base64")
            
            if role == "user" and (content_blocks or image_base64):
                # New: Robust approach - Collapse multiple text blocks into one string 
                # This is more compatible with diverse local models (Qwen, Llama, etc.)
                all_text = []
                other_parts = []
                
                # Deduplication logic: Only add 'content' if it's not already in 'content_blocks'
                if content:
                    all_text.append(content)
                
                # Add blocks
                if content_blocks:
                    for block in content_blocks:
                        b_type = block.get("type")
                        if b_type == "text":
                            b_text = block.get("text", "")
                            # Only add if not already present (prevents 'open discord\n\nopen discord')
                            if b_text and b_text not in all_text:
                                all_text.append(b_text)
                        elif b_type == "image":
                            source = block.get("source", {})
                            if source.get("type") == "base64":
                                data = source.get("data", "")
                                mime = source.get("media_type", "image/png")
                                other_parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{data}"}
                                })
                        elif b_type == "document":
                            mime = block.get("source", {}).get("media_type", "unknown")
                            all_text.append(f"\n[Document Attached: {mime}]")
                
                # Legacy image support
                if image_base64:
                    other_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                    })

                # Decide if we send a string or a list
                # If we have images, we HAVE to send a list. 
                # Otherwise, send a clean string for max compatibility.
                final_content: Any = ""
                if other_parts:
                    final_content = []
                    if all_text:
                        final_content.append({"type": "text", "text": "\n\n".join(all_text)})
                    final_content.extend(other_parts)
                else:
                    final_content = "\n\n".join(all_text)

                transformed_messages.append({"role": role, "content": final_content})
                
                # DEBUG for Local Models (Qwen/OpenAI) 
                debug_msg = f"[ OPENAI ] Built message for '{role}' with {len(final_content) if isinstance(final_content, list) else 1} parts."
                logger.debug(debug_msg)
                
                if isinstance(final_content, list):
                    for i, part in enumerate(final_content):
                        p_type = part.get('type')
                        row = f"  Part {i}: {p_type.upper()}"
                        if p_type == 'text':
                            p_text = part.get('text', '')
                            row += f" ({len(p_text)} chars) -> {p_text[:100]}..."
                        logger.debug(row)
                else:
                    msg_row = f"[ OPENAI ] Final Text: {final_content[:150]}..."
                    logger.debug(msg_row)
            else:
                # Standard conversion or assistant/tool message
                
                # REFACTOR: Local Model Compatibility Layer
                # Many local providers (LM Studio, Ollama) have Jinja templates that don't support the 'tool' role.
                # We transform 'tool' messages into 'user' messages with a specific prefix to maintain the loop.
                is_openai_native = any(m in self.model_name.lower() for m in ["gpt-", "o1-", "o3-"])
                
                # Deep copy to prevent in-place modification of original history
                msg_copy = dict(msg)

                if role == "tool" and not is_openai_native:
                    tool_name = msg_copy.get("name", "unknown")
                    tool_content = msg_copy.get("content", "")

                    # Local models don't support the OpenAI 'tool' role natively, so we
                    # re-map tool results to 'user' messages. The format of that user message
                    # matters because the model was trained to recognise specific delimiters.
                    #
                    # For Qwen3 on LM Studio using the froggeric fixed chat template
                    # (config/chat_template_qwen36_35b.jinja), the template instructs the model
                    # to expect tool results wrapped in <tool_response> tags:
                    #
                    #   <tool_response>
                    #   {result JSON}
                    #   </tool_response>
                    #
                    # Sending the old [TOOL RESULT: name] prefix instead causes the model to
                    # not recognise the result boundary, which leads to re-calling the same tool,
                    # ignoring the result, or stalling mid-agentic-loop — the exact class of bugs
                    # the froggeric template was designed to fix.
                    #
                    # Other local models (Ollama, non-Qwen3) continue to use the generic prefix
                    # since they have no such expectation baked into their templates.
                    if self._is_qwen3 and self.capabilities.needs_tool_role_transform:
                        transformed_messages.append({
                            "role": "user",
                            "content": f"<tool_response>\n{tool_content}\n</tool_response>"
                        })
                        logger.debug(f"[ OPENAI ] Transformed 'tool' → 'user' with <tool_response> tags (Qwen3/LM Studio froggeric template): {tool_name}")
                    else:
                        transformed_messages.append({
                            "role": "user",
                            "content": f"[TOOL RESULT: {tool_name}]\n{tool_content}"
                        })
                        logger.debug(f"[ OPENAI ] Transformed 'tool' → 'user' with generic prefix for local model compatibility: {tool_name}")
                elif role == "assistant" and msg_copy.get("tool_calls") and not is_openai_native:
                    # Some local models also struggle with tool_calls in assistant messages
                    # We append a text representation to content to ensure visibility
                    content = msg_copy.get("content") or ""
                    tc_desc = "\n".join([f"Action: {tc['function']['name']}({tc['function']['arguments']})" for tc in msg_copy["tool_calls"]])
                    msg_copy["content"] = f"{content}\n{tc_desc}".strip()
                    # CRITICAL: Remove the tool_calls field to satisfy OpenAI-compatible validators
                    # that expect a 'tool' role message to follow any 'assistant' message with tool_calls.
                    # Since we map 'tool' -> 'user' above, we must hide the tool_calls here.
                    msg_copy.pop("tool_calls", None)
                    transformed_messages.append(msg_copy)
                else:
                    transformed_messages.append(msg_copy)

        try:
            req_kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "messages": transformed_messages,
                "temperature": temperature,
                "timeout": timeout,
            }
            # 3. Apply model parameters with defaults
            req_kwargs["max_tokens"] = kwargs.get('max_tokens', 8192)  # Default to 8192 to prevent cut-offs
            
            if 'presence_penalty' in kwargs:
                req_kwargs["presence_penalty"] = kwargs['presence_penalty']
            if 'frequency_penalty' in kwargs:
                req_kwargs["frequency_penalty"] = kwargs['frequency_penalty']

            # Per-model generation parameters from config.yaml local_models.model_settings.
            # Falls back to Qwen 3.x family defaults for any qwen3 model not explicitly listed.
            # top_k/repeat_penalty are non-standard params so they ride in extra_body.
            _top_p = _model_cfg.get("top_p") or (0.8 if self._is_qwen3 else None)
            _top_k = _model_cfg.get("top_k") or (20 if self._is_qwen3 else None)
            _repeat_penalty = _model_cfg.get("repeat_penalty") or (1.05 if self._is_qwen3 else None)
            if _top_p is not None:
                req_kwargs.setdefault("top_p", _top_p)
            if _top_k is not None or _repeat_penalty is not None:
                _gen_extra = req_kwargs.get("extra_body") or {}
                if _top_k is not None:
                    _gen_extra.setdefault("top_k", _top_k)
                if _repeat_penalty is not None:
                    _gen_extra.setdefault("repeat_penalty", _repeat_penalty)
                req_kwargs["extra_body"] = _gen_extra
            if _top_p or _top_k or _repeat_penalty:
                logger.debug(f"[ OPENAI ] Model params: top_p={_top_p}, top_k={_top_k}, repeat_penalty={_repeat_penalty}")

            # 2. Convert and attach tools
            final_tools = []
            if tools:
                final_tools = self.convert_tool_schemas(tools)
                req_kwargs["tools"] = final_tools
                logger.info(f"[ OPENAI ] Outbound Tools: {len(final_tools)} schemas attached ({', '.join([t['function']['name'] for t in final_tools])})")
            else:
                logger.debug("[ OPENAI ] No tools provided for this turn.")
            
            # --- DEBUG: PROMPT DUMP ---
            try:
                dump_dir = Path("docs/logs")
                dump_dir.mkdir(parents=True, exist_ok=True)
                with open(dump_dir / "prompt_dump.txt", "w", encoding="utf-8") as f:
                    import json
                    f.write("=== [ OPENAI PROMPT DUMP ] ===\n")
                    f.write(f"MODEL: {self.model_name}\n")
                    f.write(f"TEMPERATURE: {temperature}\n")
                    f.write("--- MESSAGES ---\n")
                    f.write(json.dumps(transformed_messages, indent=2))
                    f.write("\n--- TOOLS ---\n")
                    f.write(json.dumps(final_tools, indent=2))
                    f.write("\n=== [ END DUMP ] ===\n")
                logger.info(f"[ OPENAI ] Outbound prompt dumped to {dump_dir / 'prompt_dump.txt'}")
            except Exception as e:
                logger.debug(f"[ OPENAI ] Failed to dump prompt: {e}")
            # --------------------------
            
            # Determine effective thinking level. Priority order:
            #   1. User toggle (thinking_enabled kwarg) — explicit per-turn override
            #   2. Per-model config (thinking: true/false) — model-specific policy
            #   3. Qwen3 family safe default (off for unlisted models)
            #   4. Raw thinking_level from caller
            _raw_thinking_level = kwargs.get("thinking_level")
            _thinking_cfg = _model_cfg.get("thinking")
            _user_thinking = kwargs.get("thinking_enabled")  # set by UI toggle via ContextVar

            if _user_thinking is False:
                # User explicitly toggled thinking off for this turn
                thinking_level = None
            elif _user_thinking is True:
                # User explicitly toggled thinking on — use config level or default
                thinking_level = _raw_thinking_level or _model_cfg.get("thinking_level", "medium")
            elif _thinking_cfg is False:
                # Model config: thinking not supported (e.g. qwen3.5-9b)
                thinking_level = None
            elif _thinking_cfg is True and not _raw_thinking_level:
                # Model config: thinking on, no caller-level override
                thinking_level = _model_cfg.get("thinking_level", "medium")
            elif _thinking_cfg is None and self._is_qwen3:
                # Unlisted qwen3 model — safe default is off
                thinking_level = None
            else:
                thinking_level = _raw_thinking_level

            # CRITICAL: Disable thinking whenever tools are attached for Qwen3 on LM Studio.
            # Even models configured with thinking=True (e.g. qwen3.6-35b) will trap tool call
            # JSON inside <think> blocks when thinking is active, producing blank/broken responses.
            # The model should reason BEFORE choosing a tool, not during tool dispatch.
            # This overrides user toggle and model config — tool-calling always wins.
            if thinking_level is not None and tools and self._is_qwen3 and self.capabilities.needs_qwen3_reasoning_workaround:
                logger.debug(
                    f"[ OPENAI ] Thinking disabled for tool-calling turn "
                    f"({len(tools)} tool(s) attached) to prevent <think>-trapped tool calls."
                )
                thinking_level = None

            if thinking_level:
                # Map minimal -> low, medium -> medium, high -> high
                level_map = {"minimal": "low", "medium": "medium", "high": "high"}
                reasoning_effort = level_map.get(thinking_level)
                
                # Pass reasoning_effort for OpenAI native reasoning models AND LM Studio Qwen 3.x.
                # LM Studio has supported reasoning_effort since v0.4.8 for Qwen/local reasoning models.
                _is_native_openai_reasoning = any(m in self.model_name.lower() for m in ["o1", "o3", "o4"])
                _needs_qwen3_workaround = self._is_qwen3 and self.capabilities.needs_qwen3_reasoning_workaround
                if reasoning_effort and (_is_native_openai_reasoning or _needs_qwen3_workaround):
                    # Model config can pin reasoning_effort (e.g. qwen3.6: medium to cap loops)
                    _cfg_effort = _model_cfg.get("reasoning_effort")
                    req_kwargs["reasoning_effort"] = _cfg_effort or reasoning_effort
                    logger.debug(f"[ OPENAI ] reasoning_effort={req_kwargs['reasoning_effort']} (thinking_level={thinking_level})")
                
                # Tracking state for throttling and progress
                last_thinking_trigger_time = 0
                last_content_trigger_time = 0
                
                # Bug 1: Scale thinking budget based on level
                # minimal -> 8k, medium -> 32k, high -> 256k
                budget_map = {"minimal": 8192, "medium": 32768, "high": 262144}
                budget = budget_map.get(thinking_level, 32768)
                
                # Only include specialized thinking parameters for supported models
                # Local models (LM Studio/Ollama) often error out on unknown top-level fields.
                if any(m in self.model_name.lower() for m in ["deepseek-r1", "thinking-model"]):
                    req_kwargs["extra_body"] = {
                        "enable_thinking": True,
                        "thinking_budget": budget,
                        "include_thoughts": True
                    }
                elif _needs_qwen3_workaround:
                    # Qwen3.5 dropped the /no_think soft switch; use chat_template_kwargs instead.
                    # "minimal" thinking level maps to disabled; anything higher keeps it on.
                    _enable = thinking_level != "minimal"
                    _qwen_thinking_extra = req_kwargs.get("extra_body") or {}
                    _qwen_thinking_extra["chat_template_kwargs"] = {"enable_thinking": _enable}
                    # Hard token cap: prevents infinite reasoning loops on Qwen3.6 35B.
                    # LM Studio v0.4.8+ honours thinking_budget in extra_body.
                    # Cap from config (e.g. reasoning_effort already set above), fallback to
                    # budget_map so the model can never exceed our chosen level's token limit.
                    _cfg_budget = _model_cfg.get("thinking_budget")
                    _qwen_thinking_extra["thinking_budget"] = _cfg_budget or budget
                    req_kwargs["extra_body"] = _qwen_thinking_extra
                    logger.debug(
                        f"[ OPENAI ] Qwen 3.x thinking: enable={_enable}, "
                        f"budget={_qwen_thinking_extra['thinking_budget']} tokens (level={thinking_level})"
                    )
                
                # Token tracking (approximated by character count)
                total_chars = 0
                last_checkpoint = 0
                CHECKPOINT_STEP = 32768
                
                # Start the stream
                req_kwargs["stream"] = True
                logger.info(f"[ OPENAI ] Calling model '{self.model_name}' at {self.base_url}...")
                
                try:
                    stream = await self.client.chat.completions.create(**req_kwargs)
                except Exception as e:
                    logger.error(f"[ OPENAI ] Connection failed to {self.base_url}: {e}")
                    raise
                
                full_content = ""
                full_reasoning = ""
                tool_calls_buffer = {}
                finish_reason = "stop"
                last_response_obj = None

                async for chunk in stream:
                    last_response_obj = chunk
                    if not chunk.choices:
                        continue
                    
                    choice = chunk.choices[0]
                    delta = choice.delta
                    
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    # 1. Handle Reasoning
                    reasoning_chunk = getattr(delta, "reasoning_content", None)
                    if not reasoning_chunk and hasattr(delta, "model_dump"):
                        reasoning_chunk = delta.model_dump().get("reasoning_content")
                    
                    if reasoning_chunk:
                        full_reasoning += reasoning_chunk
                        total_chars += len(reasoning_chunk)
                        if on_thinking:
                            await self._trigger_callback(on_thinking, reasoning_chunk, "reasoning", True)
                            last_thinking_trigger_time = time.time()

                    # 2. Handle Content
                    if delta.content:
                        full_content += delta.content
                        total_chars += len(delta.content)
                        if on_token:
                            await self._trigger_callback(on_token, delta.content, True)
                            last_content_trigger_time = time.time()

                    # 3. Handle Tool Calls
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {"id": "", "name": "", "arguments": ""}
                            
                            if tc_delta.id:
                                tool_calls_buffer[idx]["id"] += tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_buffer[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_buffer[idx]["arguments"] += tc_delta.function.arguments

                    # 4. Token Checkpoints (Every 32k tokens approximated)
                    est_tokens = total_chars // 4
                    current_checkpoint = (est_tokens // CHECKPOINT_STEP) * CHECKPOINT_STEP
                    if current_checkpoint > last_checkpoint and current_checkpoint <= 262144:
                        logger.info(f"[ OPENAI ] Token Checkpoint: ~{current_checkpoint // 1024}k tokens consumed...")
                        last_checkpoint = current_checkpoint

                # Ensure the final reasoning state is pushed (Final cumulative flush)
                if full_reasoning and on_thinking:
                    await self._trigger_callback(on_thinking, full_reasoning, "reasoning", False)
                
                # Ensure the final content state is pushed (Final cumulative flush)
                if full_content and on_token:
                    await self._trigger_callback(on_token, full_content, False)

                parsed_tool_calls = None
                if tool_calls_buffer:
                    parsed_tool_calls = []
                    for idx in sorted(tool_calls_buffer.keys()):
                        tc = tool_calls_buffer[idx]
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        
                        parsed_tool_calls.append({
                            "id": tc["id"] or f"call_{idx}",
                            "name": tc["name"],
                            "args": args
                        })
                
                # Extract known tool names for Dirty Parser validation
                known_tool_names = [t.get("name") if isinstance(t, dict) else getattr(t, "name", None) for t in (tools or [])]
                known_tool_names = [n for n in known_tool_names if n]

                # Strip thinking tokens that leaked into content (LM Studio / Qwen)
                # Some providers don't use reasoning_content and instead wrap CoT in <think> tags
                if full_content:
                    # 1. Look for unclosed or fully closed think blocks
                    # Check for <think> without </think> or with it
                    if "<think>" in full_content:
                        # Extract everything inside <think> if found
                        think_parts = full_content.split("<think>")
                        if len(think_parts) > 1:
                            # Everything after the first <think>
                            after_think = think_parts[1]
                            if "</think>" in after_think:
                                inner_parts = after_think.split("</think>")
                                leaked_reasoning = inner_parts[0]
                                remaining_content = think_parts[0] + (inner_parts[1] if len(inner_parts) > 1 else "")
                            else:
                                leaked_reasoning = after_think
                                remaining_content = think_parts[0]
                            
                            if leaked_reasoning and not full_reasoning:
                                full_reasoning = leaked_reasoning.strip()
                            
                            full_content = remaining_content.strip()
                    
                    # 2. Final sanitization (remove any remaining tags)
                    full_content = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()
                    full_content = re.sub(r'<think>.*$', '', full_content, flags=re.DOTALL).strip()

                # FALLBACK: If native tool calls are empty, try the Dirty Parser
                # We move this AFTER stripping thinking tags to prevent 'destructive' extraction
                # of tool calls that were only part of the model's internal reasoning.
                if not parsed_tool_calls and full_content:
                    parsed_tool_calls = self._extract_markdown_tool_calls(full_content, known_tools=known_tool_names)
                    if parsed_tool_calls:
                        logger.info(f"[ OPENAI ] Dirty Parser extracted {len(parsed_tool_calls)} fallback tool calls.")
                        # Clear the XML from content — it was tool call syntax, not prose.
                        # Leaving it in full_content causes it to surface as the "final response"
                        # when tool dispatch fails or when the response extractor looks at msg.content.
                        full_content = ""

                # Guard: If content is empty after stripping but we have reasoning, treat as a special case
                if not full_content and full_reasoning and not tool_calls_buffer and not parsed_tool_calls:
                    # REFACTOR: If a reasoning model (like Qwen-Reasoning or DeepSeek-R1) 
                    # ONLY returns thoughts, we surface the thoughts as the main response 
                    # so the user actually sees something instead of a hang.
                    full_content = f"> Thoughts:\n{full_reasoning}"
                    finish_reason = "thinking_only"

                # Log summary for debugging silent failures
                logger.info(
                    f"[ OPENAI ] Turn Complete: finish_reason={finish_reason}, "
                    f"text_len={len(full_content) if full_content else 0}, "
                    f"tools={len(parsed_tool_calls) if parsed_tool_calls else 0}, "
                    f"reasoning_len={len(full_reasoning) if full_reasoning else 0}"
                )
                if parsed_tool_calls:
                    logger.info(f"[ OPENAI ] Tool Calls: {', '.join([tc['name'] for tc in parsed_tool_calls])}")

                return GenerateResponse(
                    text=full_content if full_content else None,
                    tool_calls=parsed_tool_calls,
                    finish_reason=finish_reason,
                    reasoning=full_reasoning if full_reasoning else None,
                    raw=last_response_obj,
                )

            # Bug 2: NON-STREAMING PATH
            # Note: This path is only reached if thinking_level is NOT set.
            # If thinking is enabled, we MUST use the streaming path above to
            # capture reasoning_content/thoughts as they arrive.

            # For Qwen 3.x on LM Studio with no explicit thinking level: disable thinking.
            # Qwen3.5 9B: tool calls get trapped inside <think> blocks → blank output.
            # Qwen3.6 35B: enters infinite reasoning loop without a budget cap.
            # Belt-and-suspenders: reasoning_effort=none (LM Studio native, v0.4.8+) +
            # chat_template_kwargs.enable_thinking=false (Jinja template hard-switch).
            if self._is_qwen3 and self.capabilities.needs_qwen3_reasoning_workaround:
                req_kwargs["reasoning_effort"] = "none"
                _no_think_extra = req_kwargs.get("extra_body") or {}
                _no_think_extra["chat_template_kwargs"] = {"enable_thinking": False}
                req_kwargs["extra_body"] = _no_think_extra
                logger.debug(f"[ OPENAI ] Qwen 3.x thinking disabled for non-thinking turn (reasoning_effort=none)")

            response = await self.client.chat.completions.create(**req_kwargs)

            if not response.choices:
                return GenerateResponse(text=None, tool_calls=None, finish_reason="error", raw=response)

            choice = response.choices[0]
            message = choice.message
            finish_reason = choice.finish_reason or "stop"

            parsed_tool_calls = None
            if message.tool_calls:
                parsed_tool_calls = []
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    parsed_tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": args,
                    })

            # FALLBACK: Dirty Parser for the non-streaming path.
            # When thinking_level=None (all Qwen3 tool turns), the non-streaming path is taken.
            # LM Studio sometimes fails to convert froggeric XML (<tool_call><function=name>...)
            # to native tool_calls in the API response — leaving the raw XML in message.content.
            # This is exactly the same problem as the streaming path, so we apply the same fix.
            text_content = message.content or ""
            if not parsed_tool_calls and text_content:
                known_tool_names = [
                    t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
                    for t in (tools or [])
                ]
                known_tool_names = [n for n in known_tool_names if n]
                logger.info(
                    f"[ OPENAI ] Non-streaming Dirty Parser: "
                    f"content_len={len(text_content)}, known_tools={known_tool_names}"
                )
                parsed_tool_calls = self._extract_markdown_tool_calls(text_content, known_tools=known_tool_names)
                if parsed_tool_calls:
                    logger.info(
                        f"[ OPENAI ] Non-streaming Dirty Parser extracted "
                        f"{len(parsed_tool_calls)} fallback tool call(s): "
                        f"{[tc['name'] for tc in parsed_tool_calls]}"
                    )
                    text_content = ""  # Clear XML content — it was a tool call, not prose

            # Extract reasoning
            reasoning = getattr(message, 'reasoning_content', None)
            if not reasoning and hasattr(response, 'model_dump'):
                try:
                    reasoning = response.model_dump()['choices'][0]['message'].get('reasoning_content')
                except Exception:
                    pass

            if finish_reason == "length":
                # CRITICAL: This is a common cause of premature turn endings in local models
                logger.error(f"[ OPENAI ] FATAL: Model '{self.model_name}' reached token limit (max_tokens={req_kwargs.get('max_tokens')}). Output was likely truncated.")

            logger.info(
                f"[ OPENAI ] Non-streaming Turn Complete: finish_reason={finish_reason}, "
                f"text_len={len(text_content)}, tools={len(parsed_tool_calls) if parsed_tool_calls else 0}"
            )

            return GenerateResponse(
                text=text_content if text_content else None,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                reasoning=reasoning,
                raw=response,
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            err_msg = str(e)
            
            # Special handling for LM Studio / Ollama common errors
            if "Model unloaded" in err_msg or "400" in err_msg:
                logger.error(f"[ OPENAI ] Model '{self.model_name}' is unloaded or unavailable: {e}")
                # We raise here or return a specific finish reason that the caller can identify as "transient"
                return GenerateResponse(text=None, tool_calls=None, finish_reason="unloaded", raw=err_msg)

            logger.error(f"OpenAI generation failed: {e}")
            return GenerateResponse(text=None, tool_calls=None, finish_reason="error", raw=err_msg)

    @staticmethod
    def convert_tool_schemas(schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert to OpenAI tools format.
        """
        openai_tools = []
        for schema in schemas:
            if isinstance(schema, dict) and "function" in schema and isinstance(schema["function"], dict):
                openai_tools.append(schema)
                continue
            name = schema.get("name") if isinstance(schema, dict) else None
            if not name:
                logger.warning(f"[ OPENAI ] Skipping tool schema without a name: {schema}")
                continue
                
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description", ""),
                    "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return openai_tools

    @staticmethod
    def build_tool_result_message(
        tool_call_id: str,
        name: str,
        result: Any
    ) -> Dict[str, Any]:
        """
        Build a tool result message.
        """
        def scrub_result(val: Any) -> Any:
            """Recursively scrub large binary/base64 data from tool results."""
            if isinstance(val, dict):
                return {
                    k: f"<image_data:{len(str(v))}chars>" 
                       if any(x in k.lower() for x in ["base64", "image", "screenshot", "data"])
                       and len(str(v)) > 500
                       else scrub_result(v)
                    for k, v in val.items()
                }
            elif isinstance(val, list):
                return [scrub_result(item) for item in val]
            return val

        try:
            # Scrub binary blobs before text serialization to prevent model hallucination
            scrubbed = scrub_result(result)
            content = json.dumps(scrubbed) if isinstance(scrubbed, (dict, list)) else str(scrubbed)
        except Exception:
            content = str(result)

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }

    @staticmethod
    def build_assistant_tool_call_message(
        tool_calls: List[Dict[str, Any]],
        content: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build an assistant message containing tool calls.
        """
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {})),
                    },
                }
                for tc in tool_calls
            ],
        }

    async def discover_models(self, timeout: float = 2.0) -> List[str]:
        """
        Discover available models from the local API server.

        Uses a short connect/read timeout so offline providers fail fast
        instead of blocking the UI discovery flow.
        """
        try:
            from httpx import Timeout
            response = await self.client.models.list(
                timeout=Timeout(timeout, connect=2.0, read=timeout)
            )
            return [m.id for m in response.data]
        except Exception as e:
            logger.debug(f"discover_models failed (server may not be running): {e}")
            return []

    async def test_tool_calling(self) -> bool:
        """
        Test whether the current model supports tool calling.
        """
        test_tools = self.convert_tool_schemas([{
            "name": "get_weather",
            "description": "Get current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"],
            },
        }])

        test_messages = [
            {
                "role": "system",
                "content": "SYSTEM_CHECK: Respond ONLY with a tool call.",
            },
            {
                "role": "user",
                "content": "Call the get_weather tool for 'London'.",
            }
        ]

        try:
            logger.info(f"Capability test for '{self.model_name}'...")
            result = await self.generate(
                messages=test_messages,
                tools=test_tools,
                temperature=0.0,
                presence_penalty=1.5,
                timeout=30.0, # Increased from 15s to accommodate larger/busy models
            )
            
            if result.finish_reason in ("unloaded", "error"):
                logger.warning(f"Tool calling test for '{self.model_name}' deferred: {result.finish_reason}")
                return None # Indeterminate state
                
            supports = result.finish_reason == "tool_calls" or bool(result.tool_calls)
            logger.info(f"Tool calling test for '{self.model_name}': {'PASS' if supports else 'FAIL'}")
            return supports
        except Exception as e:
            logger.warning(f"Tool calling test for '{self.model_name}' failed with exception: {e}")
            return None # Indeterminate state

    async def _trigger_callback(self, callback, *args):
        if not callback:
            return
        try:
            # First call the function
            res = callback(*args)
            # If it returned a coroutine or something awaitable, await it
            if res is not None and (asyncio.iscoroutine(res) or inspect.isawaitable(res)):
                await res
        except Exception as e:
            logger.debug(f"Callback failed: {e}")

    @staticmethod
    def transform_content_blocks(content_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Static helper to convert content blocks to OpenAI-style message parts.
        """
        parts = []
        for block in content_blocks:
            b_type = block.get("type")
            if b_type == "text":
                parts.append({"type": "text", "text": block.get("text", "")})
            elif b_type == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    data = source.get("data", "")
                    mime = source.get("media_type", "image/png")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"}
                    })
            elif b_type == "document":
                # For non-multimodal capable local models, we inject a placeholder
                # The text extraction is usually done at the model level via all_text collapse
                # But here we provide a hint.
                mime = block.get("source", {}).get("media_type", "unknown")
                parts.append({"type": "text", "text": f"\n[Document Attached: {mime}]"})
        return parts
