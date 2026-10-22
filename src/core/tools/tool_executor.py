"""
Tool Executor - Handles tool call execution and formatting
Extracted from SessionManager for cleaner separation of concerns.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Set
from src.shared.logger import setup_logger
from src.shared.terminal_debug import log_action, log_error
from src.core.tools.formatter import ToolFormatter
from src.core.tools.base_executor import BaseToolExecutor
from src.shared.context import RequestContext
from src.core.tools.tool_timeouts import get_tool_timeout

logger = setup_logger("ToolExecutor")

# TOOL_PREREQUISITES moved to ToolCoordinator


from src.application.events import ToolStarted, ToolCompleted

class LocalToolExecutor(BaseToolExecutor):
    """
    Standard tool executor for local (desktop) operation.
    Handles tool call execution, formatting, and response management.
    """
    
    def __init__(
        self, 
        tool_registry, 
        on_tool_start: Optional[Callable] = None, 
        on_tool_end: Optional[Callable] = None, 
        app_reference=None,
        interrupt_check: Optional[Callable[[], bool]] = None,
        memory_engine: Optional[Any] = None,
        event_bus: Optional[Any] = None
    ):
        """
        Initialize tool executor.
        
        Args:
            tool_registry: ToolRegistry instance for executing tools
            on_tool_start: Callback when tool execution starts
            on_tool_end: Callback when tool execution completes
            app_reference: Reference to the main app (for UI integration)
            interrupt_check: Callback that returns True if execution should be interrupted
            memory_engine: Shared memory engine instance for session persistence
            event_bus: Shared EventBus instance for publishing decoupled events
        """
        self.tools = tool_registry
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.app_reference = app_reference
        self.interrupt_check = interrupt_check
        self.memory_engine = memory_engine
        self.event_bus = event_bus
        # Session tool tracking moved to ToolCoordinator

    @property
    def registry(self):
        """Expose the tool registry for agents and supervisor."""
        return self.tools
    
    def reset_session_tracking(self):
        """Reset tool tracking for a new session/conversation."""
        logger.info("[ TOOL_EXECUTOR ] Session tool tracking reset")
    
    
    def format_action_message(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Format a user-friendly message for the tool action."""
        tool_instance = self.tools.get_tool(tool_name) if self.tools else None
        return ToolFormatter.format(tool_name, params, tool_instance)
    
    async def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        active_model: Optional[Any] = None,
        context: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool call from the model.
        
        Args:
            tool_call: Tool call dictionary with id, name, and params
            active_model: Active model instance (for special tool handling like screenshots)
            
        Returns:
            Tool execution result dictionary
        """
        tool_name = tool_call.get('name')
        tool_id = tool_call.get('id')
        params = tool_call.get('params', {})
        
        logger.info(f"Tool call received: {tool_name} (id={tool_id})")
        
        # ====================================================================
        # INTERRUPT CHECK - Stop button pressed?
        # ====================================================================
        if self.interrupt_check and self.interrupt_check():
            logger.warning(f"[ TOOL_EXECUTOR ] Interrupt detected - skipping tool '{tool_name}'")
            return {
                "success": False,
                "error": "Execution interrupted by user",
                "interrupted": True
            }
        
        # Prerequisite check moved to ToolCoordinator
        
        # 1. Log action to terminal with formatted output
        action_message = self.format_action_message(tool_name, params)
        log_action(action_message, logger=logger, tool_id=tool_id)
        
        # Get tool instance from registry
        tool_instance = self.tools.get_tool(tool_name) if self.tools else None
        tool_exists = tool_instance is not None
        
        # Notify UI tool started
        if isinstance(params, dict):
            ui_params = dict(params)
        elif isinstance(params, list) and len(params) > 0:
            ui_params = {"name": str(params[0]), "_positional": True}
        else:
            ui_params = {"_raw_params": str(params)}

        if tool_name == 'open_browser' and 'query' in ui_params and 'url' not in ui_params:
             ui_params['url'] = ui_params['query']

        if tool_instance and hasattr(tool_instance, 'category'):
            ui_params['_category'] = tool_instance.category

        # 1. Emit EventBus Event (PREFERRED)
        if self.event_bus:
            from src.core.orchestration.context import current_turn_delivery
            self.event_bus.publish(ToolStarted(
                tool_name=tool_name,
                args=ui_params,
                correlation_id=tool_id,
                display_text=action_message,
                delivery=current_turn_delivery(),
            ))

        # 2. Legacy Callback (COMPATIBILITY)
        if self.on_tool_start:
            await self.on_tool_start(tool_name, ui_params, action_message, tool_id)
        
        try:
            # Execute the tool with timeout (per-tool override or default)
            if tool_exists:
                timeout = get_tool_timeout(tool_name)
                
                # Copy params for actual execution to be ultra-safe
                if isinstance(params, dict):
                    exec_params = dict(params)
                elif isinstance(params, list) and len(params) > 0:
                    # Map first list element to 'name' as a fallback for App tool
                    exec_params = {"name": str(params[0])}
                else:
                    exec_params = {}
                
                # Note: We do NOT inject _category or _app_reference into exec_params 
                # unless we are sure the tool can handle them. 
                # For now, let's keep them out of the tool's execute() method.
                
                # Inject explicit request context and shared resources ONLY for local tools.
                # MCP tools and Skills must only receive JSON-serializable arguments from the model
                # to avoid pydantic serialization errors.
                from src.tools.base_tool import BaseTool
                if isinstance(tool_instance, BaseTool):
                    if context:
                        exec_params['context'] = context
                    if self.memory_engine:
                        exec_params['memory_engine'] = self.memory_engine
                    
                    # Inject the system registry to enable skill-to-tool chaining
                    exec_params['tool_registry'] = self.tools
                
                try:
                    result = await asyncio.wait_for(
                        self.tools.execute_tool(tool_name, exec_params),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Tool '{tool_name}' execution timed out after {timeout} seconds")
                    result = {
                        "success": False,
                        "error": f"Tool execution timed out after {timeout} seconds"
                    }
            else:
                # Tool not in registry - likely Google Search (built-in) or unknown tool
                logger.warning(f"Tool '{tool_name}' not found in registry - may be built-in (Google Search) or invalid")
                
                result = {
                    "success": True,
                    "result": f"Built-in tool '{tool_name}' is being handled by the API"
                }
            
            # Special handling for capture_screen tool: send image as multimodal input
            result_for_response = result
            if tool_name == "capture_screen" and result.get('success'):
                image_bytes = result.get('image_bytes')
                if image_bytes and active_model:
                    try:
                        # Send image to Gemini via send_image() method
                        await active_model.send_image(image_bytes)
                    except Exception as e:
                        logger.error(f"Failed to send screenshot to Gemini: {e}", exc_info=True)
                        # Continue with tool response even if image sending fails
                
                # Remove image_bytes from result before sending tool response
                result_for_response = {
                    k: v for k, v in result.items() 
                    if k != 'image_bytes'
                }
            
            success = result.get('success', False)
            error_msg = result.get('error') or result.get('result') or "Unknown error" if not success else None

            # 1. Emit EventBus Event (PREFERRED)
            if self.event_bus:
                from src.core.orchestration.context import current_turn_delivery
                self.event_bus.publish(ToolCompleted(
                    tool_name=tool_name,
                    result=result_for_response,
                    success=success,
                    error=error_msg,
                    correlation_id=tool_id,
                    delivery=current_turn_delivery(),
                ))

            # 2. Legacy Callback (COMPATIBILITY)
            if self.on_tool_end:
                await self.on_tool_end(tool_name, result, tool_id)
            
            success = result.get('success', False)
            if not success:
                error_msg = result.get('error') or result.get('result') or "Unknown error"
                log_error(f"Tool '{tool_name}' failed: {error_msg}", logger=logger)
            
            logger.info(f"Tool execution complete: {tool_name} (id={tool_id}) success={success}")
            return result_for_response
            
        except Exception as e:
            logger.error(f"Error handling tool call: {e}", exc_info=True)
            
            # Return error result
            error_result = {
                "success": False,
                "error": str(e)
            }
            
            return error_result


# Backward compatibility alias
ToolExecutor = LocalToolExecutor
