"""
Background Agent Manager - Detached Task Execution
Manages 'Cowork'-style parallel ReAct loops that run independently of the main session.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.orchestration.turn_coordinator import TurnCoordinator
    from src.core.tools.mcp_registry import MCPRegistry

logger = logging.getLogger("BackgroundAgents")


class BackgroundAgentManager:
    """
    Manages background ReAct loops that run detached from the main session.
    Enables parallel task execution where users can queue multiple tasks.
    """
    
    def __init__(
        self,
        turn_coordinator: Any,
        tool_coordinator: Any,
        mcp_registry: Optional["MCPRegistry"] = None,  # Deprecated
        tool_registry: Any = None,  # Deprecated
        show_notification_callback: Optional[Callable] = None,
        on_complete_callback: Optional[Callable] = None
    ):
        """
        Initialize the background agent manager.
        
        Args:
            turn_coordinator: TurnCoordinator (modern)
            tool_coordinator: Centralized action coordinator (Action Seam)
            mcp_registry: [DEPRECATED]
            tool_registry: [DEPRECATED]
            show_notification_callback: Callback to show OS notifications
            on_complete_callback: Callback when a background task completes
        """
        self.turn_coordinator = turn_coordinator
        self.tool_coordinator = tool_coordinator
        self.show_notification = show_notification_callback
        self.on_complete = on_complete_callback
        self._tasks: List[asyncio.Task] = []
    
    async def spawn(
        self, 
        goal: str, 
        context_manifest: Optional[str] = None,
        task_name: Optional[str] = None
    ) -> asyncio.Task:
        """
        Spawn a background ReAct loop that runs detached from the main session.
        
        Args:
            goal: The task description for the Frontal Lobe
            context_manifest: Optional JSON context to pass
            task_name: Human-readable name for tracking
        
        Returns:
            asyncio.Task that can be awaited or monitored
        """
        if not self.turn_coordinator:
             raise RuntimeError("BackgroundAgentManager cannot spawn tasks without a valid engine (TurnCoordinator).")

        async def _background_worker():
            """Inner worker that runs the ReAct loop and handles completion."""
            try:
                logger.info(f"[ BACKGROUND ] : Starting task '{task_name or 'unnamed'}'...")
                
                # Check if we have the modern TurnCoordinator
                if hasattr(self.turn_coordinator, 'dispatch'):
                    # MODERN: Use TurnCoordinator
                    from src.shared.context import RequestContext
                    ctx = RequestContext(
                        platform="background",
                        session_id=f"bg_{task_name or 'task'}",
                        user_id="background_agent"
                    )
                    result = await self.turn_coordinator.dispatch(
                        prompt=goal,
                        context=ctx,
                        metadata={"is_background": True, "task_name": task_name}
                    )
                else:
                    logger.error("[ BACKGROUND ] : Engine does not support 'dispatch'. Background tasks require TurnCoordinator.")
                    result = "Error: Invalid engine configuration."
                logger.info(f"[ BACKGROUND ] : Task '{task_name or 'unnamed'}' completed.")
                
                # Notify via callback if registered
                if self.on_complete:
                    self.on_complete(task_name, result)
                
                # Show OS notification
                if self.show_notification:
                    self.show_notification(
                        title="Task Complete",
                        message=f"{task_name or 'Background task'} finished.",
                        icon="✅",
                        duration=8
                    )
                
                return result
            except Exception as e:
                logger.error(f"[ BACKGROUND ] : Task '{task_name}' failed: {e}")
                if self.show_notification:
                    self.show_notification(
                        title="Task Failed",
                        message=f"{task_name or 'Background task'}: {str(e)[:50]}",
                        icon="❌",
                        duration=10
                    )
                return {"error": str(e)}
        
        # Create and track the task
        task = asyncio.create_task(_background_worker())
        task.set_name(task_name or f"bg_agent_{len(self._tasks)}")
        self._tasks.append(task)
        
        logger.info(f"[ BACKGROUND ] : Spawned task '{task.get_name()}' (Total: {len(self._tasks)})")
        return task
    
    def get_status(self) -> List[Dict[str, Any]]:
        """
        Get status of all background tasks.
        
        Returns:
            List of task status dictionaries with name, done, and result/error
        """
        statuses = []
        for task in self._tasks:
            status = {
                "name": task.get_name(),
                "done": task.done(),
            }
            if task.done():
                try:
                    status["result"] = task.result()
                except Exception as e:
                    status["error"] = str(e)
            statuses.append(status)
        
        # Clean up completed tasks
        self._tasks = [t for t in self._tasks if not t.done()]
        
        return statuses
    
    async def cancel_all(self) -> int:
        """Cancel all running background tasks. Returns count of cancelled tasks."""
        cancelled = 0
        for task in self._tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
        self._tasks.clear()
        logger.info(f"[ BACKGROUND ] : Cancelled {cancelled} background tasks")
        return cancelled
    
    @property
    def active_count(self) -> int:
        """Return count of active (not done) background tasks."""
        return sum(1 for t in self._tasks if not t.done())
