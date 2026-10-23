"""Desktop first-run setup bridge and synchronization of saved settings."""
from src.shared.config_manager import ConfigManager
from src.application.commands import (
    GetSetupStatusCommand,
    StartLMStudioSetupCommand,
    GetLMStudioSetupProgressCommand,
    ProbeLocalBackendCommand,
    TestSetupModelCommand,
    SaveProviderChoiceCommand,
)


class SetupAPIMixin:
    """Relay setup commands and refresh the desktop cache after a saved choice."""

    def test_setup_model(self, base_url, api_key, model_id, confirmed=False):
        from src.core.tools.limits import get as limit
        from src.core.tools.failure import failure, UNAVAILABLE
        return self._execute_command(
            TestSetupModelCommand(str(base_url or ""), str(api_key or ""), str(model_id or ""), confirmed),
            timeout=limit("setup_test_timeout") + limit("setup_network_timeout"),
            default_fallback=failure("The local service did not finish the chat test. Try again.", failure_class=UNAVAILABLE),
        )

    def assess_model_hardware(self, storage_path="", context=8192, manual=None):
        """Scan the local Limb host; deliberately never send hardware to the Brain."""
        from src.infrastructure.services.hardware_setup import assess

        return assess(storage_path, context, manual)

    def start_model_setup_job(self, operation, options, confirmed=False):
        from src.infrastructure.services.setup_assets import start

        return start(operation, options, confirmed)

    def get_model_setup_job(self):
        from src.infrastructure.services.setup_assets import progress

        return progress()

    def cancel_model_setup_job(self):
        from src.infrastructure.services.setup_assets import cancel

        return cancel()

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
        result = self._execute_command(
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
        if isinstance(result, dict) and result.get("success"):
            # The Brain saved preferences in another process. Refresh the GUI's
            # cache before its startup/model polling can overwrite the new choice.
            ConfigManager.load_config()
        return result
