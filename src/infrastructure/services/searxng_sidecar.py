"""SearXNG sidecar control — one-click local search provisioning (issue #415).

Mirrors ``presenton_sidecar.py``'s pattern: Chad's ``web_search`` tool already defaults to
``tools.search_backend: searxng`` with Playwright + Brave fallbacks (see ``docs/features/WEB_SEARCH.md``),
so there's no config to write here — the only missing piece for a fresh user is actually starting
the Docker container the config already points at.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("SearxngSidecar")

COMPOSE_FILE_REL = Path("docker") / "chad-services.yml"
DEFAULT_SEARXNG_URL = "http://127.0.0.1:8080"


def _is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


async def _search_api_connected(url: str) -> bool:
    """Check the JSON search API Chad calls — not whether every engine returns hits."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{url.rstrip('/')}/search",
                params={"q": "healthcheck", "format": "json"},
            )
            response.raise_for_status()
            body = response.json()
            return isinstance(body, dict) and isinstance(body.get("results"), list)
    except Exception:
        return False


async def _wait_until_connected(url: str, timeout_seconds: float = 30.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _search_api_connected(url):
            return True
        await asyncio.sleep(1.0)
    return False


def get_searxng_settings() -> Dict[str, Any]:
    """Return SearXNG sidecar settings for Settings → Services."""
    return {
        "url": str(ConfigManager.get("tools.searxng_url", DEFAULT_SEARXNG_URL)).rstrip("/"),
        "search_backend": str(ConfigManager.get("tools.search_backend", "searxng")),
    }


VALID_SEARCH_BACKENDS = {"searxng", "brave", "serper", "tavily", "all"}


async def get_search_quota() -> Dict[str, Any]:
    """Best-effort quota/usage snapshot for each configured paid search backend.

    Only Tavily exposes a real live-usage endpoint (GET /usage, no credit consumed).
    Brave's remaining-quota figure only ever appears as a response header on an actual
    paid search call — there's no standalone check, so polling it here would burn quota
    just to display quota. Serper exposes no usage API at all (dashboard-only). Both are
    reported with ``available: false`` and a note rather than faked or omitted silently.
    """
    from src.tools.web.tavily_search import tavily_configured, get_tavily_usage
    from src.tools.web.serper_search import serper_configured

    result: Dict[str, Any] = {}

    if tavily_configured():
        usage = await get_tavily_usage()
        result["tavily"] = {"configured": True, "available": True, **usage}
    else:
        result["tavily"] = {"configured": False, "available": False}

    if serper_configured():
        result["serper"] = {
            "configured": True, "available": False,
            "note": "Serper has no usage API — check serper.dev/api-keys for remaining credits.",
        }
    else:
        result["serper"] = {"configured": False, "available": False}

    if _brave_configured():
        result["brave"] = {
            "configured": True, "available": False,
            "note": "Brave only reports quota as a header on an actual search call, not via "
                    "a standalone check — see api-dashboard.search.brave.com for usage.",
        }
    else:
        result["brave"] = {"configured": False, "available": False}

    return result


def _brave_configured() -> bool:
    return bool(ConfigManager.get_secret("BRAVE_API_KEY"))


def set_searxng_settings(search_backend: Optional[str] = None) -> Dict[str, Any]:
    """Persist ``tools.search_backend`` (issue #523: Serper/Tavily/all backend picker).

    Mirrors the preferences-overlay write pattern used by
    ``ConfigFacade.set_browser_safety_settings`` — writes to the ``preferences`` config
    layer (not ``config/config.yaml`` directly) and hot-reloads ``ConfigManager`` so the
    change takes effect without a restart.
    """
    if search_backend is None:
        return {"success": True, **get_searxng_settings()}

    if search_backend not in VALID_SEARCH_BACKENDS:
        return {
            "success": False,
            "error": f"Invalid search_backend {search_backend!r}. Must be one of {sorted(VALID_SEARCH_BACKENDS)}.",
            **get_searxng_settings(),
        }

    from src.infrastructure.repositories import ConfigRepository

    config_repo = ConfigRepository()
    prefs = config_repo.load("preferences", use_cache=False)
    tools_prefs = prefs.setdefault("tools", {})
    tools_prefs["search_backend"] = search_backend
    config_repo.save("preferences", prefs, update_cache=True)

    ConfigManager._recursive_update(ConfigManager.get_config(), {"tools": {"search_backend": search_backend}})
    logger.info(f"[ SEARXNG ] search_backend updated: {search_backend}")
    return {"success": True, **get_searxng_settings()}


def _run_docker_compose_up(repo_root: Path) -> tuple[bool, str]:
    compose = repo_root / COMPOSE_FILE_REL
    if not compose.is_file():
        return False, f"Compose file not found: {compose}"

    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose),
        "up",
        "-d",
        "searxng",
        "valkey",
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
        return False, "Docker not found on PATH — install Docker Desktop first."
    except subprocess.TimeoutExpired:
        return False, "docker compose timed out after 180s."

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        return False, f"docker compose failed: {detail or proc.returncode}"

    return True, "SearXNG sidecar started."


async def start_searxng_sidecar(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Connect to configured SearXNG, provisioning Chad's local stack only when needed."""
    url = str(ConfigManager.get("tools.searxng_url", DEFAULT_SEARXNG_URL)).rstrip("/")
    if await _search_api_connected(url):
        return {
            "success": True,
            "connected": True,
            "provisioned": False,
            "message": f"Already connected to SearXNG at {url}; Docker was not changed.",
        }

    if not _is_local_url(url):
        return {
            "success": False,
            "connected": False,
            "provisioned": False,
            "message": (
                f"Could not connect to external SearXNG at {url}. "
                "Chad did not start or modify any local Docker containers."
            ),
        }

    root = repo_root or get_project_root()
    ok, message = await asyncio.to_thread(_run_docker_compose_up, root)
    if ok:
        connected = await _wait_until_connected(url)
        if connected:
            message = "Connected to Chad's local SearXNG. Existing Docker data was preserved."
            logger.info(f"[ SEARXNG ] Sidecar start: {message}")
        else:
            ok = False
            message = (
                "Docker started the local SearXNG stack, but its search API did not become ready."
            )
            logger.warning(f"[ SEARXNG ] Sidecar start incomplete: {message}")
    else:
        logger.warning(f"[ SEARXNG ] Sidecar start failed: {message}")
        connected = False
    return {
        "success": ok,
        "connected": connected,
        "provisioned": True,
        "message": message,
    }
