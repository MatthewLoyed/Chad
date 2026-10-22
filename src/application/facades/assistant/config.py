from typing import Dict, Any, Optional
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class ConfigFacadeMixin:
    """Mixin for configuration and context-related facade methods."""

    def get_voice_name(self) -> str:
        """
        Get configured voice name.

        Returns:
            Voice name string
        """
        if not self._assistant or not hasattr(self._assistant, 'config'):
            return 'Kore'

        config = self._assistant.config
        if not config:
            return 'Kore'

        try:
            return config.get('voice_name', 'Kore')
        except Exception as e:
            logger.error(f"Failed to get voice name: {e}")
            return 'Kore'

    async def set_voice(self, voice_name: str) -> bool:
        """
        Set AI voice and reload models.

        Args:
            voice_name: Name of the voice to use

        Returns:
            True if successful
        """
        from src.infrastructure.repositories import ConfigRepository

        if not self._assistant:
            return False

        try:
            config_repo = ConfigRepository()

            # Update config
            self._assistant.config['voice_name'] = voice_name
            config_repo.update_preference("voice_name", voice_name)

            # Reload models if model_router exists
            if hasattr(self._assistant, 'model_router') and self._assistant.model_router:
                self._assistant.model_router.reload_models()

            # Stop active session if speaking
            if (hasattr(self._assistant, 'session_manager') and
                self._assistant.session_manager and
                self._assistant.session_manager.is_speaking):
                self._assistant.session_manager.is_listening = False

            logger.info(f"Voice changed to: {voice_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to set voice: {e}", exc_info=True)
            return False

    async def set_model(self, model_name: str) -> bool:
        """
        Set text mode model and persist to config.

        Args:
            model_name: Model ID to use

        Returns:
            True if successful
        """
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.repositories import ConfigRepository

        if not self._assistant:
            return False

        try:
            config_repo = ConfigRepository()

            # Update text_mode_client if exists
            if self._text_mode_client:
                # Update model name on the adapter directly if it supports it
                if hasattr(self._text_mode_client, 'adapter'):
                    self._text_mode_client.adapter.model_name = model_name
                
                # Still set the ad-hoc attribute for backward compatibility if needed
                self._text_mode_client.model_name = model_name
                self._text_mode_client.conversation_history.clear()

            # Persist to config
            config_repo.update_preference("active_model", model_name)
            config_repo.update_preference("models.text.model", model_name)

            # Synchronize assistant config
            ConfigManager._recursive_update(
                self._assistant.config,
                {"models": {"text": {"model": model_name}}}
            )

            # Update model router if exists
            if hasattr(self._assistant, 'model_router') and self._assistant.model_router:
                self._assistant.model_router.model_configs = self._assistant.config.get('models', {})
                self._assistant.model_router.reload_models()

            # If Gemini 3, also update thinking model
            if 'gemini-3' in model_name:
                config_repo.update_preference("models.thinking.model", model_name)
                ConfigManager._recursive_update(
                    self._assistant.config,
                    {"models": {"thinking": {"model": model_name}}}
                )

            logger.info(f"Model changed to: {model_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to set model: {e}", exc_info=True)
            return False

    async def get_context_status(self) -> Dict[str, Any]:
        """
        Get current context budget status.

        Returns:
            Dict with context status information
        """
        if not self._assistant or not hasattr(self._assistant, 'session_manager'):
            return {}

        try:
            sm = self._assistant.session_manager
            if sm and hasattr(sm, 'consolidation_manager') and sm.consolidation_manager:
                status = sm.consolidation_manager.get_status()

                # Add current token count if available
                if self._text_mode_client:
                    current_tokens = getattr(self._text_mode_client, 'last_token_count', 0)
                    status['current_load'] = current_tokens

                return status

        except Exception as e:
            logger.error(f"Failed to get context status: {e}", exc_info=True)

        return {}

    async def set_context_profile(self, profile_name: str) -> bool:
        """
        Set context budget profile.

        Args:
            profile_name: Profile name (aggressive/balanced/conservative)

        Returns:
            True if successful
        """
        from src.infrastructure.repositories import ConfigRepository

        if not self._assistant or not hasattr(self._assistant, 'session_manager'):
            return False

        try:
            sm = self._assistant.session_manager
            if sm and hasattr(sm, 'consolidation_manager') and sm.consolidation_manager:
                sm.consolidation_manager.set_profile(profile_name)

                # Persist to config
                config_repo = ConfigRepository()
                config_repo.update_preference("context_budget_profile", profile_name)

                logger.info(f"Context profile changed to: {profile_name}")
                return True

        except Exception as e:
            logger.error(f"Failed to set context profile: {e}", exc_info=True)

        return False

    def get_mcp_servers(self) -> list:
        """Return all configured MCP servers with their status."""
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            merge_mcp_configs,
            mcp_credentials_satisfied,
        )

        config_repo = ConfigRepository()
        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        if not config_repo.load('preferences', use_cache=False).get('connectors_opt_in_v1'):
            config_repo.save('preferences', prefs, update_cache=True)

        base_servers = ConfigManager.get('mcp_servers', {}) or {}
        disabled = set(prefs.get('disabled_mcp_servers', []))
        merged = merge_mcp_configs(prefs)

        mcp_registry = getattr(self._assistant, 'mcp_registry', None)

        result = []
        for name, cfg in merged.items():
            entry = mcp_registry.get(name) if mcp_registry else None
            configured, _ = mcp_credentials_satisfied(cfg)
            enabled = name not in disabled and configured
            result.append({
                'name': name,
                'command': cfg.get('command', []),
                'env': cfg.get('env', {}),
                'timeout_seconds': cfg.get('timeout_seconds'),
                'min_delay_seconds': cfg.get('min_delay_seconds'),
                'configured': configured,
                'enabled': enabled,
                'connected': bool(entry and entry.session) and enabled,
            })
        return result

    def get_native_connectors(self) -> list:
        """Return native connector status (Gmail, Telegram) for the settings UI."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            native_connector_status,
        )

        config_repo = ConfigRepository()
        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        if not config_repo.load('preferences', use_cache=False).get('connectors_opt_in_v1'):
            config_repo.save('preferences', prefs, update_cache=True)
        return native_connector_status(prefs)

    def _resolve_mcp_server_template(self, name: str, command: list, env: dict) -> tuple[list, dict]:
        """Prefer shipped config.yaml templates for known connectors."""
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.connectors.policy import resolve_env_value

        base_cfg = (ConfigManager.get('mcp_servers', {}) or {}).get(name, {})
        final_command = base_cfg.get('command') or command
        base_env = base_cfg.get('env') or {}
        final_env: dict[str, str] = {}
        for key, value in {**base_env, **(env or {})}.items():
            raw = (env or {}).get(key, value)
            if raw is not None and str(raw) and not (
                isinstance(raw, str) and raw.startswith('%') and raw.endswith('%')
            ):
                final_env[key] = str(raw)
            else:
                final_env[key] = resolve_env_value(value)
        return final_command, final_env

    async def add_mcp_server(self, name: str, command: list, env: dict, extra: dict = None) -> bool:
        """Add a new MCP server to preferences and hot-reload."""
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            mcp_credentials_satisfied,
            normalize_mcp_env_for_storage,
            persist_connector_secrets,
        )
        try:
            config_repo = ConfigRepository()
            prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
            base_cfg = (ConfigManager.get('mcp_servers', {}) or {}).get(name, {})

            final_command, resolved_env = self._resolve_mcp_server_template(name, command, env or {})
            ok, missing = mcp_credentials_satisfied({'env': resolved_env})
            if not ok:
                logger.warning(f"add_mcp_server refused '{name}': missing credentials {missing}")
                return False

            persist_connector_secrets(env or {})
            ConfigManager.load_config()

            stored_env = normalize_mcp_env_for_storage(env or {}, base_cfg.get('env'))
            cfg: dict = {'command': final_command, 'env': stored_env}
            if extra:
                cfg.update({k: v for k, v in extra.items() if v is not None})
            for key in ('timeout_seconds', 'min_delay_seconds', 'excluded_tools', 'connect_timeout_seconds'):
                if key in base_cfg and key not in cfg:
                    cfg[key] = base_cfg[key]

            prefs.setdefault('mcp_servers', {})[name] = cfg
            disabled = set(prefs.get('disabled_mcp_servers', []))
            disabled.discard(name)
            prefs['disabled_mcp_servers'] = sorted(disabled)
            config_repo.save('preferences', prefs, update_cache=True)
            await self._reload_mcp_from_prefs(prefs)
            return True
        except Exception as e:
            logger.error(f"add_mcp_server failed: {e}", exc_info=True)
            return False

    async def update_mcp_server(self, name: str, command: list, env: dict, extra: dict = None) -> bool:
        """Update an existing MCP server config and hot-reload."""
        return await self.add_mcp_server(name, command, env, extra)

    async def delete_mcp_server(self, name: str) -> bool:
        """Remove an MCP server from preferences and hot-reload."""
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.repositories import ConfigRepository
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load('preferences', use_cache=False)
            # Remove from pref overrides if present
            prefs.setdefault('mcp_servers', {}).pop(name, None)
            # If it lives in base config, mark as disabled instead
            base_servers = ConfigManager.get('mcp_servers', {}) or {}
            if name in base_servers:
                disabled = set(prefs.get('disabled_mcp_servers', []))
                disabled.add(name)
                prefs['disabled_mcp_servers'] = list(disabled)
            config_repo.save('preferences', prefs, update_cache=True)
            await self._reload_mcp_from_prefs(prefs)
            return True
        except Exception as e:
            logger.error(f"delete_mcp_server failed: {e}", exc_info=True)
            return False

    async def toggle_mcp_server(self, name: str, enabled: bool) -> bool:
        """Enable or disable an MCP server and hot-reload."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import merge_mcp_configs, mcp_credentials_satisfied
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load('preferences', use_cache=False)
            if enabled:
                cfg = merge_mcp_configs(prefs).get(name, {})
                ok, missing = mcp_credentials_satisfied(cfg)
                if not ok:
                    logger.warning(f"toggle_mcp_server refused enable '{name}': missing {missing}")
                    return False
            disabled = set(prefs.get('disabled_mcp_servers', []))
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
            prefs['disabled_mcp_servers'] = sorted(disabled)
            config_repo.save('preferences', prefs, update_cache=True)
            await self._reload_mcp_from_prefs(prefs)
            return True
        except Exception as e:
            logger.error(f"toggle_mcp_server failed: {e}", exc_info=True)
            return False

    async def test_mcp_connection(self, name: str, command: list, env: dict, extra: dict = None) -> dict:
        """Spin up a temporary MCP connection to validate credentials before saving."""
        from src.core.tools.mcp_registry import MCPServerEntry
        final_command, resolved_env = self._resolve_mcp_server_template(name, command, env or {})
        config = {k: v for k, v in (extra or {}).items() if v is not None}
        entry = MCPServerEntry(
            name=name or "test-server", command=final_command, env=resolved_env, config=config
        )
        try:
            await entry.connect()
            tools = await entry.list_tools()
            tool_names = [t.get("name", "") for t in tools]
            return {"success": True, "tools": tool_names}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await entry.disconnect()

    async def enable_native_connector(self, connector_id: str) -> bool:
        """Enable a native connector after setup (Gmail OAuth complete, etc.)."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            NATIVE_CONNECTOR_IDS,
            native_connector_status,
        )

        if connector_id not in NATIVE_CONNECTOR_IDS:
            return False
        status = {row['id']: row for row in native_connector_status()}
        row = status.get(connector_id)
        if not row or not row.get('configured'):
            logger.warning(f"enable_native_connector refused '{connector_id}': not configured")
            return False
        try:
            config_repo = ConfigRepository()
            prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
            disabled = set(prefs.get('disabled_native_connectors', []))
            disabled.discard(connector_id)
            prefs['disabled_native_connectors'] = sorted(disabled)
            config_repo.save('preferences', prefs, update_cache=True)
            return True
        except Exception as e:
            logger.error(f"enable_native_connector failed: {e}", exc_info=True)
            return False

    async def save_telegram_connector(self, bot_token: str) -> dict:
        """Validate and persist a Telegram bot token, then enable the connector."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import ensure_connector_defaults, test_telegram_token
        from src.remote.telegram.telegram_client import reset_telegram_client
        from src.shared.config_manager import ConfigManager

        result = await test_telegram_token(bot_token)
        if not result.get('success'):
            return result

        config_repo = ConfigRepository()
        config_repo.update_secret('telegram.bot_token', bot_token.strip())
        ConfigManager.load_config()
        reset_telegram_client()

        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        disabled = set(prefs.get('disabled_native_connectors', []))
        disabled.discard('telegram')
        prefs['disabled_native_connectors'] = sorted(disabled)
        config_repo.save('preferences', prefs, update_cache=True)
        return result

    async def save_canvas_connector(
        self,
        base_url: str,
        token: str,
        contact_email: str = "",
    ) -> dict:
        """Validate and persist Canvas LMS credentials, then enable the connector."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import ensure_connector_defaults, test_canvas_connection
        from src.tools.canvas.manager import CanvasManager

        result = test_canvas_connection(base_url, token, contact_email)
        if not result.get('success'):
            return result

        config_repo = ConfigRepository()
        config_repo.update_secret('CANVAS_BASE_URL', base_url.strip())
        config_repo.update_secret('CANVAS_TOKEN', token.strip())
        if (contact_email or '').strip():
            config_repo.update_secret('CANVAS_CONTACT_EMAIL', contact_email.strip())
        else:
            config_repo.update_secret('CANVAS_CONTACT_EMAIL', '')

        from src.shared.config_manager import ConfigManager
        ConfigManager.load_config()
        CanvasManager.reset_instance()

        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        disabled = set(prefs.get('disabled_native_connectors', []))
        disabled.discard('canvas')
        prefs['disabled_native_connectors'] = sorted(disabled)
        config_repo.save('preferences', prefs, update_cache=True)
        return result

    async def save_stock_images_connector(
        self,
        pexels_key: str = "",
        unsplash_key: str = "",
        pixabay_key: str = "",
    ) -> dict:
        """Validate and persist optional stock-image API keys, then enable the connector."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            test_stock_images_connection,
        )
        from src.shared.config_manager import ConfigManager

        result = test_stock_images_connection(pexels_key, unsplash_key, pixabay_key)
        if not result.get('success'):
            return result

        config_repo = ConfigRepository()
        if (pexels_key or '').strip():
            config_repo.update_secret('PEXELS_API_KEY', pexels_key.strip())
        if (unsplash_key or '').strip():
            config_repo.update_secret('UNSPLASH_ACCESS_KEY', unsplash_key.strip())
        if (pixabay_key or '').strip():
            config_repo.update_secret('PIXABAY_API_KEY', pixabay_key.strip())
        ConfigManager.load_config()

        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        disabled = set(prefs.get('disabled_native_connectors', []))
        disabled.discard('stock-images')
        prefs['disabled_native_connectors'] = sorted(disabled)
        config_repo.save('preferences', prefs, update_cache=True)
        return result

    async def save_finance_connector(
        self,
        client_id: str,
        secret: str,
        env: str = "sandbox",
    ) -> dict:
        """Validate and persist Plaid credentials, then enable the finance connector."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import (
            ensure_connector_defaults,
            test_plaid_connection,
        )
        from src.shared.config_manager import ConfigManager

        result = test_plaid_connection(client_id, secret, env)
        if not result.get('success'):
            return result

        config_repo = ConfigRepository()
        config_repo.update_secret('PLAID_CLIENT_ID', client_id.strip())
        config_repo.update_secret('PLAID_SECRET', secret.strip())
        config_repo.update_secret('PLAID_ENV', (env or 'sandbox').strip().lower())
        ConfigManager.load_config()

        prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
        disabled = set(prefs.get('disabled_native_connectors', []))
        disabled.discard('finance')
        prefs['disabled_native_connectors'] = sorted(disabled)
        config_repo.save('preferences', prefs, update_cache=True)
        return result

    async def delete_native_connector(self, connector_id: str) -> bool:
        """Disable a native connector (optionally clears Telegram token)."""
        from src.infrastructure.repositories import ConfigRepository
        from src.infrastructure.connectors.policy import ensure_connector_defaults, NATIVE_CONNECTOR_IDS
        from src.remote.telegram.telegram_client import reset_telegram_client
        from src.shared.config_manager import ConfigManager

        if connector_id not in NATIVE_CONNECTOR_IDS:
            return False
        try:
            config_repo = ConfigRepository()
            prefs = ensure_connector_defaults(config_repo.load('preferences', use_cache=False))
            disabled = set(prefs.get('disabled_native_connectors', []))
            disabled.add(connector_id)
            prefs['disabled_native_connectors'] = sorted(disabled)
            if connector_id == 'telegram':
                config_repo.update_secret('telegram.bot_token', '')
                ConfigManager.load_config()
                reset_telegram_client()
            if connector_id == 'canvas':
                config_repo.update_secret('CANVAS_BASE_URL', '')
                config_repo.update_secret('CANVAS_TOKEN', '')
                config_repo.update_secret('CANVAS_CONTACT_EMAIL', '')
                ConfigManager.load_config()
            if connector_id == 'stock-images':
                config_repo.update_secret('PEXELS_API_KEY', '')
                config_repo.update_secret('UNSPLASH_ACCESS_KEY', '')
                config_repo.update_secret('PIXABAY_API_KEY', '')
                ConfigManager.load_config()
                from src.tools.canvas.manager import CanvasManager
                CanvasManager.reset_instance()
            if connector_id == 'finance':
                config_repo.update_secret('PLAID_CLIENT_ID', '')
                config_repo.update_secret('PLAID_SECRET', '')
                config_repo.update_secret('PLAID_ENV', '')
                ConfigManager.load_config()
            config_repo.save('preferences', prefs, update_cache=True)
            return True
        except Exception as e:
            logger.error(f"delete_native_connector failed: {e}", exc_info=True)
            return False

    async def _reload_mcp_from_prefs(self, prefs: dict) -> None:
        """Build effective MCP config and apply hot-reload diff."""
        from src.infrastructure.connectors.policy import get_effective_mcp_servers
        mcp_registry = getattr(self._assistant, 'mcp_registry', None)
        if not mcp_registry:
            return
        effective = get_effective_mcp_servers(prefs)
        await mcp_registry.apply_config_diff(effective)

    async def force_consolidation(self) -> bool:
        """
        Force manual context consolidation.

        Returns:
            True if successful
        """
        if not self._text_mode_client:
            return False

        try:
            await self._text_mode_client.perform_manual_consolidation()
            logger.info("Manual consolidation triggered")
            return True

        except Exception as e:
            logger.error(f"Failed to force consolidation: {e}", exc_info=True)
            return False

    def get_subagents(self) -> list:
        """
        Get all specialized subagents with their enabled/disabled status.

        Returns:
            List of subagent dictionaries
        """
        from src.shared.config_manager import ConfigManager
        from src.infrastructure.repositories import ConfigRepository
        from src.core.orchestration.agents.factory import AGENT_CONFIGS

        config_repo = ConfigRepository()
        prefs = config_repo.load('preferences', use_cache=False)
        # Fall back to config.yaml default if preferences.yaml hasn't been written yet
        default_disabled = ConfigManager.get('disabled_subagents') or []
        disabled_agents = set(prefs.get('disabled_subagents', default_disabled))
        model_overrides = prefs.get('subagent_model_overrides', {}) or {}

        # Human-friendly labels and descriptions for display in GUI
        info_map = {
            "ui_automation_agent": {
                "label": "UI Automation Agent",
                "description": "Clicks/types in local Windows apps, takes screenshots, and executes local UI automation flows."
            },
            "browser_agent": {
                "label": "Browser Agent",
                "description": "Interact with dynamic web pages (logging into accounts, clicking buttons, and filling out forms)."
            },
            "research_agent": {
                "label": "Research Agent",
                "description": "Searches the web, finds documentation, and fetches static HTML pages for research."
            },
            "code_agent": {
                "label": "Code Agent",
                "description": "Writes and executes Python scripts, runs command prompt/terminal commands, and manages Git repositories."
            },
            "system_agent": {
                "label": "System Agent",
                "description": "Manages local files (move, copy, delete), checks CPU/RAM stats, and adjusts OS settings."
            }
        }

        result = []
        for name, agent_cfg in AGENT_CONFIGS.items():
            info = info_map.get(name, {
                "label": name.replace("_agent", "").replace("_", " ").title(),
                "description": f"Specialized subagent for {name} domain."
            })
            # User override (preferences.yaml) wins over the AGENT_CONFIGS default pin
            model = model_overrides.get(name) or agent_cfg.get("model_override")
            
            extra = {}
            if name == "browser_agent":
                extra = {
                    "vision_assist_enabled": ConfigManager.get("tools.browser_vision_assist_enabled", False),
                    "vision_assist_model": ConfigManager.get("tools.browser_vision_assist_model", "qwen/qwen3-vl-8b"),
                }

            result.append({
                "name": name,
                "label": info["label"],
                "description": info["description"],
                "enabled": name not in disabled_agents,
                "model": model,
                "is_model_override": name in model_overrides,
                **extra
            })
        return result

    async def toggle_subagent(self, name: str, enabled: bool) -> bool:
        """
        Enable or disable a subagent and persist choice to preferences.

        Args:
            name: Name of the subagent (e.g., 'browser_agent')
            enabled: True to enable, False to disable

        Returns:
            True if successful
        """
        from src.infrastructure.repositories import ConfigRepository
        from src.shared.config_manager import ConfigManager
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load('preferences', use_cache=False)
            disabled = set(prefs.get('disabled_subagents', []))
            
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
                
            prefs['disabled_subagents'] = list(disabled)
            config_repo.save('preferences', prefs, update_cache=True)
            
            # Hot-sync to in-memory config for immediate query access
            ConfigManager._recursive_update(
                self._assistant.config,
                {"disabled_subagents": list(disabled)}
            )
            
            # Clear compiled graph caches in TurnCoordinator so the next turn compiles fresh
            text_client = self._text_mode_client or getattr(self._assistant, 'text_mode_client', None)
            if not text_client and hasattr(self._assistant, 'container') and self._assistant.container:
                text_client = getattr(self._assistant.container, 'text_mode_client', None)
                
            if text_client and text_client.turn_coordinator:
                tc = text_client.turn_coordinator
                if tc._langgraph_engine:
                    tc._langgraph_engine._compiled_graph = None
                    logger.info("[ FACADE ] Cleared compiled graph cache in TurnCoordinator's LangGraphEngine.")
                    
            logger.info(f"Subagent '{name}' toggled to {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            logger.error(f"toggle_subagent failed: {e}", exc_info=True)
            return False

    async def set_subagent_model(
        self,
        name: str,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        thinking_level: Optional[str] = None
    ) -> bool:
        """
        Set or clear a per-subagent model override and persist to preferences.

        Passing model_name=None clears the override, reverting the agent to its
        AGENT_CONFIGS default (or the main router model if it has no default pin).

        Args:
            name: Name of the subagent (e.g., 'research_agent')
            model_name: Model identifier (e.g., 'qwen/qwen3.5-9b'), or None to clear
            provider: Provider for the model (e.g., 'lmstudio')
            thinking_level: Thinking effort level (e.g., 'high', 'medium', 'low')

        Returns:
            True if successful
        """
        from src.infrastructure.repositories import ConfigRepository
        from src.shared.config_manager import ConfigManager
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load('preferences', use_cache=False)
            overrides = dict(prefs.get('subagent_model_overrides', {}) or {})

            if model_name:
                overrides[name] = {
                    "model_name": model_name,
                    "provider": provider or "lmstudio",
                    "thinking_level": thinking_level or "medium",
                }
            else:
                overrides.pop(name, None)

            prefs['subagent_model_overrides'] = overrides
            config_repo.save('preferences', prefs, update_cache=True)

            # Hot-sync to in-memory config for immediate query access
            ConfigManager._recursive_update(
                self._assistant.config,
                {"subagent_model_overrides": overrides}
            )

            # Clear compiled graph caches so the next turn recompiles agents with the new model
            text_client = self._text_mode_client or getattr(self._assistant, 'text_mode_client', None)
            if not text_client and hasattr(self._assistant, 'container') and self._assistant.container:
                text_client = getattr(self._assistant.container, 'text_mode_client', None)

            if text_client and text_client.turn_coordinator:
                tc = text_client.turn_coordinator
                if tc._langgraph_engine:
                    tc._langgraph_engine._compiled_graph = None
                    logger.info("[ FACADE ] Cleared compiled graph cache in TurnCoordinator's LangGraphEngine.")

            logger.info(f"Subagent '{name}' model override set to {overrides.get(name)}")
            return True
        except Exception as e:
            logger.error(f"set_subagent_model failed: {e}", exc_info=True)
            return False

    async def set_browser_vision_assist(self, enabled: bool, model_name: Optional[str] = None) -> bool:
        """
        Set browser agent vision-assist settings and persist to preferences.

        Args:
            enabled: True to enable vision-assist, False to disable.
            model_name: Pinned vision model identifier (e.g. 'qwen/qwen3-vl-8b').

        Returns:
            True if successful.
        """
        from src.infrastructure.repositories import ConfigRepository
        from src.shared.config_manager import ConfigManager
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load('preferences', use_cache=False)
            
            # Retrieve or construct tools section in prefs
            tools_prefs = prefs.setdefault('tools', {})
            tools_prefs['browser_vision_assist_enabled'] = enabled
            if model_name:
                tools_prefs['browser_vision_assist_model'] = model_name
            else:
                tools_prefs.pop('browser_vision_assist_model', None)
                
            config_repo.save('preferences', prefs, update_cache=True)
            
            # Hot-sync to in-memory config for immediate query access
            ConfigManager._recursive_update(
                self._assistant.config,
                {"tools": {
                    "browser_vision_assist_enabled": enabled,
                    "browser_vision_assist_model": model_name or "qwen/qwen3-vl-8b"
                }}
            )
            logger.info(f"[ FACADE ] Browser vision-assist set to: enabled={enabled}, model={model_name}")
            return True
        except Exception as e:
            logger.error(f"set_browser_vision_assist failed: {e}", exc_info=True)
            return False

    def get_browser_safety_settings(self) -> dict:
        """
        Return browser_agent computer-use settings for the Settings UI (issue #328).

        Returns:
            Dict with ``approval_mode`` and ``allowlist`` keys.
        """
        from src.core.orchestration import browser_safety

        return {
            "approval_mode": browser_safety.get_browser_approval_mode(),
            "allowlist": browser_safety.get_browser_allowlist(),
        }

    async def set_browser_safety_settings(
        self,
        approval_mode: Optional[str] = None,
        allowlist: Optional[list] = None,
    ) -> bool:
        """
        Persist browser_agent approval mode and/or write-allowlist to preferences.

        Args:
            approval_mode: One of allow_all, model_decide, ask_all.
            allowlist: Domain hostnames trusted for autonomous writes.

        Returns:
            True if successful.
        """
        from src.core.orchestration import browser_safety
        from src.infrastructure.repositories import ConfigRepository
        from src.shared.config_manager import ConfigManager

        try:
            tools_update: dict = {}
            if approval_mode is not None:
                if approval_mode not in browser_safety.VALID_BROWSER_APPROVAL_MODES:
                    logger.error(f"Invalid browser approval mode: {approval_mode!r}")
                    return False
                tools_update["browser_approval_mode"] = approval_mode

            if allowlist is not None:
                normalized = []
                seen = set()
                for entry in allowlist:
                    if not isinstance(entry, str):
                        continue
                    host = browser_safety.extract_domain(entry.strip()) or entry.strip().lower().lstrip("www.")
                    if not host or host in seen:
                        continue
                    seen.add(host)
                    normalized.append(host)
                tools_update["browser_write_allowlist"] = normalized

            if not tools_update:
                return True

            config_repo = ConfigRepository()
            prefs = config_repo.load("preferences", use_cache=False)
            tools_prefs = prefs.setdefault("tools", {})
            tools_prefs.update(tools_update)
            config_repo.save("preferences", prefs, update_cache=True)

            ConfigManager._recursive_update(self._assistant.config, {"tools": tools_update})
            logger.info(f"[ FACADE ] Browser safety settings updated: {tools_update}")
            return True
        except Exception as e:
            logger.error(f"set_browser_safety_settings failed: {e}", exc_info=True)
            return False

    def get_presenton_settings(self) -> dict:
        """Return Presenton sidecar settings for Settings → Services."""
        from src.infrastructure.services.presenton_sidecar import get_presenton_settings

        return get_presenton_settings()

    async def set_presenton_settings(
        self,
        enabled: Optional[bool] = None,
        url: Optional[str] = None,
        llm_url: Optional[str] = None,
        model: Optional[str] = None,
        use_async: Optional[bool] = None,
        restart_sidecar: bool = False,
    ) -> dict:
        """Persist settings; provision local Docker only on an explicit connect."""
        from src.infrastructure.services.presenton_sidecar import set_presenton_settings

        return await set_presenton_settings(
            enabled=enabled,
            url=url,
            llm_url=llm_url,
            model=model,
            use_async=use_async,
            restart_sidecar=restart_sidecar,
        )

    def get_chad_code_settings(self) -> dict:
        """Return Chad Code (OpenHands harness) settings for Settings → Services (issue #574).

        Reads the ``tools.openhands_*`` keys the dispatch tool consumes. Defaults mirror
        ``src/tools/dev/openhands_dispatch.py`` so the GUI shows the effective values even
        when a key is absent from ``config.yaml``.
        """
        from src.shared.config_manager import ConfigManager

        return {
            "coder_model": str(ConfigManager.get("tools.openhands_coder_model", "openai/qwen/qwen3.6-35b-a3b")),
            "max_iterations": int(ConfigManager.get("tools.openhands_max_iterations", 30)),
            "job_timeout_s": int(ConfigManager.get("tools.openhands_job_timeout_s", 1800)),
            "use_task_tracker": bool(ConfigManager.get("tools.openhands_use_task_tracker", False)),
            "native_tool_calling": bool(ConfigManager.get("tools.openhands_native_tool_calling", False)),
            "use_condenser": bool(ConfigManager.get("tools.openhands_use_condenser", False)),
            "use_critic": bool(ConfigManager.get("tools.openhands_use_critic", False)),
            "context7_mcp": bool(ConfigManager.get("tools.openhands_context7_mcp", True)),
            "github_mcp": bool(ConfigManager.get("tools.openhands_github_mcp", False)),
            "stream_events": bool(ConfigManager.get("tools.openhands_stream_events", True)),
        }

    def set_chad_code_settings(
        self,
        coder_model: Optional[str] = None,
        max_iterations: Optional[int] = None,
        job_timeout_s: Optional[int] = None,
        use_task_tracker: Optional[bool] = None,
        native_tool_calling: Optional[bool] = None,
        use_condenser: Optional[bool] = None,
        use_critic: Optional[bool] = None,
        context7_mcp: Optional[bool] = None,
        github_mcp: Optional[bool] = None,
        stream_events: Optional[bool] = None,
    ) -> dict:
        """Persist Chad Code (OpenHands harness) settings (issue #574).

        Mirrors the preferences-overlay write pattern used by ``set_searxng_settings`` /
        ``set_browser_safety_settings`` — writes to the ``preferences`` config layer (not
        ``config/config.yaml`` directly) under ``tools.openhands_*`` and hot-reloads
        ``ConfigManager`` so the change takes effect without a restart. Only supplied
        (non-None) fields are written.
        """
        try:
            tools_update: Dict[str, Any] = {}
            if coder_model is not None:
                model = str(coder_model).strip()
                if not model:
                    return {"success": False, "error": "coder_model must be a non-empty string.",
                            **self.get_chad_code_settings()}
                tools_update["openhands_coder_model"] = model
            if max_iterations is not None:
                try:
                    mi = int(max_iterations)
                except (TypeError, ValueError):
                    return {"success": False, "error": "max_iterations must be an integer.",
                            **self.get_chad_code_settings()}
                if mi < 1:
                    return {"success": False, "error": "max_iterations must be >= 1.",
                            **self.get_chad_code_settings()}
                tools_update["openhands_max_iterations"] = mi
            if job_timeout_s is not None:
                try:
                    ts = int(job_timeout_s)
                except (TypeError, ValueError):
                    return {"success": False, "error": "job_timeout_s must be an integer.",
                            **self.get_chad_code_settings()}
                if ts < 1:
                    return {"success": False, "error": "job_timeout_s must be >= 1.",
                            **self.get_chad_code_settings()}
                tools_update["openhands_job_timeout_s"] = ts
            if use_task_tracker is not None:
                tools_update["openhands_use_task_tracker"] = bool(use_task_tracker)
            if native_tool_calling is not None:
                tools_update["openhands_native_tool_calling"] = bool(native_tool_calling)
            if use_condenser is not None:
                tools_update["openhands_use_condenser"] = bool(use_condenser)
            if use_critic is not None:
                tools_update["openhands_use_critic"] = bool(use_critic)
            if context7_mcp is not None:
                tools_update["openhands_context7_mcp"] = bool(context7_mcp)
            if github_mcp is not None:
                tools_update["openhands_github_mcp"] = bool(github_mcp)
            if stream_events is not None:
                tools_update["openhands_stream_events"] = bool(stream_events)

            if not tools_update:
                return {"success": True, **self.get_chad_code_settings()}

            from src.infrastructure.repositories import ConfigRepository
            from src.shared.config_manager import ConfigManager

            config_repo = ConfigRepository()
            prefs = config_repo.load("preferences", use_cache=False)
            tools_prefs = prefs.setdefault("tools", {})
            tools_prefs.update(tools_update)
            config_repo.save("preferences", prefs, update_cache=True)

            ConfigManager._recursive_update(ConfigManager.get_config(), {"tools": tools_update})
            logger.info(f"[ FACADE ] Chad Code settings updated: {tools_update}")
            return {"success": True, **self.get_chad_code_settings()}
        except Exception as e:
            logger.error(f"set_chad_code_settings failed: {e}", exc_info=True)
            return {"success": False, "error": str(e), **self.get_chad_code_settings()}

    async def chad_code_preflight(self) -> dict:
        """Run the Chad Code harness preflight (Docker + image + LM Studio + model), issue #574."""
        try:
            from src.tools.dev.openhands_dispatch import preflight

            return await preflight()
        except Exception as e:
            logger.error(f"chad_code_preflight failed: {e}", exc_info=True)
            return {"ready": False, "error": str(e), "checks": {}}

    async def chad_code_job_status(self, job_id: str) -> dict:
        """Report a dispatched coding job's status/files/transcript (issue #574)."""
        try:
            from src.tools.dev.openhands_dispatch import CheckCodingTaskTool

            return await CheckCodingTaskTool().execute(job_id=job_id)
        except Exception as e:
            logger.error(f"chad_code_job_status failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def get_searxng_settings(self) -> dict:
        """Return SearXNG sidecar settings for Settings → Services."""
        from src.infrastructure.services.searxng_sidecar import get_searxng_settings

        return get_searxng_settings()

    def set_searxng_settings(self, search_backend: Optional[str] = None) -> dict:
        """Persist ``tools.search_backend`` (searxng/brave/serper/tavily/all)."""
        from src.infrastructure.services.searxng_sidecar import set_searxng_settings

        return set_searxng_settings(search_backend=search_backend)

    async def get_search_quota(self) -> dict:
        """Best-effort quota snapshot per configured paid search backend (issue #523)."""
        from src.infrastructure.services.searxng_sidecar import get_search_quota

        return await get_search_quota()

    async def start_searxng_sidecar(self) -> dict:
        """Bring up the local SearXNG + valkey Docker sidecar (one-click, issue #415)."""
        from src.infrastructure.services.searxng_sidecar import start_searxng_sidecar

        return await start_searxng_sidecar()

    def get_supabase_settings(self) -> dict:
        """Return Supabase connection status for Settings → Memory."""
        from src.infrastructure.services.supabase_bootstrap import get_supabase_settings

        return get_supabase_settings()

    async def bootstrap_supabase(
        self,
        *,
        url: str,
        anon_key: str,
        service_key: str = "",
        db_url: str = "",
    ) -> dict:
        """Save Supabase credentials and run schema/persona/tool-RAG bootstrap."""
        from src.infrastructure.services.supabase_bootstrap import run_bootstrap

        return await run_bootstrap(
            url=url,
            anon_key=anon_key,
            service_key=service_key,
            db_url=db_url,
        )

    def get_setup_status(self) -> dict:
        """Whether the first-run setup wizard should be shown (issue #428)."""
        from src.infrastructure.services.setup_wizard import get_setup_status

        return get_setup_status()

    def start_lmstudio_setup(self) -> dict:
        """Kick off the LM Studio model check/download in a background thread."""
        from src.infrastructure.services.setup_wizard import start_lmstudio_setup

        return start_lmstudio_setup()

    def get_lmstudio_setup_progress(self) -> dict:
        """Poll the state of a background start_lmstudio_setup() job."""
        from src.infrastructure.services.setup_wizard import get_lmstudio_setup_progress

        return get_lmstudio_setup_progress()

    async def probe_local_backend(self, provider: str, base_url: str, api_key: str = "") -> dict:
        """Probe an Ollama / custom OpenAI-compatible endpoint for the setup wizard (issue #499)."""
        from src.infrastructure.services.setup_wizard import probe_local_backend

        return await probe_local_backend(provider, base_url, api_key)

    def save_provider_choice(
        self, provider: str, base_url: str, api_key: str, model_id: str, supports_tools: bool = True
    ) -> dict:
        """Persist the wizard's backend + model pick (issue #499)."""
        from src.infrastructure.services.setup_wizard import save_provider_choice

        result = save_provider_choice(provider, base_url, api_key, model_id, supports_tools)
        router = getattr(getattr(self, "_assistant", None), "model_router", None)
        if result.get("success") and router:
            router.reload_models()
        return result


# Alias for backward-compatibility and direct facade consumers
ConfigFacade = ConfigFacadeMixin
