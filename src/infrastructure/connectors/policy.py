"""
Connector opt-in policy.

All shipped MCP servers and native connectors (Gmail, Telegram) are off by default.
A server is only started when it is enabled in preferences AND credentials validate.
"""

from __future__ import annotations

import os
import re
from typing import Any

from src.shared.config_manager import ConfigManager

SHIPPED_MCP_SERVER_IDS = frozenset({
    "playwright",
    "brave-search",
    "todoist",
    "fetch",
    "github",
    "google-drive",
    "notion",
    "excalidraw",
    "supabase",
    "canva",
})

NATIVE_CONNECTOR_IDS = frozenset({"gmail", "telegram", "canvas", "stock-images", "finance", "groupme", "outlook"})

# Env keys that are configuration, not secrets.
_MCP_NON_SECRET_ENV_KEYS = frozenset({
    "EXPRESS_SERVER_URL",
    "ENABLE_CANVAS_SYNC",
})

_PLACEHOLDER_RE = re.compile(
    r"^(your[-_].*|paste[-_].*|sk[-_].*xxxxx|123456789:.*|/path/to/.*)$",
    re.IGNORECASE,
)


def resolve_env_value(raw: Any) -> str:
    """Expand %VAR%, ${VAR}, and os.path.expandvars for MCP env values."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return str(raw)
    text = raw.strip()
    if text.startswith("%") and text.endswith("%"):
        # Windows os.path.expandvars() leaves an unresolved %VAR% as the
        # literal string when the var isn't set — that literal text doesn't
        # match the placeholder regex below, so falling through to it here
        # would make an unset credential look "resolved" (#216).
        key = text[1:-1]
        return os.getenv(key, "")
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    return os.path.expandvars(text)


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    return bool(_PLACEHOLDER_RE.match(value))


def mcp_credentials_satisfied(cfg: dict) -> tuple[bool, list[str]]:
    """
    Return whether required MCP env credentials are present.

    Keyless MCPs (empty env or only non-secret keys) always pass.
    """
    env = cfg.get("env") or {}
    if not env:
        return True, []

    missing: list[str] = []
    for key, raw in env.items():
        if key in _MCP_NON_SECRET_ENV_KEYS:
            continue
        resolved = resolve_env_value(raw)
        if _is_placeholder(resolved):
            missing.append(key)
    return len(missing) == 0, missing


def merge_mcp_configs(prefs: dict) -> dict[str, dict]:
    base = ConfigManager.get("mcp_servers", {}) or {}
    pref_servers = prefs.get("mcp_servers") or {}
    return {**base, **pref_servers}


def ensure_connector_defaults(prefs: dict) -> dict:
    """
    One-time migration: disable all shipped MCP + native connectors unless already configured.

    Existing users with secrets/tokens filled in keep working — those servers are re-enabled.
    """
    if prefs.get("connectors_opt_in_v1"):
        return prefs

    base = ConfigManager.get("mcp_servers", {}) or {}
    disabled = set(prefs.get("disabled_mcp_servers") or [])
    disabled.update(base.keys())

    merged = merge_mcp_configs(prefs)
    for name, cfg in merged.items():
        ok, _ = mcp_credentials_satisfied(cfg)
        if ok:
            disabled.discard(name)

    native_disabled = set(prefs.get("disabled_native_connectors") or [])
    native_disabled.update(NATIVE_CONNECTOR_IDS)
    for connector_id in NATIVE_CONNECTOR_IDS:
        if _native_connector_configured(connector_id):
            native_disabled.discard(connector_id)

    prefs["disabled_mcp_servers"] = sorted(disabled)
    prefs["disabled_native_connectors"] = sorted(native_disabled)
    prefs["connectors_opt_in_v1"] = True
    return prefs


def get_effective_mcp_servers(prefs: dict | None = None) -> dict[str, dict]:
    """MCP servers that should register and discover tools at runtime."""
    from src.infrastructure.repositories import ConfigRepository

    repo = ConfigRepository()
    if prefs is None:
        prefs = repo.load("preferences", use_cache=False)

    migrated = ensure_connector_defaults(dict(prefs))
    if not prefs.get("connectors_opt_in_v1"):
        repo.save("preferences", migrated, update_cache=True)
        prefs = migrated

    disabled = set(prefs.get("disabled_mcp_servers") or [])
    merged = merge_mcp_configs(prefs)
    effective: dict[str, dict] = {}
    for name, cfg in merged.items():
        if name in disabled:
            continue
        ok, _ = mcp_credentials_satisfied(cfg)
        if not ok:
            continue
        effective[name] = cfg
    return effective


def normalize_mcp_env_for_storage(env: dict, base_env: dict | None = None) -> dict[str, str]:
    """Store %VAR% placeholders in preferences; literal values belong in secrets.yaml."""
    base_env = base_env or {}
    stored: dict[str, str] = {}
    for key, value in (env or {}).items():
        if key in _MCP_NON_SECRET_ENV_KEYS:
            stored[key] = str(value)
            continue
        base_val = str((base_env or {}).get(key, f"%{key}%"))
        if isinstance(value, str) and value.startswith("%") and value.endswith("%"):
            stored[key] = value
        else:
            stored[key] = base_val if base_val.startswith("%") else f"%{key}%"
    for key, value in base_env.items():
        if key not in stored:
            stored[key] = str(value)
    return stored


def persist_connector_secrets(env: dict) -> None:
    """Write literal credential values from a connect flow into secrets.yaml."""
    from src.infrastructure.repositories import ConfigRepository

    repo = ConfigRepository()
    for key, value in (env or {}).items():
        if key in _MCP_NON_SECRET_ENV_KEYS:
            continue
        if not value or (isinstance(value, str) and value.startswith("%") and value.endswith("%")):
            continue
        repo.update_secret(key, str(value))


def _gmail_configured() -> bool:
    try:
        from src.tools.workspace.manager import GoogleWorkspaceManager

        mgr = GoogleWorkspaceManager()
        ok, _ = mgr.authenticate()
        return bool(ok)
    except Exception:
        return False


def _telegram_configured() -> bool:
    token = ConfigManager.get("telegram.bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    return bool(token) and not _is_placeholder(str(token))


def _canvas_configured() -> bool:
    base = ConfigManager.get_secret("CANVAS_BASE_URL") or ConfigManager.get_secret("canvas.base_url")
    token = ConfigManager.get_secret("CANVAS_TOKEN") or ConfigManager.get_secret("canvas.token")
    if not base or not token:
        return False
    if _is_placeholder(str(base)) or _is_placeholder(str(token)):
        return False
    from src.tools.canvas.manager import normalize_canvas_api_base_url
    return normalize_canvas_api_base_url(str(base)) is not None


def _stock_images_configured() -> bool:
    """True when at least one optional stock-image API key is set (Openverse needs none)."""
    for key in ("PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY", "UNSPLASH_API_KEY", "PIXABAY_API_KEY"):
        val = ConfigManager.get_secret(key)
        if val and not _is_placeholder(str(val)):
            return True
    return False


def _finance_configured() -> bool:
    """True when Plaid Client ID and Secret are configured."""
    client_id = ConfigManager.get_secret("PLAID_CLIENT_ID") or ConfigManager.get_secret("plaid.client_id")
    secret = ConfigManager.get_secret("PLAID_SECRET") or ConfigManager.get_secret("plaid.secret")
    if not client_id or not secret:
        return False
    return not _is_placeholder(str(client_id)) and not _is_placeholder(str(secret))


def _groupme_configured() -> bool:
    """True when GroupMe personal access token is configured."""
    token = (
        ConfigManager.get_secret("GROUPME_ACCESS_TOKEN")
        or ConfigManager.get_secret("groupme.access_token")
        or os.getenv("GROUPME_ACCESS_TOKEN")
    )
    if not token:
        return False
    return not _is_placeholder(str(token))


def _outlook_configured() -> bool:
    """True when running on Windows and Classic Outlook desktop is dispatched."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mapi = outlook.GetNamespace("MAPI")
            return bool(mapi.Folders.Count > 0)
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return False


