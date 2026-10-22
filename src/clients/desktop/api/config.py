from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.application.commands import (
    SetVoiceCommand,
    SetModelCommand,
    DiscoverLocalModelsCommand,
    SetActiveModelCommand,
    GetSubagentsCommand,
    ToggleSubagentCommand,
    SetSubagentModelCommand,
    ForceConsolidationCommand,
    GetMCPServersCommand,
    AddMCPServerCommand,
    DeleteMCPServerCommand,
    ToggleMCPServerCommand,
    TestMCPConnectionCommand,
    GetRoutineBuilderModelCommand,
    SetRoutineBuilderModelCommand,
    GetBrowserSafetyCommand,
    SetBrowserSafetyCommand,
    GetPresentonSettingsCommand,
    SetPresentonSettingsCommand,
    GetChadCodeSettingsCommand,
    SetChadCodeSettingsCommand,
    ChadCodePreflightCommand,
    ChadCodeJobStatusCommand,
    GetSearxngSettingsCommand,
    SetSearxngSettingsCommand,
    GetSearchQuotaCommand,
    StartSearxngSidecarCommand,
    GetSupabaseSettingsCommand,
    BootstrapSupabaseCommand,
    GetSetupStatusCommand,
    StartLMStudioSetupCommand,
    GetLMStudioSetupProgressCommand,
    ProbeLocalBackendCommand,
    SaveProviderChoiceCommand,
)

logger = setup_logger("WebViewAPI")


