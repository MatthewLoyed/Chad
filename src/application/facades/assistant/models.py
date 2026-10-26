from typing import List, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class ModelsFacadeMixin:
    """Mixin for model discovery and management facade methods."""

    async def initialize(self) -> None:
        """
        Initialize the facade.
        Restores the last active local model if configured in preferences.
        """
        from src.shared.config_manager import ConfigManager
        if ConfigManager.is_local_model_active():
            active_model = ConfigManager.get_local_model_config()
            if active_model:
                logger.info(f"[ FACADE ] Restoring active local model: {active_model.get('name')}")
                # USE AWAIT here to ensure boot blocks until we know if it's UP
                await self.set_local_model(active_model)
        else:
            # Cloud model is the default, ensure its adapter is ready too
            if self._text_mode_client and hasattr(self._text_mode_client, 'adapter'):
                await self._text_mode_client.adapter.wait_until_ready(timeout=5.0)

    async def is_model_ready(self) -> bool:
        """
        Check if the active model's adapter is connected and ready.
        """
        if not self._text_mode_client or not hasattr(self._text_mode_client, 'adapter'):
            return False
            
        return await self._text_mode_client.adapter.is_ready()

    async def discover_local_models(self) -> List[Dict[str, Any]]:
        """
        Discover local AI models from LM Studio / Ollama.

        Returns a list of model descriptors for the GUI dropdown. Each dict has:
          - name: str (model ID)
          - display_name: str
          - provider: str ("lmstudio" | "ollama")
          - supports_tools: bool
          - size_hint: str (e.g. "32B")
          - base_url: str
          - api_key: str

        Returns empty list if no local server is reachable.
        """
        try:
            from src.core.intelligence.local_model_discovery import get_local_model_discovery
            discovery = get_local_model_discovery()
            # Populating the UI must be a cheap GET /v1/models — never probe-load a model
            # into LM Studio. Tool-call capability comes from the on-disk cache / name
            # heuristics only; verify_tools=True would fire a real chat completion per
            # unverified model, which JIT-loads it.
            models = await discovery.discover_all(verify_tools=False, use_cache=True)
            return [
                {
                    "name": m.name,
                    "display_name": m.display_name,
                    "provider": m.provider,
                    "supports_tools": m.supports_tools,
                    "tools_verified": m.tools_verified,
                    "size_hint": m.size_hint,
                    "base_url": m.base_url,
                    "api_key": m.api_key,
                }
                for m in models
            ]
        except Exception as e:
            logger.error(f"discover_local_models failed: {e}", exc_info=True)
            return []

    async def set_local_model(self, model_info: Dict[str, Any]) -> bool:
        """
        Switch to a local AI model.

        Updates the in-memory config, updates the TextModeClient with a local adapter,
        and persists the selection to preferences.yaml so it survives restarts.

        Args:
            model_info: Dict from discover_local_models() with name, provider, etc.

        Returns:
            True on success, False on failure.
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.infrastructure.repositories import ConfigRepository
            from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter

            model_name = model_info.get("name")
            provider = model_info.get("provider")
            base_url = model_info.get("base_url")
            api_key = model_info.get("api_key")
            supports_tools = model_info.get("supports_tools", True)
            
            from src.core.intelligence.model_lifecycle import switch_main_model
            from src.core.tools.failure import failure, UNAVAILABLE
            if not model_name or not base_url:
                return failure("Selected model has no name or provider endpoint", failure_class=UNAVAILABLE)
            previous = (ConfigManager.get_local_model_config()
                        if ConfigManager.is_local_model_active() else {})
            ready = await switch_main_model(previous, model_info)
            if not ready:
                return failure(
                    f"Could not load selected model {model_name}", failure_class=UNAVAILABLE,
                    next_actions=["Wait for any active chat to finish and check the local model server before selecting again."],
                )

            # LiteLLMAdapter handles provider prefixing automatically
            llm_model_name = model_name

            adapter = LiteLLMAdapter(
                base_url=base_url,
                api_key=api_key,
                model_name=llm_model_name,
                system_instruction=ConfigManager.get_persona_instruction()
            )

            # 2. Persist to config so orchestrator also picks it up
            config_repo = ConfigRepository()
            # Build the active_model dict
            active_model_dict = {
                "name": model_name,
                "provider": provider,
                "base_url": base_url,
                "api_key": api_key,
                "supports_tools": supports_tools,
            }
            # Routing and GUI polling prefer supervisor over active_model. Keep
            # both aliases together so an old supervisor cannot undo the picker.
            local_patch = {"use_local": True, "supervisor": active_model_dict,
                           "active_model": active_model_dict}
            prefs = config_repo.load("preferences", use_cache=False)
            ConfigManager._recursive_update(prefs, {"local_models": local_patch})
            if not config_repo.save("preferences", prefs):
                await switch_main_model(model_info, previous)
                return failure("Could not persist selected local model", failure_class=UNAVAILABLE)

            # 2. Update existing text_mode_client or create one
            if self._text_mode_client:
                # Hot-swap the adapter
                self._text_mode_client.adapter = adapter
                # We should clear history when switching models to prevent context contamination
                self._text_mode_client.clear_history()
            else:
                from src.core.interfaces.text_mode import TextModeClient
                tmc = TextModeClient(
                    adapter=adapter,
                    tool_registry=getattr(getattr(self._assistant, 'session_manager', None), 'tools', None),
                    tool_executor=getattr(getattr(self._assistant, 'session_manager', None), 'tool_executor', None),
                    memory_engine=getattr(self._assistant, 'memory', None),
                )
                self._text_mode_client = tmc


            # 3. Also update in-memory config so _is_local_model_active() fires immediately
            ConfigManager._recursive_update(
                ConfigManager._config,
                {"local_models": local_patch},
            )

            # Persisted selection must reach the Brain even when the model needs
            # loading. Readiness is a warning, not a reason to keep the old pin.
            if self._is_uplink and self._uplink and hasattr(self._uplink, 'send_model_update'):
                await self._uplink.send_model_update({"local_models": local_patch})

            logger.info(f"[ LOCAL ] Switched to local model: {model_name} (provider: {provider})")
            
            # 5. Update shared assistant state
            if self._assistant:
                self._assistant.adapter = adapter
                if hasattr(self._assistant, 'model_router') and self._assistant.model_router:
                    self._assistant.model_router.reload_models()
            
            logger.info(f"[ LOCAL ] Confirmed: '{model_name}' is active and ready.")
            return {"success": True, "warning": None}

        except Exception as e:
            logger.error(f"set_local_model failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def set_cloud_model(self, model_id: str = None) -> bool:
        """
        Switch back to the cloud Gemini model.

        Clears the local model selection and restores normal routing.

        Args:
            model_id: Optional specific cloud model ID (e.g. 'gemini-2.0-flash')

        Returns:
            True on success.
        """
        try:
            from src.shared.config_manager import ConfigManager
            from src.infrastructure.repositories import ConfigRepository
            from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
            import os

            from src.core.intelligence.model_lifecycle import switch_main_model
            from src.core.tools.failure import failure, UNAVAILABLE
            previous = (ConfigManager.get_local_model_config()
                        if ConfigManager.is_local_model_active() else {})
            if not await switch_main_model(previous, {}):
                return failure("The current local model is still serving a request", failure_class=UNAVAILABLE,
                               next_actions=["Wait for the active chat to finish before changing models."])
            
            # Persist: disable local mode
            config_repo = ConfigRepository()
            config_repo.update_preference("local_models.use_local", False)
            config_repo.update_preference("local_models.active_model", None)
            
            model_name = model_id or ConfigManager.get_active_model()
            api_key = os.getenv('GEMINI_API_KEY') if 'gemini' in model_name.lower() else None

            adapter = LiteLLMAdapter(
                model_name=model_name,
                api_key=api_key,
                system_instruction=ConfigManager.get_persona_instruction()
            )
            
            if self._text_mode_client:
                 self._text_mode_client.adapter = adapter
                 self._text_mode_client.clear_history()
            
            if model_id:
                # Update both global cloud_model and intelligence preference
                config_repo.update_preference("cloud_model", model_id)
                config_repo.update_preference("intelligence.model", model_id)

            # Update in-memory config
            ConfigManager._recursive_update(
                ConfigManager._config,
                {"local_models": {"use_local": False, "active_model": None}},
            )
            if model_id:
                 ConfigManager._recursive_update(ConfigManager._config, {"intelligence": {"model": model_id}})

            # 5. Update shared assistant state
            if self._assistant:
                # We don't build a full adapter here as select_model will do it on next turn,
                # but we trigger a reload to ensure the next turn uses the right model.
                if hasattr(self._assistant, 'model_router') and self._assistant.model_router:
                    self._assistant.model_router.reload_models()

            # 6. Notify Brain if in cloud mode
            if self._is_uplink and self._uplink and hasattr(self._uplink, 'send_model_update'):
                await self._uplink.send_model_update({
                    "cloud_model": model_id or ConfigManager.get_active_model(),
                    "local_models": {"use_local": False}
                })

            logger.info(f"[ CLOUD ] Switched to cloud model: {model_name}")
            return {"success": True, "error": None}

        except Exception as e:
            logger.error(f"set_cloud_model failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_active_model_info(self) -> Dict[str, Any]:
        """
        Return info about the currently active model for the GUI status indicator.

        Returns:
            Dict with keys: mode ("local"|"cloud"), name, provider, supports_tools
        """
        try:
            from src.shared.config_manager import ConfigManager
            if ConfigManager.is_local_model_active():
                cfg = ConfigManager.get_local_model_config()
                from src.shared.model_selection import describe_endpoint_model
                return describe_endpoint_model(cfg)
        except Exception:
            pass

        try:
            from src.shared.config_manager import ConfigManager
            model_name = ConfigManager.get_active_model()
        except:
            model_name = "Cloud Model"
            
        return {
            "mode": "cloud",
            "name": model_name,
            "provider": "cloud",
            "supports_tools": True,
        }
