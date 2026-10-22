import json
import asyncio
from typing import Any, Dict, List, Optional, Union, Callable, Sequence, Type
from src.shared.logger import setup_logger

logger = setup_logger("ChatChad")
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from pydantic import Field

from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse

class ChatChad(BaseChatModel):
    """
    LangChain wrapper for Chad's AIAdapter.
    Preserves custom logic for local models, tool parsing, and multimodal content.
    """
    adapter: Any = Field(...)
    temperature: float = 0.7
    thinking_level: str = "medium"
    thinking_enabled: Optional[bool] = None
    bound_tools: List[Any] = Field(default_factory=list)
    tool_choice: Optional[Union[str, bool, Dict[str, Any]]] = None

    def bind_tools(self, tools: Sequence[Union[Dict[str, Any], Type, Callable, BaseTool]], *, tool_choice: Optional[Union[str, bool, Dict[str, Any]]] = None, **kwargs: Any) -> "ChatChad":
        """
        Bind tools to the model. LangGraph-supervisor uses this to register agents.
        """
        from src.shared.logger import setup_logger
        logger = setup_logger("ChatChad")
        logger.debug(f"[ BIND ] Binding {len(tools)} tools to ChatChad")
        return self.copy(update={"bound_tools": tools, "tool_choice": tool_choice, **kwargs})

    def _llm_type(self) -> str:
        return "chad_adapter"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("ChatChad only supports async generation via _agenerate")

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 1. Convert LangChain messages to Chad format
        chad_messages = self._to_chad_messages(messages)
        
        # 2. Extract tools from kwargs or bound_tools
        tools = kwargs.get("tools") or self.bound_tools
        if tools:
            from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool

            chad_tool_schemas = []
            for t in tools:
                if hasattr(t, "name") and hasattr(t, "description"):
                    chad_tool_schemas.append({
                        "name": t.name,
                        "description": t.description,
                        "parameters": parameters_for_llm_from_tool(t),
                    })
                elif isinstance(t, dict):
                    chad_tool_schemas.append(t)
            tools = chad_tool_schemas

        # 3. Call the adapter
        from langchain_core.callbacks.manager import adispatch_custom_event
        from src.core.orchestration.context import (
            agent_name_var,
            parent_correlation_id_var,
            thinking_enabled_var,
            emit_node_progress,
        )

        on_token = None
        if run_manager:
            async def _on_token(token: str, *args):
                emit_node_progress()
                await run_manager.on_llm_new_token(token)
            on_token = _on_token

        # Native Event Streaming Emitter for Thinking Blocks
        async def _on_thinking(thinking: str, agent_name: str = "", complete: bool = False, *args, **kwargs):
            emit_node_progress()
            # Dispatches native event captured by graph.astream_events
            # Deduce complete status if not provided but append is in kwargs
            is_complete = complete
            if not is_complete and "append" in kwargs:
                is_complete = not kwargs["append"]
                
            config = run_manager.get_config() if run_manager and hasattr(run_manager, "get_config") else None
            # Capture attribution HERE, in the subagent's own execution context: the
            # outer astream_events loop runs with these ContextVars reset to the
            # supervisor, so it cannot recover them. parent_id (the delegation tool's
            # correlation id) is the grouping key that nests this thought under the
            # subagent's panel; it is None for the supervisor's own reasoning.
            await adispatch_custom_event(
                "agent_thinking",
                {
                    "thinking": thinking,
                    "agent_name": agent_name or agent_name_var.get() or "Agent",
                    "parent_id": parent_correlation_id_var.get(),
                    "complete": is_complete,
                    "type": kwargs.get("type", "reasoning"),
                    "append": kwargs.get("append", not is_complete)
                },
                config=config
            )

        agent_name = agent_name_var.get()
        thinking_enabled_override = kwargs.get("thinking_enabled", self.thinking_enabled)
        if thinking_enabled_override is None:
            thinking_enabled_override = thinking_enabled_var.get()

        emit_node_progress()
        response: GenerateResponse = await self.adapter.generate(
            messages=chad_messages,
            tools=tools,
            temperature=kwargs.get("temperature", self.temperature),
            thinking_level=kwargs.get("thinking_level", self.thinking_level),
            thinking_enabled=thinking_enabled_override,
            on_token=on_token,
            on_thinking=_on_thinking,
            agent_name=agent_name,
            **kwargs
        )
        
        # 4. Convert back to ChatResult
        tool_calls = []
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_calls.append({
                    "name": tc["name"],
                    "args": tc["args"],
                    "id": tc["id"],
                    "type": "tool_call",  # Required by LangGraph ToolNode _validate_tool_call
                })

        # Inject standard LangChain response_metadata (which automatically handles usage metrics)
        metadata = {}
        if response.usage:
            from dataclasses import asdict
            metadata["token_usage"] = asdict(response.usage)

        additional_kw: Dict[str, Any] = {}
        if tool_calls:
            additional_kw["tool_calls"] = tool_calls
        if response.finish_reason:
            additional_kw["finish_reason"] = response.finish_reason
            if response.finish_reason == "error" and response.raw:
                # Preserve the actual provider error text (e.g. LM Studio's "Context size has
                # been exceeded") -- without this, only the generic "error" flag survives past
                # this point and the real cause is only visible by grepping the Brain's own log
                # file, not from the turn's own response/metadata.
                additional_kw["error_detail"] = str(response.raw)[:500]
        if response.reasoning:
            # Persist reasoning/thinking text into LangSmith via additional_kwargs.
            # Both adapters populate GenerateResponse.reasoning from streamed thinking tokens;
            # without this, that signal is lost after the streaming callback fires.
            additional_kw["reasoning"] = response.reasoning
            logger.debug(f"[ LC_WRAPPER ] Persisting reasoning to LangSmith: {len(response.reasoning)} chars")

        ai_message = AIMessage(
            content=response.text or "",
            response_metadata=metadata,
            additional_kwargs=additional_kw
        )
        if tool_calls:
            ai_message.tool_calls = tool_calls

        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _to_chad_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        raw_msgs = []
        for m in messages:
            if isinstance(m, HumanMessage):
                msg = {"role": "user", "content": m.content}
                if hasattr(m, "additional_kwargs"):
                    if "content_blocks" in m.additional_kwargs:
                        msg["content_blocks"] = m.additional_kwargs["content_blocks"]
                    if "image_base64" in m.additional_kwargs:
                        msg["image_base64"] = m.additional_kwargs["image_base64"]
                raw_msgs.append(msg)
            elif isinstance(m, AIMessage):
                msg = {"role": "assistant", "content": m.content}
                if hasattr(m, "tool_calls") and m.tool_calls:
                    msg["tool_calls"] = [{"function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))}, "id": tc["id"]} for tc in m.tool_calls]
                raw_msgs.append(msg)
            elif isinstance(m, ToolMessage):
                raw_msgs.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                    "content": m.content
                })
            elif isinstance(m, SystemMessage):
                raw_msgs.append({"role": "system", "content": m.content})
        
        # Merge consecutive roles and clean up (OpenAI compatibility)
        merged_msgs = []
        for m in raw_msgs:
            # Skip empty assistant messages if they don't have tool calls
            if m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls"):
                continue

            if not merged_msgs:
                merged_msgs.append(m)
                continue
            
            last = merged_msgs[-1]
            # Merge if same role (OpenAI compatibility)
            # CRITICAL: DO NOT merge 'tool' role messages. Each must correspond to a tool_call_id.
            if last["role"] == m["role"] and m["role"] != "tool":
                last_content = last.get("content") or ""
                new_content = m.get("content") or ""
                
                if last_content and new_content:
                    last["content"] = last_content + "\n\n" + new_content
                else:
                    last["content"] = last_content or new_content
                
                if "tool_calls" in m:
                    last["tool_calls"] = last.get("tool_calls", []) + m["tool_calls"]
            else:
                merged_msgs.append(m)
                
        return merged_msgs
