"""
Hybrid Tool Executor
Routes tool calls to either Local (Cloud/Brain) or Remote (Limb) based on a partition manifest.
"""

import logging
from typing import Dict, Any, Optional
from src.core.tools.base_executor import BaseToolExecutor
from src.core.tools.partition import TOOL_MANIFEST, ToolPartition
from src.shared.logger import setup_logger
from src.shared.context import RequestContext

logger = setup_logger("HybridToolExecutor")

class HybridToolExecutor(BaseToolExecutor):
    """
    Traffic controller for tool execution in a client-server architecture.
    """
    
    def __init__(
        self,
        local_executor: BaseToolExecutor,
        remote_executor: BaseToolExecutor
    ):
        """
        Initialize hybrid executor.
        
        Args:
            local_executor: Runs tools directly on the current machine (Brain/Cloud)
            remote_executor: Proxies tools to the remote machine (Limb/PC)
        """
        self.local_executor = local_executor
        self.remote_executor = remote_executor

    @property
    def tools(self):
        """Expose the tool registry for agents and supervisor."""
        return getattr(self.local_executor, 'tools', None)

    def reset_session_tracking(self):
        self.local_executor.reset_session_tracking()
        self.remote_executor.reset_session_tracking()


    def format_action_message(self, tool_name: str, params: Dict[str, Any]) -> str:
        partition = TOOL_MANIFEST.get(tool_name, ToolPartition.CLOUD)
        # Must follow the same routing as execute_tool_call, or the "what Chad is doing"
        # line would be built by an executor that isn't the one running the tool.
        if partition == ToolPartition.CLOUD or not self._client_services_local_tools():
            return self.local_executor.format_action_message(tool_name, params)
        return self.remote_executor.format_action_message(tool_name, params)

    async def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        active_model: Optional[Any] = None,
        context: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """
        Routes the tool call based on the manifest.
        """
        tool_name = tool_call.get('name')
        partition = TOOL_MANIFEST.get(tool_name, ToolPartition.CLOUD)

        if partition == ToolPartition.CLOUD:
            logger.info(f"[ HYBRID ] Routing '{tool_name}' to CLOUD executor.")
            return await self.local_executor.execute_tool_call(tool_call, active_model, context)

        if not self._client_services_local_tools():
            logger.info(
                f"[ HYBRID ] '{tool_name}' is Limb-partitioned but this turn's client can't "
                f"service it — running it in the Brain (same machine)."
            )
            return await self.local_executor.execute_tool_call(tool_call, active_model, context)

        logger.warning(f"[ HYBRID ] Routing '{tool_name}' to LOCAL proxy (Limb).")
        return await self.remote_executor.execute_tool_call(tool_call, active_model, context)

    @staticmethod
    def _client_services_local_tools() -> bool:
        """Can the client that started this turn actually run a Limb-partitioned tool?

        Only the desktop Limb can — it owns the local tool registry. A browser/PWA client
        answers TOOL_REQUEST with "Local tools are unavailable in the browser client", so
        proxying to it could only ever fail: 30 tools (read_file, run_host_command,
        control_volume, capture_screen, ...) were simply unavailable from the phone even
        though the Brain runs on the very machine they target.

        Defaults to True when unset so autonomous/cron turns — which no client started —
        keep the existing proxy behaviour rather than silently changing where they run.

        NOTE: this only decides WHERE the tool runs. Tools needing an interactive desktop
        session (Click/Type/capture_screen) may still fail in a headless Brain — but they
        already failed outright from a browser, so this is strictly an improvement. See
        issue #715 for the broader partition question.
        """
        try:
            from src.core.orchestration.context import client_type_var
            client_type = client_type_var.get()
        except Exception:
            return True
        if not client_type:
            return True
        return client_type == "desktop"
