"""
Configuration Commands - Voice, Model, and Context settings
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from .base_command import Command
from src.shared.logger import setup_logger

logger = setup_logger("ConfigCommands")


@dataclass
class SetVoiceCommand(Command):
    """Set AI voice and reload models."""

    voice_name: str

    async def execute(self, facade):
        """
        Set voice name, update config, reload models.

        Args:
            facade: AssistantFacade instance

        Returns:
            bool: True if successful
        """
        # Validate voice
        allowed_voices = ['Puck', 'Charon', 'Kore', 'Fenrir', 'Aoede']
        if self.voice_name not in allowed_voices:
            logger.error(f"Invalid voice: {self.voice_name}. Allowed: {allowed_voices}")
            return False

        try:
            # Set voice via facade
            success = await facade.set_voice(self.voice_name)

            if success:
                logger.info(f"Voice changed to: {self.voice_name}")

            return success

        except Exception as e:
            logger.error(f"SetVoiceCommand failed: {e}", exc_info=True)
            return False


@dataclass
class SetModelCommand(Command):
    """Set text mode model and persist to config."""

    model_name: str

    async def execute(self, facade):
        """
        Set model name and update config.

        Args:
            facade: AssistantFacade instance

        Returns:
            bool: True if successful
        """
        try:
            # Set model via facade
            success = await facade.set_model(self.model_name)

            if success:
                logger.info(f"Model changed to: {self.model_name}")

            return success

        except Exception as e:
            logger.error(f"SetModelCommand failed: {e}", exc_info=True)
            return False


@dataclass
class GetContextStatusCommand(Command):
    """Get current context budget status."""

    async def execute(self, facade):
        """
        Get context status from consolidation manager.

        Args:
            facade: AssistantFacade instance

        Returns:
            dict: Context status with current_load, budget, profile, etc.
        """
        try:
            status = await facade.get_context_status()
            return status or {}

        except Exception as e:
            logger.error(f"GetContextStatusCommand failed: {e}", exc_info=True)
            return {}


@dataclass
class SetContextProfileCommand(Command):
    """Set context budget profile."""

    profile_name: str

    async def execute(self, facade):
        """
        Set context profile (aggressive/balanced/conservative).

        Args:
            facade: AssistantFacade instance

        Returns:
            bool: True if successful
        """
        try:
            success = await facade.set_context_profile(self.profile_name)

            if success:
                logger.info(f"Context profile changed to: {self.profile_name}")

            return success

        except Exception as e:
            logger.error(f"SetContextProfileCommand failed: {e}", exc_info=True)
            return False


@dataclass
class ForceConsolidationCommand(Command):
    """Force manual context consolidation."""
    conversation_id: str = "default"

    async def execute(self, facade):
        """
        Trigger manual consolidation.

        Args:
            facade: AssistantFacade instance

        Returns:
            bool: True if successful
        """
        try:
            success = await facade.force_consolidation(self.conversation_id)

            if success:
                logger.info(f"Manual consolidation triggered for session {self.conversation_id}")

            return success

        except Exception as e:
            logger.error(f"ForceConsolidationCommand failed: {e}", exc_info=True)
            return False


@dataclass
class DiscoverLocalModelsCommand(Command):
    """Command to scan and discover reachable local models."""

    async def execute(self, facade):
        try:
            return await facade.discover_local_models()
        except Exception as e:
            logger.error(f"DiscoverLocalModelsCommand failed: {e}", exc_info=True)
            return []


@dataclass
class SetActiveModelCommand(Command):
    """Command to set active cloud or local model."""

    model_info: dict

    async def execute(self, facade):
        try:
            is_cloud = self.model_info.get("mode") == "cloud" or not self.model_info.get("name")
            if is_cloud:
                # Support both legacy boolean return and unified result dictionary
                return await facade.set_cloud_model(model_id=self.model_info.get("name"))
            else:
                return await facade.set_local_model(self.model_info)
        except Exception as e:
            logger.error(f"SetActiveModelCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetSubagentsCommand(Command):
    """Get all specialized subagents with their enabled/disabled status."""

    async def execute(self, facade):
        try:
            return facade.get_subagents()
        except Exception as e:
            logger.error(f"GetSubagentsCommand failed: {e}", exc_info=True)
            return []


@dataclass
class ToggleSubagentCommand(Command):
    """Enable or disable a specialized subagent."""

    name: str
    enabled: bool

    async def execute(self, facade):
        try:
            return await facade.toggle_subagent(self.name, self.enabled)
        except Exception as e:
            logger.error(f"ToggleSubagentCommand failed: {e}", exc_info=True)
            return False


@dataclass
class SetSubagentModelCommand(Command):
    """Set (or clear) a per-subagent model override."""

    name: str
    model_name: Optional[str] = None
    provider: Optional[str] = None
    thinking_level: Optional[str] = None

    async def execute(self, facade):
        try:
            return await facade.set_subagent_model(
                self.name, self.model_name, self.provider, self.thinking_level
            )
        except Exception as e:
            logger.error(f"SetSubagentModelCommand failed: {e}", exc_info=True)
            return False


@dataclass
class GetMCPServersCommand(Command):
    """Command to get configured MCP servers with status."""

    async def execute(self, facade):
        try:
            return facade.get_mcp_servers()
        except Exception as e:
            logger.error(f"GetMCPServersCommand failed: {e}", exc_info=True)
            return []


@dataclass
class AddMCPServerCommand(Command):
    """Command to add or update an MCP server configuration."""

    name: str
    command: list
    env: dict
    extra: Optional[dict] = None

    async def execute(self, facade):
        try:
            return await facade.add_mcp_server(self.name, self.command, self.env, self.extra)
        except Exception as e:
            logger.error(f"AddMCPServerCommand failed: {e}", exc_info=True)
            return False


@dataclass
class DeleteMCPServerCommand(Command):
    """Command to remove an MCP server configuration."""

    name: str

    async def execute(self, facade):
        try:
            return await facade.delete_mcp_server(self.name)
        except Exception as e:
            logger.error(f"DeleteMCPServerCommand failed: {e}", exc_info=True)
            return False


@dataclass
class ToggleMCPServerCommand(Command):
    """Command to enable or disable an MCP server configuration."""

    name: str
    enabled: bool

    async def execute(self, facade):
        try:
            return await facade.toggle_mcp_server(self.name, self.enabled)
        except Exception as e:
            logger.error(f"ToggleMCPServerCommand failed: {e}", exc_info=True)
            return False


@dataclass
class TestMCPConnectionCommand(Command):
    """Test an MCP server connection without saving config."""

    name: str
    command: list
    env: dict
    extra: dict = None

    async def execute(self, facade):
        try:
            return await facade.test_mcp_connection(self.name, self.command, self.env, self.extra)
        except Exception as e:
            logger.error(f"TestMCPConnectionCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetNativeConnectorsCommand(Command):
    """Return native connector status for the settings UI."""

    async def execute(self, facade):
        try:
            return facade.get_native_connectors()
        except Exception as e:
            logger.error(f"GetNativeConnectorsCommand failed: {e}", exc_info=True)
            return []


@dataclass
class SaveTelegramConnectorCommand(Command):
    """Validate and save a Telegram bot token."""

    bot_token: str

    async def execute(self, facade):
        try:
            return await facade.save_telegram_connector(self.bot_token)
        except Exception as e:
            logger.error(f"SaveTelegramConnectorCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class SaveCanvasConnectorCommand(Command):
    """Validate and save Canvas LMS credentials."""

    base_url: str
    token: str
    contact_email: str = ""

    async def execute(self, facade):
        try:
            return await facade.save_canvas_connector(self.base_url, self.token, self.contact_email)
        except Exception as e:
            logger.error(f"SaveCanvasConnectorCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class SaveStockImagesConnectorCommand(Command):
    """Validate and save optional Pexels/Unsplash/Pixabay keys for search_images."""

    pexels_key: str = ""
    unsplash_key: str = ""
    pixabay_key: str = ""

    async def execute(self, facade):
        try:
            return await facade.save_stock_images_connector(
                self.pexels_key, self.unsplash_key, self.pixabay_key
            )
        except Exception as e:
            logger.error(f"SaveStockImagesConnectorCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class SaveFinanceConnectorCommand(Command):
    """Validate and save Plaid credentials for Finance connector."""

    client_id: str
    secret: str
    env: str = "sandbox"

    async def execute(self, facade):
        try:
            return await facade.save_finance_connector(self.client_id, self.secret, self.env)
        except Exception as e:
            logger.error(f"SaveFinanceConnectorCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class CreateFinanceLinkTokenCommand(Command):
    """Generate a temporary Plaid Link token for interactive bank connection."""

    user_id: str = "chad-user"

    async def execute(self, facade):
        try:
            from src.tools.finance.manager import FinanceManager
            return FinanceManager.get_instance().create_link_token(client_user_id=self.user_id)
        except Exception as e:
            logger.error(f"CreateFinanceLinkTokenCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class ExchangeFinanceTokenCommand(Command):
    """Exchange public token from Plaid Link for persistent access token."""

    public_token: str
    metadata: Optional[Dict[str, Any]] = None

    async def execute(self, facade):
        try:
            from src.tools.finance.manager import FinanceManager
            return FinanceManager.get_instance().exchange_public_token(
                public_token=self.public_token,
                metadata=self.metadata,
            )
        except Exception as e:
            logger.error(f"ExchangeFinanceTokenCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class ListFinanceInstitutionsCommand(Command):
    """List linked financial institutions and their accounts."""

    async def execute(self, facade):
        try:
            from src.tools.finance.manager import FinanceManager
            return {"success": True, "institutions": FinanceManager.get_instance().list_linked_institutions()}
        except Exception as e:
            logger.error(f"ListFinanceInstitutionsCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e), "institutions": []}


@dataclass
class DisconnectFinanceInstitutionCommand(Command):
    """Disconnect a linked financial institution by item_id."""

    item_id: str

    async def execute(self, facade):
        try:
            from src.tools.finance.manager import FinanceManager
            return FinanceManager.get_instance().disconnect_institution(self.item_id)
        except Exception as e:
            logger.error(f"DisconnectFinanceInstitutionCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class EnableNativeConnectorCommand(Command):
    """Enable a native connector after credentials are verified."""

    connector_id: str

    async def execute(self, facade):
        try:
            return await facade.enable_native_connector(self.connector_id)
        except Exception as e:
            logger.error(f"EnableNativeConnectorCommand failed: {e}", exc_info=True)
            return False


@dataclass
class DeleteNativeConnectorCommand(Command):
    """Disable and disconnect a native connector."""

    connector_id: str

    async def execute(self, facade):
        try:
            return await facade.delete_native_connector(self.connector_id)
        except Exception as e:
            logger.error(f"DeleteNativeConnectorCommand failed: {e}", exc_info=True)
            return False


@dataclass
class GetBrowserSafetyCommand(Command):
    """Get browser_agent approval mode and write-allowlist for the Settings UI."""

    async def execute(self, facade):
        try:
            return facade.get_browser_safety_settings()
        except Exception as e:
            logger.error(f"GetBrowserSafetyCommand failed: {e}", exc_info=True)
            return {"approval_mode": "allow_all", "allowlist": []}


@dataclass
class SetBrowserSafetyCommand(Command):
    """Persist browser_agent approval mode and/or write-allowlist."""

    approval_mode: Optional[str] = None
    allowlist: Optional[list] = None

    async def execute(self, facade):
        try:
            return await facade.set_browser_safety_settings(
                approval_mode=self.approval_mode,
                allowlist=self.allowlist,
            )
        except Exception as e:
            logger.error(f"SetBrowserSafetyCommand failed: {e}", exc_info=True)
            return False


@dataclass
class GetPresentonSettingsCommand(Command):
    """Get Presenton sidecar settings for Settings → Services."""

    async def execute(self, facade):
        try:
            return facade.get_presenton_settings()
        except Exception as e:
            logger.error(f"GetPresentonSettingsCommand failed: {e}", exc_info=True)
            return {
                "enabled": False,
                "url": "",
                "llm_url": "",
                "model": "",
                "use_async": False,
            }


@dataclass
class SetPresentonSettingsCommand(Command):
    """Persist Presenton settings and optionally connect the guarded local sidecar."""

    enabled: Optional[bool] = None
    url: Optional[str] = None
    llm_url: Optional[str] = None
    model: Optional[str] = None
    use_async: Optional[bool] = None
    restart_sidecar: bool = False

    async def execute(self, facade):
        try:
            return await facade.set_presenton_settings(
                enabled=self.enabled,
                url=self.url,
                llm_url=self.llm_url,
                model=self.model,
                use_async=self.use_async,
                restart_sidecar=self.restart_sidecar,
            )
        except Exception as e:
            logger.error(f"SetPresentonSettingsCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetChadCodeSettingsCommand(Command):
    """Get Chad Code (OpenHands harness) settings for Settings → Services (issue #574)."""

    async def execute(self, facade):
        try:
            return facade.get_chad_code_settings()
        except Exception as e:
            logger.error(f"GetChadCodeSettingsCommand failed: {e}", exc_info=True)
            return {
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
            }


@dataclass
class SetChadCodeSettingsCommand(Command):
    """Persist Chad Code (OpenHands harness) settings (issue #574)."""

    coder_model: Optional[str] = None
    max_iterations: Optional[int] = None
    job_timeout_s: Optional[int] = None
    use_task_tracker: Optional[bool] = None
    native_tool_calling: Optional[bool] = None
    use_condenser: Optional[bool] = None
    use_critic: Optional[bool] = None
    context7_mcp: Optional[bool] = None
    github_mcp: Optional[bool] = None
    stream_events: Optional[bool] = None

    async def execute(self, facade):
        try:
            return facade.set_chad_code_settings(
                coder_model=self.coder_model,
                max_iterations=self.max_iterations,
                job_timeout_s=self.job_timeout_s,
                use_task_tracker=self.use_task_tracker,
                native_tool_calling=self.native_tool_calling,
                use_condenser=self.use_condenser,
                use_critic=self.use_critic,
                context7_mcp=self.context7_mcp,
                github_mcp=self.github_mcp,
                stream_events=self.stream_events,
            )
        except Exception as e:
            logger.error(f"SetChadCodeSettingsCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class ChadCodePreflightCommand(Command):
    """Run the Chad Code harness preflight check (Docker + image + LM Studio + model), #574."""

    async def execute(self, facade):
        try:
            return await facade.chad_code_preflight()
        except Exception as e:
            logger.error(f"ChadCodePreflightCommand failed: {e}", exc_info=True)
            return {"ready": False, "error": str(e), "checks": {}}


@dataclass
class ChadCodeJobStatusCommand(Command):
    """Report a dispatched coding job's status/files/transcript (issue #574)."""

    job_id: str = ""

    async def execute(self, facade):
        try:
            return await facade.chad_code_job_status(self.job_id)
        except Exception as e:
            logger.error(f"ChadCodeJobStatusCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetSearxngSettingsCommand(Command):
    """Get SearXNG sidecar settings for Settings → Services."""

    async def execute(self, facade):
        try:
            return facade.get_searxng_settings()
        except Exception as e:
            logger.error(f"GetSearxngSettingsCommand failed: {e}", exc_info=True)
            return {"url": "http://127.0.0.1:8080", "search_backend": "searxng"}


@dataclass
class SetSearxngSettingsCommand(Command):
    """Persist tools.search_backend (searxng/brave/serper/tavily/all), issue #523."""

    search_backend: Optional[str] = None

    async def execute(self, facade):
        try:
            return facade.set_searxng_settings(search_backend=self.search_backend)
        except Exception as e:
            logger.error(f"SetSearxngSettingsCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetSearchQuotaCommand(Command):
    """Best-effort quota snapshot per configured paid search backend (issue #523)."""

    async def execute(self, facade):
        try:
            return await facade.get_search_quota()
        except Exception as e:
            logger.error(f"GetSearchQuotaCommand failed: {e}", exc_info=True)
            return {}


@dataclass
class StartSearxngSidecarCommand(Command):
    """Start the local SearXNG + valkey Docker sidecar (one-click, issue #415)."""

    async def execute(self, facade):
        try:
            return await facade.start_searxng_sidecar()
        except Exception as e:
            logger.error(f"StartSearxngSidecarCommand failed: {e}", exc_info=True)
            return {"success": False, "message": str(e)}


@dataclass
class GetSupabaseSettingsCommand(Command):
    """Return Supabase connection status for Settings → Memory."""

    async def execute(self, facade):
        try:
            return facade.get_supabase_settings()
        except Exception as e:
            logger.error(f"GetSupabaseSettingsCommand failed: {e}", exc_info=True)
            return {"configured": False, "url": "", "anon_key": "", "service_key": "", "db_url": ""}


@dataclass
class BootstrapSupabaseCommand(Command):
    """Save Supabase credentials and run the one-click bootstrap (issue #403)."""

    url: str
    anon_key: str
    service_key: str = ""
    db_url: str = ""

    async def execute(self, facade):
        try:
            return await facade.bootstrap_supabase(
                url=self.url,
                anon_key=self.anon_key,
                service_key=self.service_key,
                db_url=self.db_url,
            )
        except Exception as e:
            logger.error(f"BootstrapSupabaseCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetSetupStatusCommand(Command):
    """Whether the first-run setup wizard should be shown (issue #428)."""

    async def execute(self, facade):
        try:
            return facade.get_setup_status()
        except Exception as e:
            logger.error(f"GetSetupStatusCommand failed: {e}", exc_info=True)
            return {"needs_setup": False, "lms_cli_found": False, "server_running": False}


@dataclass
class StartLMStudioSetupCommand(Command):
    """Kick off the LM Studio model check/download in a background thread (issue #428)."""

    async def execute(self, facade):
        try:
            return facade.start_lmstudio_setup()
        except Exception as e:
            logger.error(f"StartLMStudioSetupCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


@dataclass
class GetLMStudioSetupProgressCommand(Command):
    """Poll the state of a background start_lmstudio_setup() job (issue #428)."""

    async def execute(self, facade):
        try:
            return facade.get_lmstudio_setup_progress()
        except Exception as e:
            logger.error(f"GetLMStudioSetupProgressCommand failed: {e}", exc_info=True)
            return {"running": False, "done": True, "log": [], "report": None}


@dataclass
class ProbeLocalBackendCommand(Command):
    """Probe an Ollama / custom OpenAI-compatible endpoint for the setup wizard (issue #499)."""

    provider: str
    base_url: str
    api_key: str = ""

    async def execute(self, facade):
        try:
            return await facade.probe_local_backend(self.provider, self.base_url, self.api_key)
        except Exception as e:
            logger.error(f"ProbeLocalBackendCommand failed: {e}", exc_info=True)
            return {"reachable": False, "models": [], "download_url": None}


@dataclass
class SaveProviderChoiceCommand(Command):
    """Persist the wizard's chosen backend + model (issue #499)."""

    provider: str
    base_url: str
    api_key: str
    model_id: str
    supports_tools: bool = True

    async def execute(self, facade):
        try:
            return facade.save_provider_choice(
                self.provider, self.base_url, self.api_key, self.model_id, self.supports_tools
            )
        except Exception as e:
            logger.error(f"SaveProviderChoiceCommand failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

