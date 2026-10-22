"""Presenton sidecar config sync — preferences + Docker env (issue #350)."""

from __future__ import annotations

import asyncio
import secrets
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("PresentonSidecar")

DEFAULT_PRESENTON_URL = "http://127.0.0.1:5000"
DEFAULT_LLM_URL = "http://host.docker.internal:1234/v1"
DEFAULT_AUTH_USERNAME = "chad"
ENV_PATH_REL = Path("docker") / "presenton" / ".env"
COMPOSE_FILE_REL = Path("docker") / "chad-services.yml"


def _read_presenton_env(repo_root: Optional[Path] = None) -> Dict[str, str]:
    env_path = _env_file_path(repo_root or get_project_root())
    if not env_path.is_file():
        return {}
    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve_presenton_auth() -> Tuple[str, str]:
    """
    Return Presenton v8+ admin credentials for Docker preseed and API Basic auth.

    Resolution order: secrets.yaml → preferences → generated default (username ``chad``).
    """
    env_values = _read_presenton_env()
    user = (
        ConfigManager.get("PRESENTON_AUTH_USERNAME")
        or env_values.get("AUTH_USERNAME")
        or ConfigManager.get("visual.presenton_auth_username")
        or DEFAULT_AUTH_USERNAME
    )
    password = (
        ConfigManager.get("PRESENTON_AUTH_PASSWORD")
        or env_values.get("AUTH_PASSWORD")
        or ConfigManager.get("visual.presenton_auth_password")
    )
    if not password:
        password = secrets.token_urlsafe(16)
    return str(user), str(password)


def get_presenton_settings() -> Dict[str, Any]:
    """Return Presenton settings for the Settings UI."""
    url = ConfigManager.get("visual.presenton_url")
    model = ConfigManager.get("visual.presenton_model")
    if not model:
        try:
            model = ConfigManager.get_active_model()
        except Exception:
            model = None
    return {
        "enabled": bool(url),
        "url": str(url).rstrip("/") if url else "",
        "llm_url": str(
            ConfigManager.get("visual.presenton_llm_url", DEFAULT_LLM_URL)
        ).rstrip("/"),
        "model": model or "",
        "use_async": bool(ConfigManager.get("visual.presenton_use_async", False)),
    }


def _env_file_path(repo_root: Path) -> Path:
    return repo_root / ENV_PATH_REL


def _is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _sidecar_runtime_keys_changed(visual_update: Dict[str, Any]) -> bool:
    """True when deck model or LLM endpoint changed (requires Docker env sync)."""
    return any(
        key in visual_update for key in ("presenton_model", "presenton_llm_url")
    )


