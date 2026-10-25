"""
Model Router - Category-Based Model Switching
Routes requests to appropriate models based on tool categories and intent.
"""

import os
import importlib
from typing import Dict, List, Optional, Any
import logging

from models.base_model import BaseModel
from src.shared.exceptions import ModelError
from src.shared.logger import setup_logger

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.infrastructure.tools import ToolRegistry

logger = setup_logger("ModelRouter")

# Canonical turn category → legacy cloud config key (models.chat preferred; conversation alias).
_CATEGORY_CONFIG_ALIASES = {"chat": ("chat", "conversation")}


class ModelRouter:
    """
    Routes requests to appropriate models based on category.
    Enables using different models for different types of tasks.
    """
    
    def __init__(self, config: Dict[str, Any], tool_registry: "ToolRegistry", memory_engine: Optional[Any] = None):
        """
        Initialize model router with configuration.
        """
        self.config = config
        self.tool_registry = tool_registry
        self.memory_engine = memory_engine
        # Lazy-load models (Category -> Instance)
        self.models: Dict[str, BaseModel] = {}
        self.model_configs = config.get('models', {})
        
        # Trigger predictive warmup for local models on boot
        self._trigger_warmup()

    def _config_keys_for_category(self, category: str) -> List[str]:
        """Resolve config keys to try for a category (supports chat ↔ conversation alias)."""
        keys = _CATEGORY_CONFIG_ALIASES.get(category, (category,))
        return list(keys)

    def reload_models(self) -> None:
        """
        Reload all models with current configuration.
        """
        # Refresh configuration from ConfigManager to avoid stale state
        from src.shared.config_manager import ConfigManager
        self.config = ConfigManager.get_config()
        self.model_configs = self.config.get('models', {})
        
        # Clear models cache (new instances will be created on demand)
        self.models = {}
        logger.info("[ ROUTER ] Models cache cleared and configuration refreshed.")
        
        # Trigger predictive warmup for local models
        self._trigger_warmup()

    def _trigger_warmup(self):
        """
        Predictive loading for local models (LM Studio).
        Fired when model configuration changes to eliminate first-message lag.
        """
        from src.shared.config_manager import ConfigManager
        if not ConfigManager.is_local_model_active():
            return

        local_config = ConfigManager.get_local_model_config()
        provider = local_config.get('provider')
        model_name = local_config.get('name')
        base_url = local_config.get('base_url')

        if provider == 'lmstudio' and model_name:
            import asyncio
            # Check if we are in Uplink mode (Limb). If so, the Brain (Server) handles warmup.
            if os.getenv("UPLINK") == "1" or os.getenv("CHAD_UPLINK", "").lower() == "true":
                logger.debug(f"[ WARMUP ] Skipping predictive load in Limb mode (Server handles it).")
                return

            # Fire and forget warmup — routed through the shared coordinator (model_lifecycle)
            # so it doesn't race the tool selector / intent router / other lanes that may be
            # deciding to load or evict a model at the same moment.
            logger.info(f"[ WARMUP ] Triggering predictive load for LM Studio: {model_name}")
            asyncio.create_task(self._warmup_lmstudio(model_name, base_url))

    async def _warmup_lmstudio(self, model_name: str, base_url: str):
        """Predictive load via the shared model-lifecycle coordinator (not a private HTTP call).

        Uses the same load timeout as every other residency path. A short timeout here
        (previously 5s) made httpx disconnect mid-``/api/v1/models/load``, which LM Studio
        surfaces as ``Client disconnected`` / ``Engine protocol startup was aborted`` —
        especially when warmup races the tool-selector JIT load at turn start.
        """
        try:
            from src.core.intelligence.model_lifecycle import (
                DEFAULT_LOAD_TIMEOUT,
                OWNER_MAIN_MODEL,
                ensure_resident,
            )
            ok = await ensure_resident(
                base_url, model_name, owner=OWNER_MAIN_MODEL, timeout=DEFAULT_LOAD_TIMEOUT
            )
            if ok:
                logger.warning(f"[ WARMUP ] LM Studio model '{model_name}' load triggered successfully.")
            else:
                logger.debug(f"[ WARMUP ] LM Studio load for '{model_name}' did not complete within warmup timeout.")
        except Exception as e:
            logger.debug(f"[ WARMUP ] Predictive load failed (likely LM Studio not running): {e}")
    
    def is_model_cached(self, category: str = 'local') -> bool:
        """Check if a model for the given category is already instantiated."""
        from src.shared.config_manager import ConfigManager
        if ConfigManager.is_local_model_active():
            return self._local_cache_key(category) in self.models
        return category in self.models

    @staticmethod
    def _local_cache_key(category: Optional[str]) -> str:
        return f"local:{category or 'agent'}"

    def _ensure_local_model(self, category: str = "agent") -> BaseModel:
        """Ensure the local model instance exists for this category and return it."""
        cache_key = self._local_cache_key(category)
        if cache_key in self.models:
            return self.models[cache_key]
            
        from src.shared.config_manager import ConfigManager
        from models.litellm_model import LiteLLMModel
        
        # Build config from preferences (per-category when configured)
        active_model = ConfigManager.get_local_model_config(category)
        logger.info(f"[ ROUTER ] Initializing selected model for '{category}' (credentials omitted)")
        
        if not active_model:
            # Fallback to defaults
            active_model = {
                'name': ConfigManager.get_active_model(),
                'provider': 'lmstudio',
                'base_url': ConfigManager.get("local_models.providers.lmstudio.base_url"),
                'api_key': ConfigManager.get("local_models.providers.lmstudio.api_key")
            }
            logger.info("[ ROUTER ] Falling back to default endpoint configuration (credentials omitted)")
            
        # Use model name from config. LiteLLMAdapter will handle provider prefixing.
        model_name = active_model.get('name')
        base_url = active_model.get('base_url', 'http://localhost:1234/v1')
        logger.info(f"[ ROUTER ] Target local model name: {model_name}")

        from config.model_registry import get_profile
        _profile = get_profile(model_name)
        if _profile:
            logger.info(f"[ ROUTER ] Registry profile matched for '{model_name}'")

        # ── LM Studio Validation ──────────────────────────────────────────────
        # Verify the model is resident in LM Studio before proceeding. Historically this
        # only checked and hard-raised if absent — but another lane (tool selector, intent
        # router, subagent swap) evicting the main model a moment earlier would then abort
        # the whole turn instead of just reloading it. Now it attempts a load through the
        # shared coordinator first (respecting the lock/VRAM-budget/refcount it tracks for
        # every other lane) and only raises if that load itself fails — a mismatched model
        # name still surfaces the same clear error, it just no longer false-positives on
        # ordinary VRAM churn from other lanes.
        if active_model.get('provider') == 'lmstudio':
            import urllib.request as _req
            import json as _json
            from src.core.intelligence.model_lifecycle import ensure_resident_sync, current_priority, OWNER_MAIN_MODEL

            def _list_loaded() -> List[str]:
                with _req.urlopen(f"{base_url}/models", timeout=3.0) as resp:
                    data = _json.loads(resp.read().decode())
                    return [m.get('id') for m in data.get('data', [])]

            try:
                loaded = _list_loaded()
            except Exception as e:
                raise ModelError(
                    f"[ ROUTER ] Cannot reach LM Studio at {base_url}. "
                    f"Is LM Studio running with the local server enabled? Error: {e}"
                )

            if model_name not in loaded:
                logger.warning(
                    f"[ ROUTER ] LM Studio model '{model_name}' not resident (loaded: {loaded}) — "
                    f"attempting to load it before failing the turn."
                )
                # Without an explicit context_length, LM Studio JIT-loads with whatever was
                # last saved in its own UI for this model -- which is how a real turn hit a
                # hard "Context size has been exceeded" LiteLLM error while loaded at ~25k
                # tokens (2026-07-10). Let active_model.context_length (preferences.yaml
                # local_models.active_model.context_length) override that, same pattern as
                # config.yaml's llm_selector_context_length for the tool-selector model.
                context_length = active_model.get('context_length')
                if not ensure_resident_sync(base_url, model_name, owner=OWNER_MAIN_MODEL,
                                            priority=current_priority(), context_length=context_length):
                    raise ModelError(
                        f"[ ROUTER ] LM Studio model '{model_name}' could not be loaded. "
                        f"Load the correct model in LM Studio first, or update "
                        f"local_models.active_model in preferences.yaml."
                    )
                logger.info(f"[ ROUTER ] LM Studio '{model_name}' loaded successfully after recovery.")
            else:
                logger.info(f"[ ROUTER ] LM Studio verified: '{model_name}' is loaded and ready.")
        # ─────────────────────────────────────────────────────────────────────

        model = LiteLLMModel({
            'type': 'litellm',
            'model': model_name,
            'base_url': base_url,
            'api_key': active_model.get('api_key'),
            'supports_tools': _profile.supports_native_tool_calls if _profile else active_model.get('supports_tools', False),
            'temperature': _profile.temperature if _profile else 0.7,
            'profile': _profile,
            'system_instruction': ConfigManager.get_persona_instruction()
        })
        
        self.models[cache_key] = model
        return model

    
    def _ensure_model(self, category: str) -> Optional[BaseModel]:
        """
        Ensure a model instance exists for the given category.
        Loads the model if it hasn't been instantiated yet.
        """
        # 1. Local Model Override (Global Bias)
        from src.shared.config_manager import ConfigManager
        if ConfigManager.is_local_model_active():
            logger.info(f"[ ROUTER ] Local model override active for category '{category}'")
            return self._ensure_local_model(category or "agent")
            
        model_config = None
        resolved_category = category
        for key in self._config_keys_for_category(category):
            model_config = self.model_configs.get(key)
            if model_config:
                resolved_category = key
                break
        if not model_config:
            return None

        # 2. Global Cloud Override
        if not ConfigManager.is_local_model_active() and category in ['agent', 'chat', 'conversation', 'text']:
            global_active_model = ConfigManager.get_active_model()
            # If the category's config is different from the global active model, override it
            if global_active_model and global_active_model != model_config.get('model'):
                logger.info(f"[ ROUTER ] Cloud model override: {global_active_model} for category '{category}'")
                model_config = model_config.copy()
                model_config['model'] = global_active_model
                # Force litellm_model type for cloud overrides to ensure provider compatibility
                model_config['type'] = 'litellm_model'
        
        if category in self.models:
            # Check if cached model matches current config (in case reload_models wasn't called)
            cached_model = self.models[category]
            if hasattr(cached_model, 'model_name') and cached_model.model_name == model_config.get('model'):
                return cached_model
            else:
                # Mismatch detected, invalidate cache for this category
                del self.models[category]
            
        try:
            model_type = model_config.get('type')
            if not model_type:
                logger.error(f"No model type specified for category '{resolved_category}'")
                return None
            
            # Skip specialized engines handled separately (unless local is active)
            if category in ['router', 'thinking']:
                logger.debug(f"Skipping model instance creation for specialized category '{category}'")
                return None
            
            # Dynamically import and create the model class
            model_instance = self._create_model_instance(model_type, model_config)
            
            # --- DEDUPLICATION (State Persistence) ---
            # If an identical instance already exists in another category, REUSE it.
            # This prevents LangGraph from re-initializing and wiping MemorySaver history.
            for existing in self.models.values():
                if (hasattr(existing, 'model_name') and existing.model_name == model_instance.model_name and 
                    type(existing) == type(model_instance)):
                    logger.debug(f"[ ROUTER ] Reusing existing instance for '{category}' (matches {existing.model_name})")
                    self.models[category] = existing
                    return existing

            self.models[category] = model_instance
            return model_instance

        except Exception as e:
            logger.error(f"Failed to load model for category '{category}': {e}", exc_info=True)
            return None

    
    def _create_model_instance(self, model_type: str, config: Dict[str, Any]) -> BaseModel:
        """
        Create a model instance from type string.
        
        Args:
            model_type: Type of model (e.g., 'gemini_live', 'gemini_text')
            config: Model configuration
            
        Returns:
            Instantiated model
        """
        try:
            # Special handling for litellm shorthand
            if model_type == 'litellm':
                model_type = 'litellm_model'

            # Import the model module (e.g., models.gemini_live)
            module_path = f"models.{model_type}"
            module = importlib.import_module(module_path)
            
            # Get the model class (assumes class name is CamelCase version of module name)
            # e.g., gemini_live -> GeminiLive
            if model_type == 'litellm_model':
                class_name = 'LiteLLMModel'
            else:
                class_name = ''.join(word.capitalize() for word in model_type.split('_'))
            model_class = getattr(module, class_name)
            
            # Merge root-level config (like voice_name) into model config
            # This ensures voice_name and other root-level settings are available to the model
            merged_config = config.copy()
            if 'voice_name' in self.config:
                merged_config['voice_name'] = self.config['voice_name']
            if 'system_instruction' in self.config:
                merged_config['system_instruction'] = self.config['system_instruction']
            
            # Inject memory manager if available
            if self.memory_engine:
                merged_config['memory_manager'] = self.memory_engine
            
            # Instantiate and return
            return model_class(merged_config)
            
        except Exception as e:
            raise ModelError(f"Failed to create model instance '{model_type}': {e}")
    
    def route(self, category: Optional[str] = None) -> BaseModel:
        """
        Determine which model to use based on category.
        
        Args:
            category: Explicit category (web, system, custom, autonomous)
            
        Returns:
            Model instance to use
            
        Raises:
            ModelError: If no suitable model is found
        """
        # If explicit category provided, use it
        if category:
            from src.shared.config_manager import ConfigManager
            is_local = ConfigManager.is_local_model_active()
            logger.info(f"[ ROUTER ] Routing category '{category}'. Local active: {is_local}")
            
            if is_local:
                logger.info(f"[ ROUTER ] Local model override active for category '{category}'")
                return self._ensure_local_model(category or "agent")
                
            model = self._ensure_model(category)
            if model:
                logger.debug(f"[ ROUTER ] Found model for category '{category}': {model.model_name}")
                return model
            else:
                logger.warning(f"[ ROUTER ] No model found for category '{category}', falling back to chat")
        
        # Default to chat model (legacy config key: conversation)
        default_model = self._ensure_model('chat')
        if default_model:
            return default_model
        
        # If chat model doesn't exist, use first available
        if self.models:
            fallback = next(iter(self.models.values()))
            logger.warning("No chat model, using fallback")
            return fallback
        
        raise ModelError("No models loaded in router")
    
    
    def get_model_for_category(self, category: str) -> Optional[BaseModel]:
        """
        Get the model assigned to a specific category.
        
        Args:
            category: Category name
            
        Returns:
            Model instance or None
        """
        return self._ensure_model(category)
    
    def get_all_models(self) -> Dict[str, BaseModel]:
        """
        Get all loaded models.
        
        Returns:
            Dictionary of category -> model
        """
        return self.models.copy()
    
    def get_tool_schemas_for_category(self, category: str) -> List[Dict]:
        """
        Get tool schemas for a specific category.
        
        Args:
            category: Category name
            
        Returns:
            List of tool schemas
        """
        return self.tool_registry.get_schemas_for_categories([category])
    
    async def cleanup(self) -> None:
        """Clean up all model resources."""

        for category, model in self.models.items():
            try:
                await model.cleanup()

            except Exception as e:
                logger.error(f"Error cleaning up model '{category}': {e}")
    
    def __repr__(self) -> str:
        categories = ', '.join(self.models.keys())
        return f"<ModelRouter(categories=[{categories}])>"