def _native_connector_configured(connector_id: str) -> bool:
    if connector_id == "gmail":
        return _gmail_configured()
    if connector_id == "telegram":
        return _telegram_configured()
    if connector_id == "canvas":
        return _canvas_configured()
    if connector_id == "stock-images":
        return _stock_images_configured()
    if connector_id == "finance":
        return _finance_configured()
    if connector_id == "groupme":
        return _groupme_configured()
    if connector_id == "outlook":
        return _outlook_configured()
    return False


def native_connector_status(prefs: dict | None = None) -> list[dict[str, Any]]:
    from src.infrastructure.repositories import ConfigRepository

    if prefs is None:
        prefs = ConfigRepository().load("preferences", use_cache=False)

    prefs = ensure_connector_defaults(dict(prefs))
    disabled = set(prefs.get("disabled_native_connectors") or [])

    results = []
    for connector_id in sorted(NATIVE_CONNECTOR_IDS):
        configured = _native_connector_configured(connector_id)
        enabled = connector_id not in disabled and configured
        results.append({
            "id": connector_id,
            "enabled": enabled,
            "configured": configured,
            "connected": enabled,
        })
    return results


def test_canvas_connection(
    base_url: str,
    token: str,
    contact_email: str = "",
) -> dict[str, Any]:
    """Validate Canvas instance URL + access token via GET /users/self/profile."""
    import requests
    from src.tools.canvas.manager import (
        build_canvas_user_agent,
        normalize_canvas_api_base_url,
    )

    api_base = normalize_canvas_api_base_url((base_url or "").strip())
    token = (token or "").strip()
    if not api_base:
        return {"success": False, "error": "Canvas base URL is required (e.g. https://your-school.instructure.com)"}
    if not token:
        return {"success": False, "error": "Canvas access token is required"}

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": build_canvas_user_agent(contact_email),
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(f"{api_base}/users/self/profile", headers=headers, timeout=30)
        if response.status_code == 401:
            return {
                "success": False,
                "error": "Unauthorized — token invalid or expired. Create a new token under Canvas → Settings → Approved Integrations.",
            }
        if response.status_code >= 400:
            return {"success": False, "error": f"Canvas returned HTTP {response.status_code}. Check the base URL and token."}
        profile = response.json()
        name = profile.get("name") or profile.get("short_name") or "Canvas user"
        return {"success": True, "name": name, "login_id": profile.get("login_id")}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Connection timed out. Check the Canvas URL and network."}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Could not reach Canvas: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_stock_images_connection(
    pexels_key: str = "",
    unsplash_key: str = "",
    pixabay_key: str = "",
) -> dict[str, Any]:
    """Validate at least one stock-image API key with a 1-result search."""
    import requests

    pexels_key = (pexels_key or "").strip()
    unsplash_key = (unsplash_key or "").strip()
    pixabay_key = (pixabay_key or "").strip()
    checked: list[str] = []

    try:
        if pexels_key and not _is_placeholder(pexels_key):
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": "nature", "per_page": 1},
                headers={"Authorization": pexels_key},
                timeout=20,
            )
            if resp.status_code == 401:
                return {"success": False, "error": "Pexels API key rejected (401)."}
            if resp.status_code >= 400:
                return {"success": False, "error": f"Pexels returned HTTP {resp.status_code}."}
            checked.append("Pexels")

        if unsplash_key and not _is_placeholder(unsplash_key):
            resp = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": "nature", "per_page": 1},
                headers={"Authorization": f"Client-ID {unsplash_key}", "Accept-Version": "v1"},
                timeout=20,
            )
            if resp.status_code == 401:
                return {"success": False, "error": "Unsplash access key rejected (401)."}
            if resp.status_code >= 400:
                return {"success": False, "error": f"Unsplash returned HTTP {resp.status_code}."}
            checked.append("Unsplash")

        if pixabay_key and not _is_placeholder(pixabay_key):
            resp = requests.get(
                "https://pixabay.com/api/",
                params={"key": pixabay_key, "q": "nature", "per_page": 3},
                timeout=20,
            )
            if resp.status_code >= 400:
                return {"success": False, "error": f"Pixabay returned HTTP {resp.status_code}."}
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if isinstance(body, dict) and body.get("totalHits") is None and "hits" not in body:
                return {"success": False, "error": "Pixabay key looks invalid (unexpected response)."}
            checked.append("Pixabay")
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Connection timed out checking stock image APIs."}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Could not reach stock image API: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    if not checked:
        return {
            "success": False,
            "error": (
                "Provide at least one API key (Pexels, Unsplash, or Pixabay). "
                "Openverse needs no key and is always available via Services."
            ),
        }
    return {"success": True, "providers": checked, "name": ", ".join(checked)}


