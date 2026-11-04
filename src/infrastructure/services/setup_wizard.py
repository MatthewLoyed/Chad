"""First-run setup wizard — local-only LM Studio model setup (issue #428, revised scope).

Chad boots fine with zero secrets configured (see docs/setup/COLD_START.md),
but a completely fresh CHAD_HOME can't hold a real conversation without a
local model loaded. Chad doesn't use cloud LLMs at all in practice, so this
wizard is local-only: it drives LM Studio's `lms` CLI to check/download the
models Chad needs and confirm the local server is up, reusing the model
catalog and CLI primitives already built in scripts/setup/setup_lmstudio.py
(issue #419) rather than duplicating them. MCP connector setup (native
one-click connectors) is a separate wizard step handled by the existing
Connectors UI (issue #217); this module only owns the LM Studio step +
config-template bootstrap.
"""

from __future__ import annotations

import shutil
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict

from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("SetupWizard")

# scripts/ isn't an importable package (no __init__.py) — import setup_lmstudio.py
# directly by path so this reuses its REQUIRED_MODELS catalog + `lms` CLI wrapper
# functions instead of re-implementing them here.
_SCRIPTS_SETUP_DIR = get_project_root() / "scripts" / "setup"
if str(_SCRIPTS_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_SETUP_DIR))
import setup_lmstudio as _lms  # noqa: E402

_TEMPLATE_COPIES = (
    ("config/preferences.yaml.example", "config/preferences.yaml"),
    ("config/personal_knowledge.md.example", "config/personal_knowledge.md"),
    (".env.example", ".env"),
    ("CLAUDE.md.example", "CLAUDE.md"),
)


def bootstrap_config_if_needed() -> list[str]:
    """Python port of scripts/setup/bootstrap_config.ps1 — copy-if-missing only."""
    root = get_project_root()
    copied: list[str] = []
    for source_rel, dest_rel in _TEMPLATE_COPIES:
        source = root / source_rel
        dest = root / dest_rel
        if dest.exists() or not source.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, dest)
        copied.append(dest_rel)
    if copied:
        logger.info(f"[ SETUP ] Instantiated config template(s): {', '.join(copied)}")
    return copied


def _uses_existing_dev_profile() -> bool:
    """Legacy installed preferences count as setup only when explicitly shared in dev.

    A normal clean install still needs an explicit model choice. Merely setting
    CHAD_MAIN_ROOT must not hide onboarding in an unrelated/fresh checkout.
    """
    main = os.environ.get("CHAD_MAIN_ROOT")
    if os.environ.get("CHAD_DEV_WORKTREE") != "1" or not main:
        return False
    try:
        return (get_project_root() / "config/preferences.yaml").samefile(
            Path(main) / "config/preferences.yaml"
        )
    except OSError:
        return False


def get_setup_status() -> Dict[str, Any]:
    """Return whether the first-run wizard should be shown, and current LM Studio state."""
    bootstrap_config_if_needed()

    from src.shared.config_manager import ConfigManager
    selected = ConfigManager.get_local_model_config()
    # Older installers copied a sample LM Studio model into preferences. That
    # alone is not a user choice. Modern picker writes both model aliases.
    explicit_lmstudio = _uses_existing_dev_profile() or bool(ConfigManager.get("setup.model_selected", False)) or isinstance(
        ConfigManager.get("local_models.supervisor"), dict
    )
    if ((selected.get("provider") in {"static", "ollama"}
            or (selected.get("provider") == "lmstudio" and explicit_lmstudio))
            and selected.get("name") and selected.get("base_url")):
        # Setup records a choice, not server liveness. A stopped custom server
        # should produce a connection error, not restart LM Studio onboarding.
        return {"needs_setup": False, "backend_configured": True,
                "provider": selected["provider"]}

    lms_path = _lms.find_lms_cli()
    # Chat needs one chosen model, not the entire specialist model catalog.
    # A working HTTP server does not require the lms command to be on PATH.
    server_running = _lms.is_server_running()
    app_path = _lms.find_lmstudio_app()

    return {
        "needs_setup": True,
        "lmstudio_installed": bool(app_path or lms_path or server_running),
        "lms_cli_found": lms_path is not None,
        "server_running": server_running,
        "lmstudio_download_url": _lms.LMSTUDIO_DOWNLOAD_URL,
    }


