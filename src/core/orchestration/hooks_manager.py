"""
Hooks Manager - Lifecycle hooks for orchestration extension points

Provides hook points throughout the conversation lifecycle for custom behaviors
without modifying core logic.

Hook Types:
    - before_turn_start: Before turn begins (can modify context)
    - after_turn_start: After turn initialized
    - before_model_resolve: Before model selection (can override model)
    - after_model_resolve: After model selected
    - before_prompt_build: Before prompt construction (can inject context)
    - after_prompt_build: After prompt built (can modify prompt)
    - before_tool_execute: Before tool execution (can block/modify)
    - after_tool_execute: After tool completes
    - before_turn_complete: Before turn finalization
    - after_turn_complete: After turn finalized
    - on_error: When error occurs

Use Cases:
    - Profile-specific model routing
    - Time-based tool restrictions
    - Context injection (weather, location, time)
    - Audit logging
    - Custom analytics
    - Policy enforcement

Example:
    def before_model_hook(context: HookContext) -> HookContext:
        if context.metadata.get('time_of_day') == 'night':
            context.metadata['preferred_model'] = 'gemini-2.5-flash'
        return context

    hooks_manager.register('before_model_resolve', before_model_hook)

OpenClaw Pattern Source:
    - Lifecycle hooks from OpenClaw gateway architecture
    - Extension points without core modifications
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.shared.logger import setup_logger

logger = setup_logger("HooksManager")


class HookType(Enum):
    """Types of lifecycle hooks."""
    # Turn lifecycle
    BEFORE_TURN_START = "before_turn_start"
    AFTER_TURN_START = "after_turn_start"
    BEFORE_TURN_COMPLETE = "before_turn_complete"
    AFTER_TURN_COMPLETE = "after_turn_complete"

    # Model lifecycle
    BEFORE_MODEL_RESOLVE = "before_model_resolve"
    AFTER_MODEL_RESOLVE = "after_model_resolve"

    # Prompt lifecycle
    BEFORE_PROMPT_BUILD = "before_prompt_build"
    AFTER_PROMPT_BUILD = "after_prompt_build"

    # Tool lifecycle
    BEFORE_TOOL_EXECUTE = "before_tool_execute"
    AFTER_TOOL_EXECUTE = "after_tool_execute"

    # Error handling
    ON_ERROR = "on_error"


@dataclass
class HookContext:
    """
    Context passed to hook handlers.

    Attributes:
        hook_type: Type of hook being triggered
        turn_id: Current turn identifier
        initiator: Who initiated the turn (user, system, skill)
        session_key: Session key for isolation
        category: Turn category (chat, web, system)
        user_input: User's input text (if available)
        model_name: Selected model name (if resolved)
        tool_name: Tool being executed (if applicable)
        metadata: Additional context data
        timestamp: Hook trigger timestamp
    """
    hook_type: HookType
    turn_id: str
    initiator: str = "user"
    session_key: str = "default"
    category: str = "chat"
    user_input: Optional[str] = None
    model_name: Optional[str] = None
    tool_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'hook_type': self.hook_type.value,
            'turn_id': self.turn_id,
            'initiator': self.initiator,
            'session_key': self.session_key,
            'category': self.category,
            'user_input': self.user_input,
            'model_name': self.model_name,
            'tool_name': self.tool_name,
            'metadata': self.metadata,
            'timestamp': self.timestamp.isoformat()
        }


class HooksManager:
    """
    Manages lifecycle hooks for orchestration extension points.

    Features:
    - Hook registration with priority support
    - Sync and async hook handlers
    - Context modification (hooks can update context)
    - Error handling (failed hooks don't break execution)
    - Conditional hooks (can be enabled/disabled)
    """

    def __init__(self):
        """Initialize hooks manager."""
        # Hook storage: {hook_type: [(priority, handler, enabled)]}
        self._hooks: Dict[HookType, List[tuple]] = {hook_type: [] for hook_type in HookType}
        self._enabled = True

        logger.debug("HooksManager initialized")

    def register(
        self,
        hook_type: Union[str, HookType],
        handler: Callable,
        priority: int = 50,
        enabled: bool = True
    ) -> None:
        """
        Register a hook handler.

        Args:
            hook_type: Type of hook (string or HookType enum)
            handler: Callable that accepts HookContext and returns HookContext
            priority: Execution priority (lower = earlier, default: 50)
            enabled: Whether hook is initially enabled

        Example:
            def my_hook(context: HookContext) -> HookContext:
                context.metadata['custom_data'] = 'value'
                return context

            manager.register('before_turn_start', my_hook, priority=10)
        """
        # Convert string to enum if needed
        if isinstance(hook_type, str):
            try:
                hook_type = HookType(hook_type)
            except ValueError:
                logger.error(f"Invalid hook type: {hook_type}")
                return

        # Add to hooks list
        self._hooks[hook_type].append((priority, handler, enabled))

        # Sort by priority (lower priority = earlier execution)
        self._hooks[hook_type].sort(key=lambda x: x[0])

        logger.debug(f"Registered hook: {hook_type.value} (priority: {priority})")

    def unregister(self, hook_type: Union[str, HookType], handler: Callable) -> bool:
        """
        Unregister a hook handler.

        Args:
            hook_type: Type of hook
            handler: Handler to remove

        Returns:
            True if handler was found and removed
        """
        # Convert string to enum if needed
        if isinstance(hook_type, str):
            try:
                hook_type = HookType(hook_type)
            except ValueError:
                return False

        # Find and remove handler
        original_count = len(self._hooks[hook_type])
        self._hooks[hook_type] = [
            (p, h, e) for p, h, e in self._hooks[hook_type] if h != handler
        ]

        removed = len(self._hooks[hook_type]) < original_count
        if removed:
            logger.debug(f"Unregistered hook: {hook_type.value}")

        return removed

    async def trigger(
        self,
        hook_type: Union[str, HookType],
        context: HookContext
    ) -> HookContext:
        """
        Trigger all hooks of a specific type.

        Hooks are executed in priority order and can modify the context.
        Failed hooks are logged but don't stop execution.

        Args:
            hook_type: Type of hook to trigger
            context: HookContext to pass to handlers

        Returns:
            Modified HookContext (hooks can update context)

        Example:
            context = HookContext(
                hook_type=HookType.BEFORE_TURN_START,
                turn_id='123',
                category='chat'
            )
            context = await hooks_manager.trigger('before_turn_start', context)
        """
        if not self._enabled:
            return context

        # Convert string to enum if needed
        if isinstance(hook_type, str):
            try:
                hook_type = HookType(hook_type)
            except ValueError:
                logger.error(f"Invalid hook type: {hook_type}")
                return context

        # Get hooks for this type
        hooks = self._hooks.get(hook_type, [])
        if not hooks:
            return context

        logger.debug(f"Triggering {len(hooks)} hooks for: {hook_type.value}")

        # Execute hooks in priority order
        for priority, handler, enabled in hooks:
            if not enabled:
                continue

            try:
                # Call handler (support both sync and async)
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(context)
                else:
                    result = handler(context)

                # Update context if handler returns modified context
                if result is not None and isinstance(result, HookContext):
                    context = result

            except Exception as e:
                logger.debug(
                    "Failed hook handler execution",
                    exc_info=True
                )
                # Continue with other hooks

        return context

    def enable_hook(self, hook_type: Union[str, HookType], handler: Callable) -> bool:
        """
        Enable a specific hook handler.

        Args:
            hook_type: Type of hook
            handler: Handler to enable

        Returns:
            True if handler was found and enabled
        """
        if isinstance(hook_type, str):
            try:
                hook_type = HookType(hook_type)
            except ValueError:
                return False

        # Find and enable handler
        for i, (priority, h, enabled) in enumerate(self._hooks[hook_type]):
            if h == handler:
                self._hooks[hook_type][i] = (priority, h, True)
                logger.info(f"Enabled hook: {hook_type.value}")
                return True

        return False

    def disable_hook(self, hook_type: Union[str, HookType], handler: Callable) -> bool:
        """
        Disable a specific hook handler without removing it.

        Args:
            hook_type: Type of hook
            handler: Handler to disable

        Returns:
            True if handler was found and disabled
        """
        if isinstance(hook_type, str):
            try:
                hook_type = HookType(hook_type)
            except ValueError:
                return False

        # Find and disable handler
        for i, (priority, h, enabled) in enumerate(self._hooks[hook_type]):
            if h == handler:
                self._hooks[hook_type][i] = (priority, h, False)
                logger.info(f"Disabled hook: {hook_type.value}")
                return True

        return False

    def list_hooks(self, hook_type: Optional[Union[str, HookType]] = None) -> Dict[str, List[Dict]]:
        """
        List registered hooks.

        Args:
            hook_type: Optional type to filter by (returns all if None)

        Returns:
            Dictionary of hook_type -> list of hook info
        """
        result = {}

        # Filter by type if specified
        if hook_type:
            if isinstance(hook_type, str):
                try:
                    hook_type = HookType(hook_type)
                except ValueError:
                    return {}
            hook_types = [hook_type]
        else:
            hook_types = list(HookType)

        # Build result
        for ht in hook_types:
            hooks = self._hooks.get(ht, [])
            result[ht.value] = [
                {
                    'priority': priority,
                    'handler': handler.__name__,
                    'enabled': enabled
                }
                for priority, handler, enabled in hooks
            ]

        return result

    def clear_hooks(self, hook_type: Optional[Union[str, HookType]] = None) -> None:
        """
        Clear all hooks of a specific type, or all hooks if type not specified.

        Args:
            hook_type: Optional type to clear (clears all if None)
        """
        if hook_type:
            if isinstance(hook_type, str):
                try:
                    hook_type = HookType(hook_type)
                except ValueError:
                    return
            self._hooks[hook_type] = []
            logger.info(f"Cleared hooks for: {hook_type.value}")
        else:
            for ht in HookType:
                self._hooks[ht] = []
            logger.info("Cleared all hooks")

    def enable_all(self) -> None:
        """Enable the entire hooks system."""
        self._enabled = True
        logger.info("Hooks system enabled")

    def disable_all(self) -> None:
        """Disable the entire hooks system (no hooks will execute)."""
        self._enabled = False
        logger.warning("Hooks system disabled")

    def is_enabled(self) -> bool:
        """Check if hooks system is enabled."""
        return self._enabled

    def get_hook_count(self) -> int:
        """Get total number of registered hooks."""
        return sum(len(hooks) for hooks in self._hooks.values())


# Global singleton instance
_hooks_manager: Optional[HooksManager] = None


def get_hooks_manager() -> HooksManager:
    """
    Get the global HooksManager instance.

    Returns:
        Singleton HooksManager instance
    """
    global _hooks_manager

    if _hooks_manager is None:
        _hooks_manager = HooksManager()

    return _hooks_manager
