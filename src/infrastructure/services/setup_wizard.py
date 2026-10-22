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
import sys
import threading
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


def get_setup_status() -> Dict[str, Any]:
    """Return whether the first-run wizard should be shown, and current LM Studio state."""
    bootstrap_config_if_needed()

    from src.shared.config_manager import ConfigManager
    selected = ConfigManager.get_local_model_config()
    if (selected.get("provider") in {"static", "ollama"}
            and selected.get("name") and selected.get("base_url")):
        # Setup records a choice, not server liveness. A stopped custom server
        # should produce a connection error, not restart LM Studio onboarding.
        return {"needs_setup": False, "backend_configured": True,
                "provider": selected["provider"]}

    lms_path = _lms.find_lms_cli()
    downloaded = _lms.list_downloaded_models(lms_path) if lms_path else []
    missing = [
        spec.model_id for spec in _lms.REQUIRED_MODELS
        if not _lms._is_already_present(spec.model_id, downloaded)
    ]
    server_running = _lms.is_server_running() if lms_path else False

    return {
        "needs_setup": not lms_path or bool(missing) or not server_running,
        "lms_cli_found": lms_path is not None,
        "server_running": server_running,
        "models_missing": missing,
        "required_models": [
            {"model_id": s.model_id, "role": s.role, "approx_vram_gb": s.approx_vram_gb}
            for s in _lms.REQUIRED_MODELS
        ],
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

    from src.core.intelligence.adapters.openai_adapter import OpenAIAdapter

    probe = OpenAIAdapter(base_url=base_url, api_key=api_key or "not-needed", model_name="probe")
    model_ids = await probe.discover_models(timeout=3.0)
    return {
        "reachable": bool(model_ids),
        "models": [
            {"id": mid, "display_name": mid.split("/")[-1], "supports_tools": True}
            for mid in model_ids
            if not any(
                term in mid.lower()
                for term in ("embed", "rerank", "clip", "tts", "kokoro", "whisper", "stt")
            )
        ],
        "download_url": None,
    }


def save_provider_choice(
    provider: str, base_url: str, api_key: str, model_id: str, supports_tools: bool = True
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
    provider_key = "ollama" if provider == "ollama" else "static"

    from src.infrastructure.repositories import ConfigRepository
    from src.shared.config_manager import ConfigManager

    config_repo = ConfigRepository()
    config_repo.update_preference(
        f"local_models.providers.{provider_key}",
        {"base_url": base_url, "api_key": api_key or "", "enabled": True},
    )
    active_model = {
        "name": model_id,
        "provider": provider_key,
        "base_url": base_url,
        "api_key": api_key or "",
        "supports_tools": supports_tools,
    }
    config_repo.update_preference("local_models.use_local", True)
    config_repo.update_preference("local_models.active_model", active_model)
    config_repo.update_preference("local_models.supervisor", active_model)

    # Keep in-memory config in sync so a subsequent get_setup_status()/discovery call
    # in the same process sees the choice immediately (mirrors set_local_model).
    ConfigManager._recursive_update(
        ConfigManager._config,
        {"local_models": {"use_local": True, "active_model": active_model,
                          "supervisor": active_model}},
    )

    logger.info(f"[ SETUP ] Backend chosen: {provider_key} / '{model_id}' at {base_url}")
    return {"success": True}
