"""
Model Coordinator - Single Responsibility for Model Management
Extracted from SessionManager to enable clean model routing and lifecycle management.

This coordinator handles:
- Model selection based on category/intent
- Model lifecycle management (start/close sessions)
- Tool schema retrieval and management
- Model state tracking
- Future: Multi-model support (Claude, Gemini 2.5, GPT-4)
"""

import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from src.shared.logger import setup_logger
from src.shared.exceptions import ModelError
from src.core.intelligence.model_router import ModelRouter
from src.core.intelligence.model_capability_registry import get_model_capability_registry
from models.base_model import BaseModel
from src.application.events import EventBus
from src.core.orchestration.hooks_manager import get_hooks_manager, HookContext, HookType
from src.core.intelligence.provider_failover import get_failover_manager

logger = setup_logger("ModelCoordinator")


@dataclass
class ModelSession:
    """Represents an active model session"""
    model: BaseModel
    category: str
    is_persistent: bool = False
    session_active: bool = False


class ModelCoordinator:
    """
    Coordinates all model operations for the assistant.

    Design principles:
    - Single responsibility: Model management only
    - Strategy-based: Uses ModelRouter for model selection
    - Lifecycle management: Handles session start/stop
    - Scalable: Designed for multi-model orchestration
    """

    def __init__(
        self,
        model_router: ModelRouter,
        event_bus: EventBus,
        consolidation_manager: Optional[Any] = None
    ):
        """
        Initialize ModelCoordinator.

        Args:
            model_router: Router for model selection
            event_bus: Event bus for publishing model events
            consolidation_manager: Optional context manager for persistent sessions
        """
        self.router = model_router
        self.event_bus = event_bus
        self.consolidation_manager = consolidation_manager
        self._current_turn_is_simple = False

        # Active model tracking
        self.active_model: Optional[BaseModel] = None
        self.current_category: str = 'chat'
        self.active_session: Optional[ModelSession] = None

        # Hooks manager (Phase 2 - OpenClaw pattern)
        self._hooks_manager = get_hooks_manager()

        # Provider failover manager (Phase 2 - OpenClaw pattern)
        self._failover_manager = get_failover_manager()
        self._current_provider: Optional[str] = None

        logger.debug("ModelCoordinator initialized")

        # Subscribe to ModelChanged events to reload router
        if self.event_bus:
            from src.application.events import ModelChanged
            self.event_bus.subscribe(ModelChanged, self._on_model_changed)

    async def select_model(self, category: str = 'chat') -> Optional[BaseModel]:
        """
        Select appropriate model based on category.

        Args:
            category: Explicit category (chat, web, system, etc.)

        Returns:
            Selected model instance or None if no suitable model found
        """
        if not self.router:
            logger.error("[ MODEL_COORDINATOR ] No router available for model selection")
            return None

        # Trigger before_model_resolve hook (Phase 2 - OpenClaw pattern)
        try:
            hook_context = HookContext(
                hook_type=HookType.BEFORE_MODEL_RESOLVE,
                turn_id="model_selection",
                category=category or 'chat',
                metadata={'requested_category': category}
            )
            hook_context = await self._hooks_manager.trigger(HookType.BEFORE_MODEL_RESOLVE, hook_context)

            # Hooks can override category
            category = hook_context.category
            if 'preferred_model' in hook_context.metadata:
                # Hook requested specific model - try to honor it
                preferred = hook_context.metadata['preferred_model']
                logger.info(f"[ MODEL_COORDINATOR ] Hook requested preferred model: {preferred}")
        except Exception as e:
            logger.error(f"before_model_resolve hook failed: {e}", exc_info=True)

        try:
            model = self.router.route(category=category)

            if model:
                self.active_model = model
                self.current_category = category or 'chat'

                # Update capability registry and auto-select compaction profile
                model_id = model.model_id if hasattr(model, 'model_id') else None
                cap_registry = get_model_capability_registry()
                cap_registry.set_active_model(model_id)
                if self.consolidation_manager and model_id:
                    derived_profile = cap_registry.get_active_context_budget_profile()
                    if derived_profile:
                        self.consolidation_manager.set_profile(derived_profile)

                # Trigger after_model_resolve hook (Phase 2 - OpenClaw pattern)
                try:
                    hook_context = HookContext(
                        hook_type=HookType.AFTER_MODEL_RESOLVE,
                        turn_id="model_selection",
                        category=self.current_category,
                        model_name=model_id or 'unknown'
                    )
                    await self._hooks_manager.trigger(HookType.AFTER_MODEL_RESOLVE, hook_context)
                except Exception as e:
                    logger.error(f"after_model_resolve hook failed: {e}", exc_info=True)

                logger.info(f"[ MODEL_COORDINATOR ] Selected model for category: {self.current_category}")
                return model
            else:
                logger.warning(f"[ MODEL_COORDINATOR ] No model found for category: {category}")
                return None

        except ModelError as me:
            logger.error(f"[ MODEL_COORDINATOR ] Model selection failed with ModelError: {me}")
            raise me
        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Model selection failed: {e}", exc_info=True)
            return None


    def get_tool_schemas(
        self,
        tool_registry,
        category: Optional[str] = None,
        user_input: Optional[str] = None,
        history: Optional[List[Any]] = None
    ) -> List[Dict]:
        """
        Retrieve tool schemas for model use.
        
        NOTE: [ ARCHITECTURAL DUALITY ]
        This is the "Headless Brain" (Flight Controller) path. Changes here 
        likely need mirroring in TextModeClient._get_optimized_tools() 
        (the "Autopilot" path).
        
        Retrieve tool schemas for the current model.
        Supports category-based filtering, smart turn-based selection, or full discovery.

        Args:
            tool_registry: Tool registry to fetch schemas from
            category: Specific category to get tools for (e.g. 'web', 'system')
            user_input: Optional user prompt to enable smart tool selection
            history: Optional message history for contextual search
        """
        try:
            # 1. Smart Selection (If user_input provided and we are in autonomous/full mode)
            if user_input and (not category or category in ['autonomous', 'agent']):
                if hasattr(tool_registry, 'get_schemas_for_turn'):
                    result = tool_registry.get_schemas_for_turn(user_input, history=history)
                    tool_schemas = result.get("schemas", [])
                    self._current_turn_is_simple = result.get("is_simple", False)
                    logger.warning(f"[ MODEL_COORDINATOR ] Using smart selection: {len(tool_schemas)} tools (is_simple={self._current_turn_is_simple})")
                    return tool_schemas

            # 2. Category-based filtering (legacy / explicit)
            if category and category not in ['autonomous', 'agent']:
                if self.router:
                    tool_schemas = self.router.get_tool_schemas_for_category(category)
                    logger.info(f"[ MODEL_COORDINATOR ] Loaded {len(tool_schemas)} tools for category: {category}")
                    return tool_schemas

            # 3. Fallback: Full discovery
            if tool_registry:
                tool_schemas = tool_registry.get_all_schemas()
                logger.info(f"[ MODEL_COORDINATOR ] Loaded all {len(tool_schemas)} tools (Full Access)")
                return tool_schemas
            
            return []

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Failed to retrieve tool schemas: {e}", exc_info=True)
            return []


    async def start_persistent_session(self, tool_schemas: List[Dict]) -> bool:
        """
        Start a persistent session with the active model.

        Args:
            tool_schemas: Tool schemas to provide to the model

        Returns:
            True if session started successfully, False otherwise
        """
        if not self.active_model:
            logger.warning("[ MODEL_COORDINATOR ] No active model for persistent session")
            return False

        if not hasattr(self.active_model, 'start_persistent_session'):
            logger.debug("[ MODEL_COORDINATOR ] Active model does not support persistent sessions")
            return False

        try:
            # Inject consolidation manager if available
            if self.consolidation_manager and hasattr(self.active_model, 'set_consolidation_manager'):
                self.active_model.set_consolidation_manager(self.consolidation_manager)

            success = await self.active_model.start_persistent_session(tool_schemas)

            if success:
                self.active_session = ModelSession(
                    model=self.active_model,
                    category=self.current_category,
                    is_persistent=True,
                    session_active=True
                )
                logger.info("[ MODEL_COORDINATOR ] Persistent session started successfully")
                return True
            else:
                logger.warning("[ MODEL_COORDINATOR ] Persistent session start returned False")
                return False

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Failed to start persistent session: {e}", exc_info=True)
            return False

    async def close_persistent_session(self) -> None:
        """Close the active persistent session if one exists."""
        if not self.active_model:
            return

        if not hasattr(self.active_model, 'close_persistent_session'):
            return

        try:
            await self.active_model.close_persistent_session()

            if self.active_session:
                self.active_session.session_active = False

            logger.info("[ MODEL_COORDINATOR ] Persistent session closed")

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Failed to close persistent session: {e}", exc_info=True)

    def pause_streaming(self) -> None:
        """Pause audio streaming (close audio gate, keep WebSocket open)."""
        if not self.active_model:
            return

        if not hasattr(self.active_model, 'pause_streaming'):
            logger.debug("[ MODEL_COORDINATOR ] Active model does not support pause_streaming")
            return

        try:
            self.active_model.pause_streaming()
            logger.debug("[ MODEL_COORDINATOR ] Streaming paused (audio gate closed)")

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Failed to pause streaming: {e}", exc_info=True)

    def reset_generation_flag(self) -> None:
        """Reset model's generation complete flag for new turn."""
        if not self.active_model:
            return

        if hasattr(self.active_model, 'reset_generation_flag'):
            try:
                self.active_model.reset_generation_flag()
                logger.debug("[ MODEL_COORDINATOR ] Generation flag reset")
            except Exception as e:
                logger.error(f"[ MODEL_COORDINATOR ] Failed to reset generation flag: {e}")

    def set_expecting_followup(self, expecting: bool) -> None:
        """Set whether the model should expect a followup from the user."""
        if not self.active_model:
            return

        if hasattr(self.active_model, 'expecting_followup'):
            try:
                self.active_model.expecting_followup = expecting
                logger.debug(f"[ MODEL_COORDINATOR ] Expecting followup set to: {expecting}")
            except Exception as e:
                logger.error(f"[ MODEL_COORDINATOR ] Failed to set expecting_followup: {e}")

    def is_generation_complete(self) -> bool:
        """Check if model has completed generation."""
        if not self.active_model:
            return True

        if hasattr(self.active_model, 'is_generation_complete'):
            try:
                return self.active_model.is_generation_complete
            except Exception as e:
                logger.error(f"[ MODEL_COORDINATOR ] Failed to check generation status: {e}")
                return True

        return True  # Default to True if not supported

    def has_session(self) -> bool:
        """Check if a persistent session is active."""
        if not self.active_model:
            return False

        if not hasattr(self.active_model, 'session'):
            return False

        return self.active_model.session is not None

    async def interrupt(self) -> None:
        """Interrupt the active model's generation."""
        if not self.active_model:
            return

        try:
            await self.active_model.interrupt()
            logger.info("[ MODEL_COORDINATOR ] Model interrupted")

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Failed to interrupt model: {e}", exc_info=True)

    def get_model(self) -> Optional[BaseModel]:
        """Get the currently active model instance."""
        return self.active_model

    def get_category(self) -> str:
        """Get the current category."""
        return self.current_category

    async def cleanup(self) -> None:
        """Clean up all model resources."""
        try:
            # Close persistent session if active
            await self.close_persistent_session()

            # Clear active model reference
            self.active_model = None
            self.active_session = None

            logger.info("[ MODEL_COORDINATOR ] Cleanup complete")

        except Exception as e:
            logger.error(f"[ MODEL_COORDINATOR ] Error during cleanup: {e}", exc_info=True)

    # =========================================================================
    # PROVIDER FAILOVER (Phase 2 - OpenClaw Pattern)
    # =========================================================================

    def mark_provider_failure(self, error: Exception, provider: Optional[str] = None) -> None:
        """
        Mark current provider as failed and apply cooldown.

        Args:
            error: Exception that occurred
            provider: Provider name (defaults to current provider)
        """
        provider = provider or self._current_provider
        if not provider:
            logger.debug("No provider to mark as failed")
            return

        self._failover_manager.mark_failure(provider, error)
        logger.warning(f"Provider {provider} marked as failed: {str(error)[:100]}")

    def mark_provider_success(self, provider: Optional[str] = None) -> None:
        """
        Mark current provider as successful.

        Args:
            provider: Provider name (defaults to current provider)
        """
        provider = provider or self._current_provider
        if not provider:
            return

        self._failover_manager.mark_success(provider)
        logger.debug(f"Provider {provider} marked as successful")

    async def try_failover(
        self,
        category: Optional[str] = None,
        error: Optional[Exception] = None
    ) -> Optional[BaseModel]:
        """
        Attempt to failover to next available provider.

        Args:
            category: Category for model selection
            error: Error that triggered failover (for logging)

        Returns:
            New model instance or None if no fallback available
        """
        if not self._failover_manager.is_enabled():
            logger.debug("Failover disabled, not attempting")
            return None

        # Mark current provider as failed
        if error:
            self.mark_provider_failure(error)

        # Get next available provider
        fallback_profile = self._failover_manager.get_next_provider(
            current_provider=self._current_provider
        )

        if not fallback_profile:
            logger.error("No fallback provider available")
            return None

        # Try to switch to fallback provider
        logger.info(
            f"Attempting failover: {self._current_provider or 'unknown'} → {fallback_profile.provider}"
        )

        try:
            # Update current provider
            self._current_provider = fallback_profile.provider

            # Select model with new provider (using router)
            # Note: This assumes router can handle provider hints
            model = await self.select_model(category=category or self.current_category)

            if model:
                logger.info(f"Failover successful: switched to {fallback_profile.provider}")
                self.mark_provider_success(fallback_profile.provider)
                return model
            else:
                logger.error(f"Failover failed: could not initialize {fallback_profile.provider}")
                return None

        except Exception as e:
            logger.error(f"Failover to {fallback_profile.provider} failed: {e}", exc_info=True)
            self.mark_provider_failure(e, fallback_profile.provider)
            return None

    def get_provider_status(self) -> List[Dict[str, Any]]:
        """
        Get status of all providers.

        Returns:
            List of provider status dictionaries
        """
        return self._failover_manager.list_providers()

    def reset_provider(self, provider: str) -> bool:
        """
        Reset provider state (clear cooldown, reset failures).

        Args:
            provider: Provider name

        Returns:
            True if reset successful
        """
        return self._failover_manager.reset_provider(provider)

    def reset_all_providers(self) -> None:
        """Reset all provider states."""
        self._failover_manager.reset_all()
        logger.info("All provider states reset")

    def enable_failover(self) -> None:
        """Enable provider failover system."""
        self._failover_manager.enable()
        logger.info("Provider failover enabled")

    def disable_failover(self) -> None:
        """Disable provider failover system."""
        self._failover_manager.disable()
        logger.warning("Provider failover disabled")

    def _on_model_changed(self, event) -> None:
        """Reload models in router when global model selection changes."""
        if self.router:
            self.router.reload_models()
            logger.warning("[ MODEL_COORDINATOR ] Model router reloaded due to ModelChanged event.")
        # Clear active model in capability registry; new model will be set on next select_model()
        get_model_capability_registry().set_active_model(None)
        if self.consolidation_manager:
            self.consolidation_manager.set_profile("balanced")
