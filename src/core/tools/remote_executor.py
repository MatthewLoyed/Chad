"""
Remote Tool Executor
Delegates tool execution to a remote Lite Client (Limb) via WebSockets.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from src.core.tools.base_executor import BaseToolExecutor
from src.shared.logger import setup_logger
from src.remote.protocol import ChadMessage, MessageType
from src.shared.context import RequestContext
from src.core.tools.tool_timeouts import get_tool_timeout

logger = setup_logger("RemoteToolExecutor")

class RemoteToolExecutor(BaseToolExecutor):
    """
    Executes tools by proxying them to a connected Lite Client.
    Used by the Headless Brain in Cloud mode.
    """
    
    def __init__(
        self,
        send_message_callback: Callable[[ChadMessage], Any],
        wait_for_response_callback: Callable[[str], Any],
        on_tool_start: Optional[Callable] = None,
        on_tool_end: Optional[Callable] = None
    ):
        """
        Initialize remote executor.
        
        Args:
            send_message_callback: Callback to send a ChadMessage to the client
            wait_for_response_callback: Callback to wait for a TOOL_RESPONSE by correlation_id
            on_tool_start: Optional UI notification for tool start
            on_tool_end: Optional UI notification for tool completion
        """
        self.send_message = send_message_callback
        self.wait_for_response = wait_for_response_callback
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self._tools_called_this_session = set()

    def reset_session_tracking(self):
        self._tools_called_this_session.clear()

    def format_action_message(self, tool_name: str, params: Dict[str, Any]) -> str:
        # We can reuse formatting logic or keep it simple for the server logs
        tool_display = tool_name.replace('_', ' ').title()
        return f"[ REMOTE ] Executing {tool_display} on Limb..."

    async def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        active_model: Optional[Any] = None,
        context: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """
        Proxies the tool call to the Limb.
        """
        tool_name = tool_call.get('name')
        tool_id = tool_call.get('id')
        params = tool_call.get('params', {})
        
        correlation_id = tool_id or str(asyncio.get_event_loop().time())
        action_message = self.format_action_message(tool_name, params)
        
        logger.warning(f"Requesting remote tool: {tool_name} (id={correlation_id})")

        # 1. Notify UI (Start)
        if self.on_tool_start:
            await self.on_tool_start(tool_name, params, action_message, correlation_id)

        # 2. Wrap in TOOL_REQUEST message
        msg = ChadMessage(
            MessageType.TOOL_REQUEST,
            payload={"tool": tool_name, "params": params},
            correlation_id=correlation_id
        )

        try:
            # 3. Send and await response
            await self.send_message(msg)
            
            # Wait for response via the server's response tracker.
            # Use the same per-tool timeout the Limb applies, so the Brain doesn't
            # give up early on tools that legitimately run long (e.g. interactive
            # OAuth flows) while the Limb is still waiting on them.
            result_msg = await self.wait_for_response(correlation_id, timeout=get_tool_timeout(tool_name))
            
            if not result_msg:
                return {"success": False, "error": "Remote tool execution timed out or failed."}

            result = result_msg.payload
            
            # 4. Notify UI (End)
            if self.on_tool_end:
                await self.on_tool_end(tool_name, result, correlation_id)
                
            return result

        except Exception as e:
            logger.error(f"Remote tool execution error: {e}")
            return {"success": False, "error": f"Remote error: {str(e)}"}
