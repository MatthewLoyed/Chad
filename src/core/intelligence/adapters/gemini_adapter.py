"""
Gemini Adapter - Implementation for Google GenAI SDK.
"""

import logging
from typing import Any, Dict, List, Optional, Callable
from google.genai import types
from google.genai import types
from .ai_adapter_base import AIAdapter, GenerateResponse, TokenUsage

logger = logging.getLogger("GeminiAdapter")

class GeminiAdapter(AIAdapter):
    """
    Adapter for Google Gemini models.
    """

    def __init__(self, client: Any, model_name: str, system_instruction: Optional[str] = None):
        self.client = client
        self.model_name = model_name
        self.system_instruction = system_instruction

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> GenerateResponse:
        """
        Send a request to Gemini.
        """
        logger.info(f"[ GEMINI ] Generating with model: {self.model_name}")
        
        # 1. Extract dynamic system instructions from messages if present
        dynamic_sys = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:
                    dynamic_sys.append(content)
        
        final_sys_instr = self.system_instruction or ""
        if dynamic_sys:
            joined_sys = "\n\n".join(filter(None, dynamic_sys))
            if final_sys_instr:
                # Ensure we don't double-inject if it's already present
                if final_sys_instr not in joined_sys:
                    final_sys_instr = f"{final_sys_instr}\n\n{joined_sys}"
                else:
                    final_sys_instr = joined_sys
            else:
                final_sys_instr = joined_sys
        
        # 2. Convert internal messages to Gemini Content objects (skips 'system' roles natively)
        contents = self._convert_to_gemini_contents(messages)

        # 3. Build config
        final_tools = self.convert_tool_schemas(tools) if tools else None
        if not final_tools:
            final_tools = None

        config = types.GenerateContentConfig(
            system_instruction=final_sys_instr if final_sys_instr else None,
            temperature=temperature,
            tools=final_tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
 
        config.max_output_tokens = kwargs.get('max_tokens', 8192)

        # Handle ThinkingConfig if provided in kwargs
        if 'thinking_config' in kwargs:
            config.thinking_config = kwargs['thinking_config']

        # 3. Call Gemini (using executor since SDK is sync)
        import asyncio
        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config
                )
            )
        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            return GenerateResponse(text=None, tool_calls=None, finish_reason="error", raw=str(e))

        # 4. Parse response
        text = ""
        tool_calls = []
        reasoning = ""
        
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.thought:
                        thought_text = part.text or ""
                        reasoning += thought_text
                        if on_thinking:
                             await self._trigger_callback(on_thinking, thought_text)
                    elif part.text:
                        text += part.text
                    
                    # Handle thinking parts from Gemini 2.5/3.x
                    # Note: SDK might change this, but usually it's a specific Part type
                    
            # Extract function calls
            if response.function_calls:
                for fc in response.function_calls:
                    tool_calls.append({
                        "id": f"call_{fc.name}", # Gemini doesn't have IDs, we map by name
                        "name": fc.name,
                        "args": dict(fc.args) if fc.args else {}
                    })

        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"
        
        # 5. Handle Token Usage Metadata
        normalized_usage = None
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            meta = response.usage_metadata
            
            # Map common Gemini model capacities (Fallback context window mapping)
            context_window = None
            model_low = self.model_name.lower()
            if "flash" in model_low:
                context_window = 1048576  # 1M
            elif "pro" in model_low:
                context_window = 2097152  # 2M
            
            normalized_usage = TokenUsage(
                prompt_tokens=getattr(meta, 'prompt_token_count', 0),
                completion_tokens=getattr(meta, 'candidates_token_count', 0),
                total_tokens=getattr(meta, 'total_token_count', 0),
                reasoning_tokens=None, # Gemini's Thinking isn't strictly split yet in metadata
                context_window=context_window
            )
        
        return GenerateResponse(
            text=text if text else None,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=finish_reason,
            reasoning=reasoning if reasoning else None,
            raw=response,
            usage=normalized_usage
        )

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        """
        Convert to Gemini Tool declarations.
        """
        function_declarations = []
        for schema in schemas:
            func_decl = types.FunctionDeclaration(
                name=schema.get('name', ''),
                description=schema.get('description', ''),
                parameters_json_schema=schema.get('parameters', {})
            )
            function_declarations.append(func_decl)
        
        if function_declarations:
            return [types.Tool(function_declarations=function_declarations)]
        return []

    def build_tool_result_message(
        self,
        tool_call_id: str,
        name: str,
        result: Any
    ) -> Dict[str, Any]:
        """
        Build internal representation of a tool result.
        """
        return {
            "role": "tool",
            "name": name,
            "tool_call_id": tool_call_id,
            "content": result
        }

    def build_assistant_tool_call_message(
        self,
        tool_calls: List[Dict[str, Any]],
        content: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build internal representation of assistant tool calls.
        """
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls
        }

    def _convert_to_gemini_contents(self, messages: List[Dict[str, Any]]) -> List[types.Content]:
        """
        Convert internal list of dicts to Gemini types.Content list.
        Supports multimodal content blocks.
        Fixes:
         - Grouping consecutive user/tool roles to avoid consecutive role errors.
         - Robust tool call extraction for nested OpenAI/LangChain format.
         - Wrapping non-dictionary tool outputs so Pydantic validation passes.
        """
        import base64
        try:
            gemini_contents = []
            
            for msg in messages:
                role = msg.get('role')
                if role == 'system':
                    # Gemini takes system_instruction in GenerateContentConfig, not history
                    continue
                
                # Map role: 'user' & 'tool' both map to 'user', 'assistant' maps to 'model'
                msg_g_role = 'user' if role in ('user', 'tool') else 'model'
                
                content = msg.get('content')
                tool_calls = msg.get('tool_calls')
                content_blocks = msg.get('content_blocks')
                image_base64 = msg.get('image_base64')
                
                parts = []
                
                if role == 'user':
                    # Priority 1: Multi-block content (new system)
                    if content_blocks:
                        for block in content_blocks:
                            b_type = block.get('type')
                            if b_type == 'text':
                                b_text = block.get('text', "")
                                logger.debug(f"[ GEMINI ] Found text block in content_blocks: {len(b_text)} chars")
                                parts.append(types.Part(text=b_text))
                            elif b_type in ['image', 'document']:
                                source = block.get('source', {})
                                if source.get('type') == 'base64':
                                    data_b64 = source.get('data', '')
                                    mime = source.get('media_type', 'application/octet-stream')
                                    try:
                                        raw_data = base64.b64decode(data_b64)
                                        logger.debug(f"[ GEMINI ] Found {b_type} block: {len(raw_data)} bytes, MIME={mime}")
                                        parts.append(types.Part(
                                            inline_data=types.Blob(
                                                data=raw_data,
                                                mime_type=mime
                                            )
                                        ))
                                    except Exception as e:
                                        logger.error(f"Failed to decode base64 for {b_type}: {e}")
                    
                    # Priority 2: Legacy Image Base64
                    elif image_base64:
                        try:
                            raw_data = base64.b64decode(image_base64)
                            parts.append(types.Part(
                                inline_data=types.Blob(
                                    data=raw_data,
                                    mime_type="image/png" # Default for legacy
                                )
                            ))
                        except Exception as e:
                            logger.error(f"Failed to decode legacy image_base64: {e}")
                    
                    # Priority 3: Standard Text
                    if content and not any(p.text == content for p in parts):
                        logger.debug(f"[ GEMINI ] Adding standard content text: {len(content)} chars")
                        parts.append(types.Part(text=content))

                elif role == 'assistant':
                    if content:
                        parts.append(types.Part(text=content))
                    if tool_calls:
                        for tc in tool_calls:
                            # Robust parsing for flat internal format OR nested OpenAI format from ChatChad
                            name = tc.get('name')
                            args = tc.get('args', {})
                            if not name and 'function' in tc:
                                name = tc['function'].get('name')
                                args_raw = tc['function'].get('arguments', {})
                                if isinstance(args_raw, str):
                                    try:
                                        args = json.loads(args_raw)
                                    except:
                                        args = {}
                                else:
                                    args = args_raw
                            
                            if name:
                                parts.append(types.Part.from_function_call(
                                    name=name,
                                    args=args
                                ))
                                
                elif role == 'tool':
                    # Gemini expects FunctionResponse for tool results.
                    # MUST be a dict; wrap strings/numbers/lists to prevent ValidationError.
                    raw_content = msg.get('content', {})
                    final_response = {}
                    if isinstance(raw_content, dict):
                        final_response = raw_content
                    else:
                        final_response = {"result": raw_content}
                        
                    parts.append(types.Part.from_function_response(
                        name=msg.get('name', 'unknown'),
                        response=final_response
                    ))
                
                if not parts:
                    continue
                    
                # CRITICAL MERGE LOOP: Merge consecutive messages mapping to the same role
                if gemini_contents and gemini_contents[-1].role == msg_g_role:
                    gemini_contents[-1].parts.extend(parts)
                    logger.debug(f"[ ADAPTER ] Merged {len(parts)} parts into existing role '{msg_g_role}'.")
                else:
                    gemini_contents.append(types.Content(role=msg_g_role, parts=parts))
                    logger.debug(f"[ ADAPTER ] Created new Content object for role '{msg_g_role}' with {len(parts)} parts.")
            
            return gemini_contents
        except Exception as e:
            logger.error(f"[ ADAPTER ] Failed to convert internal history to Gemini contents: {e}", exc_info=True)
            return []

    async def is_ready(self) -> bool:
        """
        Check if the Gemini client is reachable and can communicate.
        """
        import asyncio
        import logging
        try:
            # Short timeout to avoid blocking startup
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: list(self.client.models.list())
                ),
                timeout=5.0
            )
            return True
        except Exception as e:
            logger.debug(f"[ GEMINI ] Readiness check failed: {e}")
            return False

    async def _trigger_callback(self, callback, data):
        import asyncio
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.debug(f"Callback failed: {e}")

    @staticmethod
    def transform_content_blocks(content_blocks: List[Dict[str, Any]], text_only: bool = False) -> List[Any]:
        """
        Static helper to convert content blocks to Gemini parts.
        Useful for building initial history in the orchestrator.
        """
        import base64
        from google.genai import types
        parts = []
        for block in content_blocks:
            b_type = block.get('type')
            if b_type == 'text':
                parts.append(types.Part(text=block.get('text', "")))
            elif b_type in ['image', 'document'] and not text_only:
                source = block.get('source', {})
                if source.get('type') == 'base64':
                    try:
                        raw_data = base64.b64decode(source.get('data', ''))
                        parts.append(types.Part(
                            inline_data=types.Blob(
                                data=raw_data,
                                mime_type=source.get('media_type', 'application/octet-stream')
                            )
                        ))
                    except Exception as e:
                        logger.error(f"Failed to decode base64 for {b_type}: {e}")
            elif b_type == 'document' and text_only:
                # If we only want text, we don't add the blob
                pass
        return parts