# ── Incremental LM Studio setup job ──────────────────────────────────────────
# setup_lmstudio.run() is synchronous/interactive (built for CLI use, issue
# #419) and only returns a final Report once every model has been attempted.
# The GUI needs incremental progress instead, so this drives the same
# find_lms_cli/list_downloaded_models/download_model/start_server primitives
# from a background thread and exposes a pollable job state.

_job_lock = threading.Lock()
_job_state: Dict[str, Any] = {"running": False, "done": False, "log": [], "report": None}


def _job_log(msg: str) -> None:
    with _job_lock:
        _job_state["log"].append(msg)
    logger.info(f"[ SETUP ] {msg}")


def _run_lmstudio_job() -> None:
    report = _lms.Report()
    lms_path = _lms.find_lms_cli()
    report.lms_cli_found = lms_path is not None

    if not lms_path:
        report.errors.append(
            f"`lms` CLI not found on PATH. Install LM Studio ({_lms.LMSTUDIO_DOWNLOAD_URL}), "
            "open it once (this adds `lms` to PATH), then try again."
        )
        _job_log("LM Studio not found.")
        with _job_lock:
            _job_state["report"] = report.as_dict()
            _job_state["running"] = False
            _job_state["done"] = True
        return

    downloaded = _lms.list_downloaded_models(lms_path)
    for spec in _lms.REQUIRED_MODELS:
        if _lms._is_already_present(spec.model_id, downloaded):
            report.models_present.append(spec.model_id)
            _job_log(f"{spec.model_id} already downloaded.")
            continue
        _job_log(f"Downloading {spec.model_id} (~{spec.approx_vram_gb}GB, {spec.role})…")
        ok, err = _lms.download_model(lms_path, spec.model_id)
        if ok:
            report.models_downloaded.append(spec.model_id)
            _job_log(f"{spec.model_id} ready.")
        else:
            report.models_missing.append(spec.model_id)
            report.errors.append(f"Failed to download {spec.model_id}: {err}")
            _job_log(f"{spec.model_id} failed: {err}")

    report.server_running = _lms.is_server_running()
    if not report.server_running:
        _job_log("Starting LM Studio local server…")
        report.server_started_by_us = _lms.start_server(lms_path)
        report.server_running = report.server_started_by_us
        if not report.server_started_by_us:
            report.errors.append("Could not start the LM Studio local server.")
            _job_log("Could not start the LM Studio local server.")
        else:
            _job_log("LM Studio server running.")

    _job_log("Setup finished." if report.ok else "Setup finished with errors.")
    with _job_lock:
        _job_state["report"] = report.as_dict()
        _job_state["running"] = False
        _job_state["done"] = True


def start_lmstudio_setup() -> Dict[str, Any]:
    """Kick off the LM Studio model check/download in a background thread."""
    with _job_lock:
        if _job_state["running"]:
            return {"success": False, "error": "Setup is already running."}
        _job_state["running"] = True
        _job_state["done"] = False
        _job_state["log"] = []
        _job_state["report"] = None
    threading.Thread(target=_run_lmstudio_job, daemon=True, name="lmstudio-setup").start()
    return {"success": True}


def get_lmstudio_setup_progress() -> Dict[str, Any]:
    """Poll the current state of a background start_lmstudio_setup() job."""
    with _job_lock:
        return {
            "running": _job_state["running"],
            "done": _job_state["done"],
            "log": list(_job_state["log"]),
            "report": _job_state["report"],
        }


# ── Backend chooser (issue #499, epic #450) ──────────────────────────────────
# LM Studio keeps the one-click `lms` CLI flow above unchanged. For Ollama / any
# other OpenAI-compatible endpoint (llama.cpp server, vLLM), Chad doesn't own or
# script those servers — it only probes what's already running and lets the user
# pick a model from what it finds, per the "detect + manual guidance" approach.

_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"


