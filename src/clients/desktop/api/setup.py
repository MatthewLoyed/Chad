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

        result = assess(storage_path, context, manual)
        self._setup_plans = {p["id"]: p for p in result.get("bundles", [])}
        self._setup_storage = storage_path
        return result

    def test_setup_bundle(self, bundle_id, base_url, api_key, model_id, confirmed=False):
        """Only selected role settings cross the bridge; local hardware stays here."""
        from src.infrastructure.services.setup_inventory import installed_assets
        from src.application.commands import CheckSetupBundleCommand
        from src.core.tools.failure import failure, BAD_INPUT, UNAVAILABLE
        from src.core.tools.limits import get as limit
        plan = getattr(self, "_setup_plans", {}).get(bundle_id)
        if not plan or not plan["fits"]:
            return failure("Rescan and choose a compatible model bundle first.", failure_class=BAD_INPUT)
        if confirmed is not True:
            return failure("Confirm the model-role test first; models may load or be evicted.", failure_class=BAD_INPUT)
        from urllib.parse import urlsplit
        try:
            local_host = urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        except ValueError:
            local_host = False
        if not local_host:
            return failure("This scan describes this computer. Use manual Chat setup for a remote server; its hardware and installed files need their own assessment.", failure_class=BAD_INPUT)
        installed = installed_assets(getattr(self, "_setup_storage", ""), verify=True)
        assets = {a["catalog_id"]: a for a in plan["assets"]}
        if any(k not in installed for k in assets):
            return failure("Prepare the recommended setup first. Every bundle file must match its pinned checksum before reuse; check the model folder or choose manual Chat setup.", failure_class=BAD_INPUT)
        keys = {k: installed.get(k, a["model_key"]) for k, a in assets.items()}
        if keys[plan["primary"]] != model_id:
            return failure("The selected main model differs from the bundle. Refresh LM Studio after downloading, or choose manual Chat setup.", failure_class=BAD_INPUT)
        roles = {"secondary": keys[plan["secondary"]],
                 "selector": keys.get(plan["selector"]), "context": plan["context"],
                 "residency": plan["residency"],
                 "runtime_budget_gb": plan["runtime_budget_gb"],
                 "estimates": {keys[k]: a["required_gb"] for k, a in assets.items()}}
        return self._execute_command(CheckSetupBundleCommand(base_url, api_key, model_id, roles, confirmed),
            timeout=limit("setup_bundle_timeout"),
            default_fallback=failure("The model-role check did not finish. Retry or use manual Chat setup.", failure_class=UNAVAILABLE))

    def save_setup_bundle(self, base_url, api_key, model_id, token, chat_only=False):
        from src.application.commands import SaveSetupBundleCommand
        result = self._execute_command(SaveSetupBundleCommand(base_url, api_key, model_id, token, chat_only),
            timeout=10.0, default_fallback={"success": False})
        if result.get("success"):
            ConfigManager.load_config()
        return result

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
        result = self._execute_command(
            ProbeLocalBackendCommand(provider=str(provider or ""), base_url=str(base_url or ""), api_key=str(api_key or "")),
            timeout=10.0,
            default_fallback={"reachable": False, "models": [], "download_url": None},
        )
        from src.infrastructure.services.setup_inventory import installed_assets
        plans = getattr(self, "_setup_plans", {})
        if plans:
            installed = installed_assets(getattr(self, "_setup_storage", ""))
            result["bundle_models"] = {k: installed.get(p["primary"], next(a["model_key"] for a in p["assets"] if a["catalog_id"] == p["primary"])) for k, p in plans.items()}
        return result

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