async def test_telegram_token(bot_token: str) -> dict[str, Any]:
    """Validate a Telegram bot token via getMe."""
    import httpx

    token = (bot_token or "").strip()
    if not token:
        return {"success": False, "error": "Bot token is required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = res.json()
        if not data.get("ok"):
            return {"success": False, "error": data.get("description", "Invalid bot token")}
        bot = data.get("result", {})
        return {
            "success": True,
            "username": bot.get("username"),
            "first_name": bot.get("first_name"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_plaid_connection(
    client_id: str,
    secret: str,
    env: str = "sandbox",
) -> dict[str, Any]:
    """Validate Plaid API credentials via institutions_get."""
    client_id = (client_id or "").strip()
    secret = (secret or "").strip()
    env = (env or "sandbox").strip().lower()

    if not client_id:
        return {"success": False, "error": "Plaid Client ID is required."}
    if not secret:
        return {"success": False, "error": "Plaid Secret key is required."}

    try:
        import plaid
        from plaid.api import plaid_api
        from plaid.model.institutions_get_request import InstitutionsGetRequest
        from plaid.model.country_code import CountryCode

        host_env = plaid.Environment.Sandbox
        if env in ("production", "prod"):
            host_env = plaid.Environment.Production
        elif env in ("development", "dev"):
            host_env = getattr(plaid.Environment, "Development", plaid.Environment.Production)

        configuration = plaid.Configuration(
            host=host_env,
            api_key={"clientId": client_id, "secret": secret},
        )
        api_client = plaid.ApiClient(configuration)
        client = plaid_api.PlaidApi(api_client)

        req = InstitutionsGetRequest(count=1, offset=0, country_codes=[CountryCode("US")])
        client.institutions_get(req)
        return {"success": True, "message": f"Plaid credentials verified successfully ({env} mode)."}
    except Exception as e:
        return {"success": False, "error": f"Plaid connection failed: {e}"}


async def test_groupme_token(access_token: str) -> dict[str, Any]:
    """Validate a GroupMe personal access token via GET /users/me."""
    import httpx

    token = (access_token or "").strip()
    if not token:
        return {"success": False, "error": "GroupMe access token is required"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                "https://api.groupme.com/v3/users/me",
                headers={"X-Access-Token": token, "User-Agent": "ChadAI/1.0"},
            )
            data = res.json()
        if res.status_code != 200 or not data.get("response"):
            err = (data.get("meta", {}).get("errors") or ["Invalid access token"])[0]
            return {"success": False, "error": err}
        user = data.get("response", {})
        return {
            "success": True,
            "name": user.get("name"),
            "phone_number": user.get("phone_number"),
            "email": user.get("email"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_outlook_connection() -> dict[str, Any]:
    """Test local Classic Outlook MAPI connection."""
    import sys
    if sys.platform != "win32":
        return {"success": False, "error": "Outlook COM connector is only supported on Windows"}
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mapi = outlook.GetNamespace("MAPI")
            accounts = [f.Name for f in mapi.Folders if "archive" not in f.Name.lower()]
            if not accounts:
                return {"success": False, "error": "No email accounts configured in Classic Outlook profile"}
            return {
                "success": True,
                "account": accounts[0],
                "all_accounts": [f.Name for f in mapi.Folders],
            }
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return {"success": False, "error": f"Failed connecting to Classic Outlook: {e}"}