async def _presenton_connected(url: str) -> bool:
    """Verify both Presenton availability and the credentials Chad will use."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            base_url = url.rstrip("/")
            status = await client.get(f"{base_url}/api/v1/auth/status")
            status.raise_for_status()
            body = status.json()
            if not isinstance(body, dict) or body.get("configured") is not True:
                return False

            api_key = ConfigManager.get("PRESENTON_API_KEY") or ConfigManager.get(
                "visual.presenton_api_key"
            )
            kwargs: Dict[str, Any]
            if api_key:
                kwargs = {"headers": {"Authorization": f"Bearer {str(api_key).strip()}"}}
            else:
                kwargs = {"auth": resolve_presenton_auth()}
            protected = await client.get(
                f"{base_url}/api/v1/ppt/presentation/all",
                **kwargs,
            )
            return protected.status_code < 400
    except Exception:
        return False


async def _wait_until_connected(url: str, timeout_seconds: float = 45.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _presenton_connected(url):
            return True
        await asyncio.sleep(1.0)
    return False


def write_presenton_env(
    repo_root: Path,
    *,
    llm_url: str,
    model: str,
    auth_username: Optional[str] = None,
    auth_password: Optional[str] = None,
) -> Path:
    """Write docker/presenton/.env for the sidecar container."""
    env_path = _env_file_path(repo_root)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    user, password = auth_username or "", auth_password or ""
    if not user or not password:
        user, password = resolve_presenton_auth()
    lines = [
        "# Generated by Chad — edit via Settings → Services → Presenton",
        "LLM=custom",
        f"CUSTOM_LLM_URL={llm_url.rstrip('/')}",
        f"CUSTOM_MODEL={model}",
        "CUSTOM_LLM_API_KEY=lm-studio",
        "TOOL_CALL=true",
        "DISABLE_IMAGE_GENERATION=true",
        "CAN_CHANGE_KEYS=false",
        f"AUTH_USERNAME={user}",
        f"AUTH_PASSWORD={password}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"[ PRESENTON ] Wrote sidecar env: {env_path}")
    return env_path


def _run_docker_compose_presenton(repo_root: Path) -> tuple[bool, str]:
    compose = repo_root / COMPOSE_FILE_REL
    if not compose.is_file():
        return False, f"Compose file not found: {compose}"

    docker = "docker"
    cmd = [
        docker,
        "compose",
        "-f",
        str(compose),
        "--profile",
        "presenton",
        "up",
        "-d",
        "presenton",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except FileNotFoundError:
        return False, "Docker not found on PATH — install Docker Desktop."
    except subprocess.TimeoutExpired:
        return False, "docker compose timed out after 180s."

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        return False, f"docker compose failed: {detail or proc.returncode}"

    return True, "Presenton sidecar restarted."


async def restart_presenton_sidecar(repo_root: Optional[Path] = None) -> tuple[bool, str]:
    root = repo_root or get_project_root()
    return await asyncio.to_thread(_run_docker_compose_presenton, root)


async def _apply_local_sidecar_config(
    merged: Dict[str, Any],
    repo_root: Path,
) -> tuple[bool, str, bool]:
    """Write docker/presenton/.env and recreate the local Presenton container."""
    from src.infrastructure.repositories import ConfigRepository

    presenton_url = str(merged["url"]).rstrip("/")
    env_path = _env_file_path(repo_root)
    env_values = _read_presenton_env(repo_root)
    if env_path.is_file() and (
        not env_values.get("AUTH_USERNAME") or not env_values.get("AUTH_PASSWORD")
    ):
        return (
            False,
            (
                f"Existing Presenton configuration preserved at {env_path}. "
                "Add AUTH_USERNAME and AUTH_PASSWORD to that file, then try again."
            ),
            False,
        )

    auth_user, auth_password = resolve_presenton_auth()
    if not env_path.is_file():
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load("preferences", use_cache=False)
            visual_prefs = prefs.setdefault("visual", {})
            visual_prefs["presenton_auth_username"] = auth_user
            visual_prefs["presenton_auth_password"] = auth_password
            config_repo.save("preferences", prefs, update_cache=True)
            ConfigManager._recursive_update(
                ConfigManager.get_config(),
                {
                    "visual": {
                        "presenton_auth_username": auth_user,
                        "presenton_auth_password": auth_password,
                    }
                },
            )
        except Exception as exc:
            logger.warning(f"[ PRESENTON ] Could not persist auth credentials: {exc}")

    write_presenton_env(
        repo_root,
        llm_url=merged["llm_url"],
        model=merged["model"],
        auth_username=auth_user,
        auth_password=auth_password,
    )

    sidecar_ok, sidecar_message = await restart_presenton_sidecar(repo_root)
    if not sidecar_ok:
        return False, sidecar_message, False

    connected = await _wait_until_connected(presenton_url)
    if connected:
        sidecar_message = (
            f"Presenton now uses {merged['model']} at {merged['llm_url']}."
        )
    else:
        sidecar_ok = False
        sidecar_message = (
            "Docker restarted Presenton, but its authenticated API did not become ready."
        )
    return sidecar_ok, sidecar_message, connected


async def set_presenton_settings(
    *,
    enabled: Optional[bool] = None,
    url: Optional[str] = None,
    llm_url: Optional[str] = None,
    model: Optional[str] = None,
    use_async: Optional[bool] = None,
    restart_sidecar: bool = False,
) -> Dict[str, Any]:
    """
    Persist Presenton settings to preferences and optionally sync the Docker sidecar.
    """
    from src.infrastructure.repositories import ConfigRepository

    current = get_presenton_settings()
    visual_update: Dict[str, Any] = {}

    if enabled is not None:
        if enabled and not (url or current["url"]):
            visual_update["presenton_url"] = DEFAULT_PRESENTON_URL
        elif not enabled:
            visual_update["presenton_url"] = None

    if url is not None:
        cleaned = str(url).strip().rstrip("/")
        visual_update["presenton_url"] = cleaned or None

    if llm_url is not None:
        cleaned = str(llm_url).strip().rstrip("/")
        visual_update["presenton_llm_url"] = cleaned or DEFAULT_LLM_URL

    if model is not None:
        cleaned = str(model).strip()
        visual_update["presenton_model"] = cleaned or None

    if use_async is not None:
        visual_update["presenton_use_async"] = bool(use_async)

    if not visual_update and not restart_sidecar:
        return {"success": True, "settings": get_presenton_settings()}

    if visual_update:
        try:
            config_repo = ConfigRepository()
            prefs = config_repo.load("preferences", use_cache=False)
            visual_prefs = prefs.setdefault("visual", {})
            visual_prefs.update(visual_update)
            config_repo.save("preferences", prefs, update_cache=True)
            ConfigManager._recursive_update(ConfigManager.get_config(), {"visual": visual_update})
        except Exception as exc:
            logger.error(f"set_presenton_settings persist failed: {exc}", exc_info=True)
            return {"success": False, "error": str(exc)}

    merged = get_presenton_settings()
    sidecar_ok = True
    sidecar_message = ""
    connected: Optional[bool] = None
    runtime_changed = _sidecar_runtime_keys_changed(visual_update)
    presenton_url = str(merged["url"]).rstrip("/")
    local_sidecar = merged["enabled"] and merged["model"] and _is_local_url(presenton_url)
    should_sync = local_sidecar and (restart_sidecar or runtime_changed)

    if restart_sidecar and merged["enabled"] and merged["model"] and not _is_local_url(presenton_url):
        connected = await _presenton_connected(presenton_url)
        if not connected:
            return {
                "success": False,
                "settings": get_presenton_settings(),
                "sidecar_restarted": False,
                "connected": False,
                "error": (
                    f"Could not connect to external Presenton at {presenton_url}. "
                    "Chad did not start Docker or modify docker/presenton/.env."
                ),
            }
        return {
            "success": True,
            "settings": get_presenton_settings(),
            "sidecar_restarted": False,
            "connected": True,
            "sidecar_message": f"Connected to external Presenton at {presenton_url}.",
        }

    if should_sync:
        repo_root = get_project_root()
        if restart_sidecar and not runtime_changed and await _presenton_connected(presenton_url):
            return {
                "success": True,
                "settings": get_presenton_settings(),
                "sidecar_restarted": False,
                "connected": True,
                "sidecar_message": (
                    f"Already connected to Presenton at {presenton_url}; "
                    "Docker and its environment were not changed."
                ),
            }

        sidecar_ok, sidecar_message, connected = await _apply_local_sidecar_config(
            merged, repo_root
        )
        if not sidecar_ok:
            logger.warning(f"[ PRESENTON ] Sidecar sync failed: {sidecar_message}")

    return {
        "success": sidecar_ok,
        "settings": get_presenton_settings(),
        "sidecar_restarted": sidecar_ok and should_sync,
        "connected": connected,
        "sidecar_message": sidecar_message,
    }
