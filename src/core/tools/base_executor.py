"""
Base Tool Executor Interface
Defines the standard interface for executing tools (Dependency Injection).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from src.shared.context import RequestContext

class BaseToolExecutor(ABC):
    """
    Abstract interface for tool execution.
    Allows SessionManager to be agnostic of local vs remote execution.
    """
    
    @abstractmethod
    def reset_session_tracking(self):
        """Reset session-level tracking (e.g., prerequisites)."""
        pass

    @abstractmethod
    async def execute_tool_call(
        self,
        tool_call: Dict[str, Any],
        active_model: Optional[Any] = None,
        context: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool call and return the result.
        
        Args:
            tool_call: Tool call dictionary (id, name, params)
            active_model: Active model instance for multimodal tools
            
        Returns:
            Execution result dictionary
        """
        pass

    @abstractmethod
    def format_action_message(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Format a user-friendly message for the tool action."""
        pass
