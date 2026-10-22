from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from src.core.orchestration.context import (
    agent_name_var,
    parent_correlation_id_var,
    current_turn_delivery,
    turn_id_var,
)
from src.application.events.events import ResponseFragment, TokenUsageUpdate, TurnCompleteEvent


class TurnTelemetry:
    """Per-turn streaming hooks handed to the LangGraph engine.

    Publishes token/thinking/usage telemetry to the EventBus, forwards to the
    caller's optional ``on_token`` / ``on_thinking`` callbacks, records
    time-to-first-token, and accumulates usage into the shared ``metrics`` dict
    (mutated in place so the coordinator sees the live values).
    """

    def __init__(
        self,
        *,
        event_bus: Optional[Any],
        metrics: Dict[str, Any],
        turn_start_time: float,
        on_token: Optional[Callable] = None,
        on_thinking: Optional[Callable] = None,
    ) -> None:
        self.event_bus = event_bus
        self.metrics = metrics
        self.turn_start_time = turn_start_time
        self._on_token = on_token
        self._on_thinking = on_thinking
        self.first_token_time: Optional[float] = None

    def _mark_first_token(self) -> None:
        if self.first_token_time is None:
            self.first_token_time = time.time()
            self.metrics["ttft"] = round(self.first_token_time - self.turn_start_time, 2)

    async def on_token(self, token: str) -> None:
        self._mark_first_token()
        if self.event_bus:
            self.event_bus.publish(ResponseFragment(
                text=token,
                delivery=current_turn_delivery(),
                client_turn_id=turn_id_var.get(),
            ))
        if self._on_token:
            if asyncio.iscoroutinefunction(self._on_token):
                await self._on_token(token)
            else:
                self._on_token(token)

    async def on_thinking(
        self,
        text: str,
        type: str = "thought",
        append: bool = True,
        agent_name: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        self._mark_first_token()

        # Prefer explicit attribution carried from the subagent's execution context
        # (the engine forwards it for subagent reasoning); fall back to the ContextVars
        # for the supervisor's own thoughts, where they are correctly "Supervisor"/None.
        current_agent = agent_name if agent_name is not None else agent_name_var.get()
        current_parent = parent_id if parent_id is not None else parent_correlation_id_var.get()

        if self.event_bus:
            from src.application.events import ThinkingUpdate

            self.event_bus.publish(ThinkingUpdate(
                thinking_text=text,
                thinking_type=type,
                append=append,
                agent_name=current_agent,
                parent_id=current_parent,
                delivery=current_turn_delivery(),
            ))
        if self._on_thinking:
            if asyncio.iscoroutinefunction(self._on_thinking):
                await self._on_thinking(text, type, append)
            else:
                self._on_thinking(text, type, append)

    async def on_usage(self, usage: dict) -> None:
        self.metrics["prompt_tokens"] = usage.get("prompt_tokens", 0)
        self.metrics["completion_tokens"] = usage.get("completion_tokens", 0)
        self.metrics["total_tokens"] = usage.get("total_tokens", 0)
        self.metrics["reasoning_tokens"] = usage.get("reasoning_tokens")
        self.metrics["context_window"] = usage.get("context_window")
        if self.event_bus:
            self.event_bus.publish(TokenUsageUpdate(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                reasoning_tokens=usage.get("reasoning_tokens"),
                context_window=usage.get("context_window"),
                estimated_prompt_tokens=self.metrics.get("estimated_prompt_tokens"),
            ))

    def complete(self, outcome, *, session_id: str, client_turn_id: Optional[str]) -> None:
        """Fire the completion event and the final token metrics."""
        from src.core.orchestration.context import current_turn_delivery

        if self.event_bus:
            self.event_bus.publish(TurnCompleteEvent(
                turn_id=session_id,
                response=outcome.response,
                awaiting_approval=outcome.awaiting_approval,
                awaiting_input=outcome.awaiting_input,
                delivery=current_turn_delivery(),
                client_turn_id=client_turn_id,
                pending_approval=outcome.pending_approval,
            ))

        # Always publish final metrics — ensures the UI updates even if LM Studio
        # didn't send a usage chunk during streaming.
        m = self.metrics
        if self.event_bus:
            self.event_bus.publish(TokenUsageUpdate(
                prompt_tokens=m.get("prompt_tokens", 0),
                completion_tokens=m.get("completion_tokens", 0),
                total_tokens=m.get("total_tokens", 0),
                reasoning_tokens=m.get("reasoning_tokens"),
                context_window=m.get("context_window"),
                speed=m.get("speed"),
                ttft=m.get("ttft"),
                stop_reason=outcome.stop_reason,
                estimated_prompt_tokens=m.get("estimated_prompt_tokens"),
                pre_model_breakdown=m.get("pre_model_breakdown"),
            ))