class ConfigAPIMixin:
    """Mixin for configuration-related WebView API methods (models, voice, preferences)."""

    def get_voice(self):
        """Get the current AI voice name."""
        try:
            # Fetch from centrally synchronized ConfigManager
            return ConfigManager.get_voice_name()
        except Exception:
            return 'Kore'

    def set_voice(self, voice_name):
        """Set the AI voice and persist to config."""
        success = self._execute_command(SetVoiceCommand(voice_name=voice_name), timeout=5.0, default_fallback=False)
        if success:
            window = self.main_window_ref()
            if window:
                # Sync client-side config so it reflects changes immediately
                ConfigManager.load_config()
                # Update UI
                window.update_status(state="ready", text="Voice changed. Say 'Hey Chad' to test.")
                window.set_thinking_text(f"Switching voice to {voice_name}...")
        return success

    def get_model(self):
        """Get the current active model identifier from config."""
        return ConfigManager.get_active_model()

    def get_available_models(self):
        """JavaScript can call this to get list of available models from config."""
        # Priority: Config file allowed list -> fallback defaults
        models = ConfigManager.get("available_cloud_models")
        
        logger.debug(f"[ BRIDGE ] fetching available_cloud_models: {models}")
        
        if models and isinstance(models, list):
            return models
            
        # Decoupled Fallback: If config is empty, list what we CAN use by scanning keys
        from src.core.intelligence.provider_failover import scan_available_providers
        providers = scan_available_providers()
        
        fb = []
        if 'gemini' in providers:
            fb.append({'id': 'gemini-2.0-flash', 'name': 'Gemini 2.0 Flash'})
        if 'anthropic' in providers:
            fb.append({'id': 'claude-3-5-sonnet-latest', 'name': 'Claude 3.5 Sonnet'})
        if 'openai' in providers:
            fb.append({'id': 'gpt-4o', 'name': 'GPT-4o'})
            
        return fb if fb else [{'id': ConfigManager.get_active_model(), 'name': 'Default Cloud Model'}]

    def set_model(self, model_name):
        """Set the text mode model and persist to config."""
        success = self._execute_command(SetModelCommand(model_name=model_name), timeout=5.0, default_fallback=False)
        if success:
            window = self.main_window_ref()
            if window:
                # Sync client-side config so it reflects changes immediately
                ConfigManager.load_config()
                # Update UI
                window.set_thinking_text(f"Switched to {model_name}")
                window.update_status(state="ready", text=f"Model: {model_name}")
        return success

    def discover_local_models(self):
        """
        Scan LM Studio / Ollama for available models.

        Called by the GUI model dropdown to populate the local model list.
        This may take a few seconds if tool-calling verification is enabled.

        Returns:
            List of model dicts: [{name, display_name, provider, supports_tools, size_hint, ...}]
            Empty list if no servers are reachable.
        """
        logger.debug("[ BRIDGE ] : Scanning for local AI models...")
        return self._execute_command(DiscoverLocalModelsCommand(), timeout=120.0, default_fallback=[])

    def set_active_model(self, model_info):
        """
        Switch to a model (local or cloud).

        Args:
            model_info: dict — if model_info['mode'] == 'cloud', switches back to Gemini.
                        Otherwise treats as LocalModelInfo dict.

        Returns:
            True on success, False on failure.
        """
        # JSON can arrive as a string from JS
        if isinstance(model_info, str):
            import json
            try:
                model_info = json.loads(model_info)
            except Exception:
                return False

        is_cloud = model_info.get("mode") == "cloud" or not model_info.get("name")
        result = self._execute_command(SetActiveModelCommand(model_info=model_info), timeout=30.0, default_fallback=False)
        
        # Support both boolean (legacy/cloud) and dict (new/local) results
        if isinstance(result, bool):
            success = result
            warning = None
            error = None
        else:
            result_dict = result or {}
            success = result_dict.get("success", False)
            warning = result_dict.get("warning")
            error = result_dict.get("error")

        if success:
            # Sync client-side config so local polling matches server immediately
            ConfigManager.load_config()
            window = self.main_window_ref()
            if window:
                if warning == "model_not_loaded":
                    model_name = result_dict.get("model_name", "Selected model") if isinstance(result, dict) else "Selected model"
                    window.set_thinking_text(f"⚠️ {model_name} not loaded in local server")
                else:
                    model_label = model_info.get("display_name", model_info.get("name", "Cloud" if is_cloud else "Local"))
                    window.set_thinking_text(f"Switched to {model_label}")
        
        elif error:
            window = self.main_window_ref()
            if window:
                window.set_thinking_text(f"❌ Failed to switch model: {error}")

        return success

    def get_active_model_info(self):
        """
        Return the currently active model info for the GUI status bar.

        Returns:
            Dict: {mode, name, provider, supports_tools, thinking_enabled, model_settings}
            model_settings contains the full per-model generation config (thinking,
            reasoning_effort, thinking_budget, top_p, top_k, repeat_penalty).
        """
        try:
            if ConfigManager.is_local_model_active():
                cfg = ConfigManager.get_local_model_config()
                model_id = cfg.get("name", "")
                # Full per-model settings from config.yaml local_models.model_settings
                local_cfg = ConfigManager.get("local_models") or {}
                model_settings = dict((local_cfg.get("model_settings") or {}).get(model_id, {}))
                thinking_val = model_settings.get("thinking")
                thinking_enabled = bool(thinking_val) if thinking_val is not None else None
                return {
                    "mode": "local",
                    "name": model_id or "Choose a model",
                    "provider": cfg.get("provider", "lmstudio"),
                    "supports_tools": cfg.get("supports_tools", True),
                    "thinking_enabled": thinking_enabled,
                    "model_settings": model_settings,
                }
        except Exception:
            pass

        try:
            name = ConfigManager.get_active_model()
        except Exception:
            name = "Cloud Model"

        return {"mode": "cloud", "name": name, "provider": "cloud", "supports_tools": True,
                "thinking_enabled": None, "model_settings": {}}

    def update_model_settings(self, model_id: str, settings: dict) -> bool:
        """
        Persist per-model generation settings to config.yaml under
        local_models.model_settings.<model_id> and hot-reload ConfigManager.

        Called by the Model Settings UI when the user saves changes.

        Args:
            model_id: Exact LM Studio model ID (e.g. "qwen/qwen3.6-35b-a3b")
            settings: Dict of overrides. Keys: thinking, reasoning_effort, thinking_budget,
                      top_p, top_k, repeat_penalty.  Pass null/None to remove a key.

        Returns:
            True on success.
        """
        import json
        try:
            if isinstance(settings, str):
                settings = json.loads(settings)

            from src.infrastructure.repositories.config_repository import ConfigRepository
            repo = ConfigRepository()
            full_config = repo.load("config", use_cache=False)

            local_models = full_config.setdefault("local_models", {})
            model_settings_map = local_models.setdefault("model_settings", {})
            existing = dict(model_settings_map.get(model_id, {}))

            # Merge: None values remove the key; everything else is set
            for k, v in settings.items():
                if v is None:
                    existing.pop(k, None)
                else:
                    existing[k] = v

            if existing:
                model_settings_map[model_id] = existing
            else:
                # Remove the entry entirely if nothing is left
                model_settings_map.pop(model_id, None)

            success = repo.save("config", full_config, update_cache=True)
            if success:
                ConfigManager.load_config()
                logger.info(f"[ BRIDGE ] Model settings updated for '{model_id}': {existing}")
            return success
        except Exception as e:
            logger.error(f"update_model_settings failed: {e}")
            return False

    def get_mcp_servers(self):
        """Return all configured MCP servers with status for the settings UI."""
        try:
            return self._execute_command(GetMCPServersCommand(), timeout=5.0, default_fallback=[])
        except Exception as e:
            logger.error(f"get_mcp_servers failed: {e}")
            return []

    def add_mcp_server(self, name, command, env, extra=None):
        """Add a new MCP server and hot-reload."""
        import json
        try:
            parsed_command = command if isinstance(command, list) else json.loads(command)
            parsed_env = env if isinstance(env, dict) else json.loads(env or '{}')
            parsed_extra = extra if isinstance(extra, dict) else json.loads(extra or '{}')
        except Exception as e:
            logger.error(f"Failed to parse MCP server parameters: {e}")
            return False

        return self._execute_command(
            AddMCPServerCommand(name=name, command=parsed_command, env=parsed_env, extra=parsed_extra),
            timeout=10.0,
            default_fallback=False
        )

    def update_mcp_server(self, name, command, env, extra=None):
        """Update an existing MCP server config and hot-reload."""
        return self.add_mcp_server(name, command, env, extra)

    def delete_mcp_server(self, name):
        """Remove an MCP server and hot-reload."""
        return self._execute_command(
            DeleteMCPServerCommand(name=name),
            timeout=10.0,
            default_fallback=False
        )

    def toggle_mcp_server(self, name, enabled):
        """Enable or disable an MCP server and hot-reload."""
        return self._execute_command(
            ToggleMCPServerCommand(name=name, enabled=bool(enabled)),
            timeout=10.0,
            default_fallback=False
        )

    def test_mcp_connection(self, name, command, env, extra=None):
        """Test an MCP server connection without saving. Returns {success, tools, error}."""
        import json
        try:
            parsed_command = command if isinstance(command, list) else json.loads(command)
            parsed_env = env if isinstance(env, dict) else json.loads(env or '{}')
            parsed_extra = extra if isinstance(extra, dict) else json.loads(extra or '{}')
        except Exception as e:
            return {"success": False, "error": str(e)}
        connect_timeout = float(parsed_extra.get('connect_timeout_seconds', 8))
        cmd_timeout = max(20.0, connect_timeout + 10.0)
        return self._execute_command(
            TestMCPConnectionCommand(
                name=name or "test-server",
                command=parsed_command,
                env=parsed_env,
                extra=parsed_extra or None,
            ),
            timeout=cmd_timeout,
            default_fallback={"success": False, "error": "Command system not available"}
        )

    def get_native_connectors(self):
        """Return native connector status (Gmail, Telegram)."""
        from src.application.commands import GetNativeConnectorsCommand
        return self._execute_command(
            GetNativeConnectorsCommand(),
            timeout=5.0,
            default_fallback=[],
        )

    def save_telegram_connector(self, bot_token):
        """Validate and save Telegram bot token."""
        from src.application.commands import SaveTelegramConnectorCommand
        return self._execute_command(
            SaveTelegramConnectorCommand(bot_token=bot_token),
            timeout=20.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def save_canvas_connector(self, base_url, token, contact_email=""):
        """Validate and save Canvas LMS credentials."""
        from src.application.commands import SaveCanvasConnectorCommand
        return self._execute_command(
            SaveCanvasConnectorCommand(
                base_url=base_url,
                token=token,
                contact_email=contact_email or "",
            ),
            timeout=30.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def save_stock_images_connector(self, pexels_key="", unsplash_key="", pixabay_key=""):
        """Validate and save optional stock-image API keys for search_images."""
        from src.application.commands import SaveStockImagesConnectorCommand
        return self._execute_command(
            SaveStockImagesConnectorCommand(
                pexels_key=pexels_key or "",
                unsplash_key=unsplash_key or "",
                pixabay_key=pixabay_key or "",
            ),
            timeout=30.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def save_finance_connector(self, client_id="", secret="", env="sandbox"):
        """Validate and save Plaid credentials for Finance connector."""
        from src.application.commands import SaveFinanceConnectorCommand
        return self._execute_command(
            SaveFinanceConnectorCommand(
                client_id=client_id or "",
                secret=secret or "",
                env=env or "sandbox",
            ),
            timeout=30.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def create_finance_link_token(self, user_id="chad-user"):
        """Generate a Plaid Link token for interactive bank connection."""
        from src.application.commands import CreateFinanceLinkTokenCommand
        return self._execute_command(
            CreateFinanceLinkTokenCommand(user_id=user_id or "chad-user"),
            timeout=20.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def exchange_finance_token(self, public_token, metadata=None):
        """Exchange public token from Plaid Link for persistent access token."""
        from src.application.commands import ExchangeFinanceTokenCommand
        return self._execute_command(
            ExchangeFinanceTokenCommand(public_token=public_token, metadata=metadata),
            timeout=30.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def list_finance_institutions(self):
        """List linked financial institutions."""
        from src.application.commands import ListFinanceInstitutionsCommand
        return self._execute_command(
            ListFinanceInstitutionsCommand(),
            timeout=10.0,
            default_fallback={"success": False, "institutions": []},
        )

    def disconnect_finance_institution(self, item_id):
        """Disconnect a linked financial institution by item_id."""
        from src.application.commands import DisconnectFinanceInstitutionCommand
        return self._execute_command(
            DisconnectFinanceInstitutionCommand(item_id=item_id),
            timeout=20.0,
            default_fallback={"success": False, "error": "Command system not available"},
        )

    def enable_native_connector(self, connector_id):
        """Enable Gmail/Telegram after successful setup."""
        from src.application.commands import EnableNativeConnectorCommand
        return self._execute_command(
            EnableNativeConnectorCommand(connector_id=connector_id),
            timeout=10.0,
            default_fallback=False,
        )

    def delete_native_connector(self, connector_id):
        """Disconnect a native connector."""
        from src.application.commands import DeleteNativeConnectorCommand
        return self._execute_command(
            DeleteNativeConnectorCommand(connector_id=connector_id),
            timeout=10.0,
            default_fallback=False,
        )

    def get_subagents(self):
        """Return all specialized subagents with status for the settings UI."""
        return self._execute_command(GetSubagentsCommand(), timeout=5.0, default_fallback=[])

    def toggle_subagent(self, name, enabled):
        """Enable or disable a specialized subagent."""
        return self._execute_command(
            ToggleSubagentCommand(name=name, enabled=bool(enabled)), timeout=10.0, default_fallback=False
        )

    def set_subagent_model(self, name, model_info):
        """Set (model_info dict) or clear (model_info=None) a per-subagent model override."""
        model_info = model_info or {}
        return self._execute_command(
            SetSubagentModelCommand(
                name=name,
                model_name=model_info.get("model_name"),
                provider=model_info.get("provider"),
                thinking_level=model_info.get("thinking_level"),
            ),
            timeout=10.0,
            default_fallback=False
        )

    def get_ui_preferences(self):
        """Get UI preferences from config."""
        prefs = ConfigManager.get("ui", {})
        print(f"[ WEBVIEW ] Providing UI preferences: {prefs}")
        return prefs
    
    def save_ui_preference(self, key, value):
        """Save a UI preference."""
        print(f"[ WEBVIEW ] Saving UI preference: {key} = {value}")
        res = self._config_repo.update_preference(f"ui.{key}", value)
        if res:
            # Sync client-side config so it reflects immediately in get_ui_preferences
            ConfigManager.load_config()
        return res

    def force_consolidation(self, conversation_id: str = "default") -> bool:
        """
        Force manual context consolidation from WebView.
        """
        logger.info(f"[ BRIDGE ] WebView requested manual consolidation for session {conversation_id}")
        return self._execute_command(
            ForceConsolidationCommand(conversation_id=conversation_id),
            timeout=30.0,
            default_fallback=False
        )

    def get_routine_builder_model(self):
        """Supervisor model used for routine drafting."""
        return self._execute_command(
            GetRoutineBuilderModelCommand(),
            timeout=5.0,
            default_fallback={"model": ConfigManager.get_routine_builder_model()},
        )

    def set_routine_builder_model(self, model_id):
        """Legacy endpoint: only the current supervisor model is accepted."""
        if not model_id or not str(model_id).strip():
            return False
        return self._execute_command(
            SetRoutineBuilderModelCommand(model_id=str(model_id).strip()),
            timeout=5.0,
            default_fallback=False,
        )

    def get_browser_safety_settings(self):
        """Return browser_agent approval mode and write-allowlist."""
        return self._execute_command(
            GetBrowserSafetyCommand(),
            timeout=5.0,
            default_fallback={"approval_mode": "allow_all", "allowlist": []},
        )

    def set_browser_safety_settings(self, approval_mode=None, allowlist=None):
        """Persist browser_agent approval mode and/or write-allowlist."""
        return self._execute_command(
            SetBrowserSafetyCommand(approval_mode=approval_mode, allowlist=allowlist),
            timeout=10.0,
            default_fallback=False,
        )

    def get_presenton_settings(self):
        """Return Presenton sidecar URL, LLM endpoint, and model."""
        return self._execute_command(
            GetPresentonSettingsCommand(),
            timeout=5.0,
            default_fallback={
                "enabled": False,
                "url": "",
                "llm_url": "",
                "model": "",
                "use_async": False,
            },
        )

    def set_presenton_settings(
        self,
        enabled=None,
        url=None,
        llm_url=None,
        model=None,
        use_async=None,
        restart_sidecar=False,
    ):
        """Persist settings; connect Docker only when explicitly requested."""
        return self._execute_command(
            SetPresentonSettingsCommand(
                enabled=enabled,
                url=url,
                llm_url=llm_url,
                model=model,
                use_async=use_async,
                restart_sidecar=restart_sidecar,
            ),
            timeout=120.0,
            default_fallback={"success": False},
        )

    def get_chad_code_settings(self):
        """Return Chad Code (OpenHands harness) settings for Settings → Services (#574)."""
        return self._execute_command(
            GetChadCodeSettingsCommand(),
            timeout=5.0,
            default_fallback={
                "coder_model": "",
                "max_iterations": 30,
                "job_timeout_s": 1800,
                "use_task_tracker": False,
                "native_tool_calling": False,
                "use_condenser": False,
                "use_critic": False,
                "context7_mcp": True,
                "github_mcp": False,
                "stream_events": True,
            },
        )

    def set_chad_code_settings(
        self,
        coder_model=None,
        max_iterations=None,
        job_timeout_s=None,
        use_task_tracker=None,
        native_tool_calling=None,
        use_condenser=None,
        use_critic=None,
        context7_mcp=None,
        github_mcp=None,
        stream_events=None,
    ):
        """Persist Chad Code (OpenHands harness) settings (#574)."""
        return self._execute_command(
            SetChadCodeSettingsCommand(
                coder_model=coder_model,
                max_iterations=max_iterations,
                job_timeout_s=job_timeout_s,
                use_task_tracker=use_task_tracker,
                native_tool_calling=native_tool_calling,
                use_condenser=use_condenser,
                use_critic=use_critic,
                context7_mcp=context7_mcp,
                github_mcp=github_mcp,
                stream_events=stream_events,
            ),
            timeout=10.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )

    def chad_code_preflight(self):
        """Run the Chad Code harness preflight (Docker + image + LM Studio + model), #574."""
        return self._execute_command(
            ChadCodePreflightCommand(),
            timeout=45.0,
            default_fallback={"ready": False, "error": "Command execution failed.", "checks": {}},
        )

    def chad_code_job_status(self, job_id):
        """Report a dispatched coding job's status/files/transcript (#574)."""
        return self._execute_command(
            ChadCodeJobStatusCommand(job_id=job_id),
            timeout=30.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )

    def get_searxng_settings(self):
        """Return SearXNG sidecar URL and active search backend."""
        return self._execute_command(
            GetSearxngSettingsCommand(),
            timeout=5.0,
            default_fallback={"url": "http://127.0.0.1:8080", "search_backend": "searxng"},
        )

    def set_searxng_settings(self, search_backend=None):
        """Persist the search_backend picker (searxng/brave/serper/tavily/all)."""
        return self._execute_command(
            SetSearxngSettingsCommand(search_backend=search_backend),
            timeout=5.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )

    def get_search_quota(self):
        """Best-effort quota snapshot per configured paid search backend."""
        return self._execute_command(
            GetSearchQuotaCommand(),
            timeout=10.0,
            default_fallback={},
        )

    def start_searxng_sidecar(self):
        """Start the local SearXNG + valkey Docker sidecar (Settings → Services)."""
        return self._execute_command(
            StartSearxngSidecarCommand(),
            timeout=180.0,
            default_fallback={"success": False, "message": "Command execution failed."},
        )

    def get_supabase_settings(self):
        """Return Supabase connection status for Settings → Memory."""
        return self._execute_command(
            GetSupabaseSettingsCommand(),
            timeout=5.0,
            default_fallback={"configured": False, "url": "", "anon_key": "", "service_key": "", "db_url": ""},
        )

    def bootstrap_supabase(self, url, anon_key, service_key="", db_url=""):
        """Save Supabase credentials and run one-click provisioning (issue #403)."""
        return self._execute_command(
            BootstrapSupabaseCommand(
                url=str(url or ""),
                anon_key=str(anon_key or ""),
                service_key=str(service_key or ""),
                db_url=str(db_url or ""),
            ),
            timeout=600.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )

    def get_setup_status(self):
        """Whether the first-run setup wizard should be shown (issue #428)."""
        return self._execute_command(
            GetSetupStatusCommand(),
            timeout=10.0,
            # Unknown while the Brain boots; do not falsely report setup complete.
            default_fallback={},
        )

    def start_lmstudio_setup(self):
        """Kick off the LM Studio model check/download in a background thread."""
        return self._execute_command(
            StartLMStudioSetupCommand(),
            timeout=10.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )

    def get_lmstudio_setup_progress(self):
        """Poll the state of a background start_lmstudio_setup() job."""
        return self._execute_command(
            GetLMStudioSetupProgressCommand(),
            timeout=10.0,
            default_fallback={"running": False, "done": True, "log": [], "report": None},
        )

    def probe_local_backend(self, provider, base_url, api_key=""):
        """Probe an Ollama / custom OpenAI-compatible endpoint for the setup wizard (issue #499)."""
        return self._execute_command(
            ProbeLocalBackendCommand(provider=str(provider or ""), base_url=str(base_url or ""), api_key=str(api_key or "")),
            timeout=10.0,
            default_fallback={"reachable": False, "models": [], "download_url": None},
        )

    def save_provider_choice(self, provider, base_url, api_key, model_id, supports_tools=True):
        """Persist the wizard's chosen backend + model (issue #499)."""
        return self._execute_command(
            SaveProviderChoiceCommand(
                provider=str(provider or ""),
                base_url=str(base_url or ""),
                api_key=str(api_key or ""),
                model_id=str(model_id or ""),
                supports_tools=bool(supports_tools),
            ),
            timeout=10.0,
            default_fallback={"success": False, "error": "Command execution failed."},
        )
