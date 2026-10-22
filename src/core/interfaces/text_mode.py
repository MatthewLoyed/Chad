"""
TextModeClient — direct path (PATH 3, integration tests & Skill sub-brains)
============================================================================

This client dispatches directly to TurnCoordinator without a WebSocket or HTTP hop.
See message_commands.py for the full three-path architecture diagram.

Persistence contract for this path:
    NONE — this client intentionally does NOT write to Supabase.
    The LangGraph checkpointer is the sole state store for these callers.
    Integration tests and Skills are ephemeral; they do not accumulate DB history.

If you add Supabase persistence here in the future, follow the cardinal rule:
    defer the write until AFTER turn_coordinator.dispatch() returns, never before.
"""

import logging
from typing import Dict, Any, List, Optional, Callable

from src.shared.logger import setup_logger
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter
from src.shared.action_scratchpad import ActionScratchpadMixin

logger = setup_logger("TextMode")
# --- LIMITS ---
MAX_TOOLS_PER_TURN = 30
SOFT_TOOL_LIMIT = 20

from src.application.events import ThinkingUpdate, TurnStartEvent, TurnCompleteEvent

class TextModeClient(ActionScratchpadMixin):
    """
    Unified text client that routes through an AIAdapter.
    Consolidates execution through the TurnCoordinator.
    """
    
    def __init__(
        self,
        adapter: AIAdapter,
        tool_registry: Optional[Any] = None,
        tool_executor: Optional[Any] = None,
        memory_engine: Optional[Any] = None,
        on_thinking: Optional[Callable] = None,
        on_token: Optional[Callable] = None,
        event_bus: Optional[Any] = None,
        turn_coordinator: Optional[Any] = None
    ):
        super().__init__()
        self._adapter = adapter
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.memory_engine = memory_engine
        self.event_bus = event_bus
        
        # Canonical TurnCoordinator (injected)
        from src.core.orchestration.turn_coordinator import TurnCoordinator
        self.turn_coordinator = turn_coordinator
        
        # Fallback construction if not provided by DI container
        if not self.turn_coordinator:
            logger.warning("[ TEXT MODE ] TurnCoordinator not provided, bootstrapping local fallback instance.")
            self.turn_coordinator = TurnCoordinator(
                tool_registry=self.tool_registry,
                tool_executor=self.tool_executor,
                memory_engine=self.memory_engine,
                event_bus=self.event_bus,
                default_adapter=self._adapter
            )

        # Callbacks (Supported via direct injection to dispatch)
        self.on_thinking = on_thinking
        self.on_token = on_token
        
        # Conversation History (maintained for local tracking, actual persistent history managed by LangGraph)
        self.conversation_history: List[Dict[str, Any]] = []
        
        self._stop_requested = False
        self.consolidation_manager = None

    @property
    def adapter(self) -> AIAdapter:
        return self._adapter

    @adapter.setter
    def adapter(self, new_adapter: AIAdapter):
        """Hot-swap the adapter and update the orchestration engine."""
        self._adapter = new_adapter
        if self.turn_coordinator:
            self.turn_coordinator.default_adapter = new_adapter
            logger.info(f"[ TEXT MODE ] TurnCoordinator updated with new adapter: {new_adapter.__class__.__name__}")

    def set_consolidation_manager(self, manager):
        self.consolidation_manager = manager
        if self.turn_coordinator:
            self.turn_coordinator.consolidation_manager = manager
        logger.info("[ TEXT MODE ] Consolidation Manager attached.")

    def stop(self):
        self._stop_requested = True
        logger.info("[ STOP ] : Stop requested in TextModeClient.")

    async def send_message(
        self,
        text: str,
        image_base64: Optional[str] = None,
        content_blocks: Optional[List[Dict[str, Any]]] = None,
        max_tool_calls: int = 15,
        confirm_continuation: Optional[Callable] = None,
        thinking_level: str = "medium",
        context: Optional[Any] = None,
        conversation_id: Optional[str] = None,
        on_token: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delegates ReAct execution directly to the TurnCoordinator.
        """
        self._stop_requested = False
        self._reset_scratchpad()
        
        # 1. Compose RequestContext
        from src.shared.context import RequestContext
        if not context:
            context = RequestContext(
                session_id=conversation_id or "default",
                platform="pc",
                initiator="user"
            )
        elif not context.session_id and conversation_id:
            context.session_id = conversation_id

        # Keep local legacy history track for compatibility
        user_msg = {"role": "user", "content": text}
        if content_blocks:
            user_msg["content_blocks"] = content_blocks
        elif image_base64:
            user_msg["image_base64"] = image_base64
        self.conversation_history.append(user_msg)

        try:
            logger.info(f"[ TEXT MODE ] Dispatching message execution to TurnCoordinator (Session: {context.session_id})")
            
            # Direct ReAct loop delegation!
            final_response = await self.turn_coordinator.dispatch(
                prompt=text,
                context=context,
                adapter=self._adapter,
                content_blocks=content_blocks,
                image_base64=image_base64,
                category=category,
                max_tool_calls=max_tool_calls,
                on_token=on_token or self.on_token,
                on_thinking=self.on_thinking,
                thinking_level=thinking_level,
                metadata=metadata
            )

            # Sync Assistant message back to local history
            self.conversation_history.append({"role": "assistant", "content": final_response})

            # Compatibility response format for legacy local loops
            thinking_events = getattr(self.turn_coordinator, 'last_turn_thinking_events', [])
            turn_metrics = getattr(self.turn_coordinator, 'last_turn_metrics', {})
            return {
                "success": True,
                "response": final_response,
                "tool_results": [],  # Decoupled from direct flow
                "tool_call_count": 0, # Decoupled
                "thinking_events": thinking_events,
                "turn_metrics": turn_metrics
            }

        except Exception as e:
            logger.error(f"[ TEXT MODE ] Execution via TurnCoordinator failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def clear_history(self):
        self.conversation_history.clear()
        logger.info("Conversation history cleared.")

    async def perform_manual_consolidation(self, session_id: str = "default") -> bool:
        """
        Manually trigger conversation memory compaction for the given session.
        """
        if not self.turn_coordinator:
            logger.error("[ TEXT MODE ] Cannot force consolidation: no turn_coordinator attached.")
            return False
        return await self.turn_coordinator.force_compaction(session_id, self._adapter)
