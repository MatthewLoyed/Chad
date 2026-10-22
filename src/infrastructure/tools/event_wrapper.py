"""
Utility to wrap tools with EventBus notifications for lifecycle tracking.
Decouples ToolRegistry from event-specific execution logic.
"""

import logging
import asyncio
import uuid
import hashlib
import json
from typing import Any, Dict, Optional
from src.application.events import EventBus, ToolStarted, ToolCompleted
from src.core.orchestration.context import parent_correlation_id_var

logger = logging.getLogger("ToolEventWrapper")

class ToolDisplayResolver:
    """Logic to convert raw tool calls into user-friendly status text."""

    @staticmethod
    def resolve(tool_name: str, args: Dict[str, Any], tool_instance: Optional[Any] = None) -> str:
        from src.core.tools.formatter import ToolFormatter
        return ToolFormatter.format(tool_name, args, tool_instance)


class ToolEventWrapper:
    """
    Wraps tools to emit lifecycle events (start/end) to the EventBus.
    
    WHY THIS EXISTS:
    In LangGraph-based systems, specialized agents (like Workspace Agent) execute 
    tools directly via LangGraph's ToolNode. This bypasses the traditional 
    ToolCoordinator, making tool calls "invisible" to the UI.
    
    By wrapping the tools at the Registry level before they are handed to LangChain, 
    we ensure that every execution—regardless of which agent triggers it—is 
    captured and broadcast to the frontend.
    """
    
    @staticmethod
    def wrap_as_langchain_tool(
        tool_name: str, 
        original_executor: Any, 
        event_bus: Optional[EventBus] = None,
        tool_instance: Optional[Any] = None
    ) -> Any:
        """
        Returns an async execution wrapper that publishes events.
        
        Args:
            tool_name: Canonical name of the tool
            original_executor: The object/method that actually runs the tool
            event_bus: EventBus instance to publish to
            tool_instance: The actual tool object (for display text resolution)
        """
        
        async def _exec_wrapper(config: Any = None, **kwargs):
            logger.debug(f"[ TOOL_WRAPPER ] Executing: {tool_name}")
            
            # Extract or generate correlation ID
            tool_call_id = ToolEventWrapper._get_tool_call_id(tool_name, config, kwargs)
            
            # Resolve display text
            display_text = ToolDisplayResolver.resolve(tool_name, kwargs, tool_instance)

            # 1. Publish ToolStarted
            current_parent = parent_correlation_id_var.get()
            if event_bus:
                from src.core.orchestration.context import current_turn_delivery
                event_bus.publish(ToolStarted(
                    tool_name=tool_name,
                    args=kwargs,
                    correlation_id=tool_call_id,
                    display_text=display_text,
                    parent_id=current_parent,
                    delivery=current_turn_delivery(),
                ))

            def _log_oats_outcome(is_success: bool):
                try:
                    from src.core.orchestration.context import current_turn_tool_queries_var
                    ctx = current_turn_tool_queries_var.get()
                    if not ctx:
                        return
                    
                    query_text = None
                    query_embedding = None
                    
                    if tool_name in ctx:
                        query_text, query_embedding = ctx[tool_name]
                    elif "__query__" in ctx:
                        query_text = ctx["__query__"]
                        
                    turn_id = ctx.get("__turn_id__")
                    
                    if query_text and query_embedding:
                        from src.infrastructure.database.supabase_manager import get_supabase_manager
                        supabase = get_supabase_manager()
                        if supabase:
                            # When Supabase isn't configured the insert routes through the
                            # LocalPostgrestShim (issue #470) — register the backing table first
                            # so the same write path persists outcomes locally with zero setup.
                            if not supabase.enabled:
                                from src.core.orchestration.oats_refiner import ensure_local_oats_tables
                                ensure_local_oats_tables()

                            async def _insert_and_increment():
                                insert_data = {
                                    "tool_name": tool_name,
                                    "query_text": query_text,
                                    "query_embedding": query_embedding,
                                    "outcome": "success" if is_success else "failure"
                                }
                                if turn_id:
                                    insert_data["turn_id"] = turn_id
                                if not supabase.enabled:
                                    # Supabase's tool_outcome_log.created_at has DEFAULT NOW();
                                    # the local table has no default, so stamp it here.
                                    from datetime import datetime, timezone
                                    insert_data["created_at"] = datetime.now(timezone.utc).isoformat()
                                res = await supabase.async_insert(
                                    "tool_outcome_log",
                                    insert_data
                                )
                                if res:
                                    from src.core.orchestration.oats_refiner import OATSRefiner
                                    await OATSRefiner.increment_and_check()

                            asyncio.create_task(_insert_and_increment())
                except Exception as log_err:
                    logger.error(f"[ OATS ] Failed to log outcome for {tool_name}: {log_err}")

            try:
                # 2. Execute
                # We assume original_executor is the ToolRegistry's execute_tool or similar
                result = await original_executor(tool_name, kwargs)

                # 3. Publish ToolCompleted with the REAL outcome.
                #
                # Tools signal failure by RETURNING {"success": False, ...}; they do
                # not raise. Treating "did not raise" as success meant every failed
                # tool call was logged as a success — and three self-improvement
                # systems read that column: OATS embedding refinement (tool
                # selection), capability_patterns (which groups "successful" runs
                # into suggested skills), and reflection (lesson generation). All
                # three were being trained on data that said everything worked.
                # The UI was wrong too: a failed call rendered as a green tick.
                succeeded = not (isinstance(result, dict) and result.get("success") is False)
                error_text = None
                if not succeeded:
                    error_text = str(result.get("error") or "Tool reported failure.")

                if event_bus:
                    from src.core.orchestration.context import current_turn_delivery
                    awaiting_user = isinstance(result, dict) and bool(result.get("_pending_approval"))
                    event_bus.publish(ToolCompleted(
                        tool_name=tool_name,
                        result=result,
                        success=succeeded,
                        error=error_text,
                        correlation_id=tool_call_id,
                        display_text=display_text, # Pass start text for consistency
                        parent_id=current_parent,
                        delivery=current_turn_delivery(),
                        awaiting_user=awaiting_user,
                    ))
                _log_oats_outcome(is_success=succeeded)
                return result
                
            except Exception as e:
                # 4. Publish ToolCompleted (Failure)
                if event_bus:
                    from src.core.orchestration.context import current_turn_delivery
                    event_bus.publish(ToolCompleted(
                        tool_name=tool_name,
                        result=None,
                        success=False,
                        error=str(e),
                        correlation_id=tool_call_id,
                        display_text=display_text,
                        parent_id=current_parent,
                        delivery=current_turn_delivery(),
                    ))
                _log_oats_outcome(is_success=False)
                raise

        return _exec_wrapper

    @staticmethod
    def _get_tool_call_id(tool_name: str, config: Any, kwargs: Dict[str, Any]) -> str:
        """Extract tool_call_id from LangChain config or generate a fallback."""
        tool_call_id = None
        if config and hasattr(config, 'get'):
            metadata = config.get('metadata', {})
            tool_call_id = metadata.get('tool_call_id')
            
        if not tool_call_id:
            # Deterministic hash of args to match start/end within a single turn
            arg_str = json.dumps(kwargs, sort_keys=True, default=str)
            tool_call_id = f"{tool_name}_{hashlib.md5(arg_str.encode()).hexdigest()[:8]}"
            
        return tool_call_id