async def probe_local_backend(provider: str, base_url: str, api_key: str = "") -> Dict[str, Any]:
    """Probe a local OpenAI-compatible endpoint and list its chat-capable models.

    ``provider`` is ``"ollama"`` (uses the richer LocalModelDiscovery path, which
    applies the non-chat-model filter + tool-support heuristics) or ``"custom"``
    (any other OpenAI-compatible endpoint — llama.cpp server, vLLM, remote) which
    only does a bare ``GET /v1/models`` listing. Never raises; a down/unreachable
    server just yields an empty model list.
    """
    provider = (provider or "custom").strip().lower()
    base_url = (base_url or "").strip().rstrip("/") or _OLLAMA_DEFAULT_BASE_URL

    if provider == "ollama":
        from src.core.intelligence.local_model_discovery import get_local_model_discovery

        discovery = get_local_model_discovery()
        models = await discovery.discover_ollama(
            base_url=base_url, api_key=api_key or "ollama", verify_tools=False
        )
        return {
            "reachable": bool(models),
            "models": [
                {"id": m.name, "display_name": m.display_name, "supports_tools": m.supports_tools}
                for m in models
            ],
            "download_url": _OLLAMA_DOWNLOAD_URL,
        }

    import httpx
    from src.core.tools.failure import failure, AUTH, UNAVAILABLE
    from src.core.tools.limits import get as limit
    from src.core.intelligence.local_model_discovery import NON_CHAT_MODEL_TERMS

    try:
        async with httpx.AsyncClient(timeout=limit("hardware_probe_timeout")) as client:
            response = await client.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key or 'not-needed'}"})
        if response.status_code in {401, 403}:
            return {"reachable": True, "models": [], **failure("The provider rejected access. Check your API key and model permissions.", failure_class=AUTH)}
        if response.status_code in {404, 405}:
            return {"reachable": True, "models": [], "detail": "This endpoint does not offer model discovery. Enter the model ID from your provider and test it below."}
        response.raise_for_status()
        entries = response.json().get("data", [])
        return {"reachable": True, "models": [
            {"id": entry["id"], "display_name": entry["id"], "supports_tools": False,
             "tools_verified": False}
            for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            and not any(term in entry["id"].lower() for term in NON_CHAT_MODEL_TERMS)
        ], "download_url": None}
    except Exception:
        return {"reachable": False, "models": [], **failure("Model discovery could not finish. Check the address, server and account, or enter a model ID to test directly.", failure_class=UNAVAILABLE)}



def save_provider_choice(
    provider: str, base_url: str, api_key: str, model_id: str, supports_tools: bool = True, *, extra_updates=None
) -> Dict[str, Any]:
    """Persist the wizard's backend + model pick to preferences.yaml.

    Writes ``local_models.providers.<provider>`` (so future ``LocalModelDiscovery``
    scans keep finding this endpoint) and ``local_models.active_model`` (so the very
    next turn uses it — mirrors the shape ``AssistantFacade.set_local_model`` writes
    for the post-setup Settings model picker).
    """
    if not model_id:
        return {"success": False, "error": "No model selected."}

    provider = (provider or "custom").strip().lower()
    base_url = (base_url or "").strip().rstrip("/")
    provider_key = provider if provider in {"ollama", "lmstudio"} else "static"

    from src.infrastructure.repositories import ConfigRepository
    from src.shared.config_manager import ConfigManager

    config_repo = ConfigRepository()
    active_model = {
        "name": model_id,
        "hosting": "cloud" if provider == "cloud" else "unknown",
        "provider": provider_key,
        "base_url": base_url,
        "api_key": api_key or "",
        "supports_tools": supports_tools,
        "tools_verified": False,
    }
    update = {"setup": {"model_selected": True, "share_main_model": True},
              "tools": {"llm_selector_model": model_id, "llm_selector_context_length": 8192},
              "intent_router": {"enabled": False},
              "local_models": {
        "use_local": True, "active_model": active_model, "supervisor": active_model,
        "providers": {provider_key: {"base_url": base_url, "api_key": api_key or "", "enabled": True}},
    }}
    if extra_updates:
        ConfigManager._recursive_update(update, extra_updates)
    preferences = config_repo.load("preferences", use_cache=False)
    ConfigManager._recursive_update(preferences, update)
    if not config_repo.save("preferences", preferences, update_cache=True):
        return {"success": False, "error": "Your model choice could not be saved. Check that Chad can write its settings, then try again."}

    # Keep in-memory config in sync so a subsequent get_setup_status()/discovery call
    # in the same process sees the choice immediately (mirrors set_local_model).
    ConfigManager._recursive_update(
        ConfigManager._config,
        update,
    )

    logger.info(f"[ SETUP ] Backend chosen: {provider_key} / '{model_id}' at {base_url}")
    return {"success": True}
