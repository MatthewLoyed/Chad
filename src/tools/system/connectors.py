"""
Manage Connectors Tool

System tool for inspecting, testing, configuring, listing, and disconnecting
native integrations and MCP tool servers dynamically in Chad.
"""

import asyncio
from typing import Dict, Any, Optional, List
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.infrastructure.connectors.schemas import (
    CONNECTOR_SCHEMAS,
    get_all_connector_schemas,
    get_connector_schema,
)
from src.infrastructure.connectors.policy import (
    ensure_connector_defaults,
    get_effective_mcp_servers,
    native_connector_status,
    persist_connector_secrets,
    test_canvas_connection,
    test_telegram_token,
    test_stock_images_connection,
    test_plaid_connection,
    test_groupme_token,
    test_outlook_connection,
    NATIVE_CONNECTOR_IDS,
    SHIPPED_MCP_SERVER_IDS,
)
from src.infrastructure.repositories import ConfigRepository
from src.shared.config_manager import ConfigManager

logger = setup_logger("ManageConnectorsTool")


def mask_secret_value(val: str) -> str:
    """Mask sensitive API keys or tokens for logs and tool output responses."""
    if not val:
        return ""
    text = str(val).strip()
    if len(text) > 12:
        return f"{text[:4]}...{text[-4:]}"
    return "***"


class ManageConnectorsTool(BaseTool):
    """
    Manage system connectors dynamically in chat: list status, fetch required input schemas,
    test connection credentials, save secrets to secrets.yaml, and enable/disable connectors.
    """

    name = "manage_connectors"
    description = (
        "Query, list, test, configure, or disconnect native integrations and MCP tool servers "
        "(Finance/Plaid, Canvas LMS, Telegram, GitHub, Notion, Todoist, Brave Search, Stock Images, Excalidraw, etc.). "
        "Use when the user asks to connect a service or when a tool returns an unconfigured/missing credential error. "
        "IMPORTANT: When helping a user set up a connector, first call get_schema to inspect required fields. "
        "Always use web search (brave_search or web search tools) if needed to find exact, up-to-date signup URLs, "
        "dashboard portal paths, and step-by-step instructions for where the user can generate or find those credentials, "
        "providing solid, clear guidance rather than raw parameter prompts. Practice strict credential safety: never commit "
        "secrets to repositories, never paste secrets in public channels, and store them securely."
    )
    category = "system"
    is_side_effecting = True
    requirements = []

    def format_action(self, params: Dict[str, Any]) -> str:
        action = params.get("action", "list")
        cid = params.get("connector_id")
        return f"Managing connector {cid} ({action})" if cid else f"Managing connectors ({action})"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get_schema", "test", "configure", "disconnect"],
                        "description": "The action to perform: 'list', 'get_schema', 'test', 'configure', or 'disconnect'",
                    },
                    "connector_id": {
                        "type": "string",
                        "description": "ID of the connector (e.g. 'canvas', 'telegram', 'github', 'notion', 'todoist', 'brave-search', 'stock-images', 'excalidraw')",
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Key-value dictionary of inputs/credentials required by the connector (e.g. {'CANVAS_BASE_URL': '...', 'CANVAS_TOKEN': '...'})",
                    },
                },
                "required": ["action"],
            },
        }

    async def execute(
        self,
        action: str,
        connector_id: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute connector management action."""
        action = str(action).strip().lower()
        inputs = inputs or {}

        try:
            if action == "list":
                return self._list_connectors()
            elif action == "get_schema":
                return self._get_schema(connector_id)
            elif action == "test":
                return await self._test_connector(connector_id, inputs)
            elif action == "configure":
                return await self._configure_connector(connector_id, inputs)
            elif action == "disconnect":
                return self._disconnect_connector(connector_id)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action '{action}'. Allowed actions: 'list', 'get_schema', 'test', 'configure', 'disconnect'",
                }
        except Exception as e:
            logger.error(f"ManageConnectorsTool action '{action}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _list_connectors(self) -> Dict[str, Any]:
        """List status of all known native and MCP connectors."""
        repo = ConfigRepository()
        prefs = repo.load("preferences", use_cache=False)
        prefs = ensure_connector_defaults(dict(prefs))

        native_statuses = {item["id"]: item for item in native_connector_status(prefs)}
        effective_mcps = set(get_effective_mcp_servers(prefs).keys())
        disabled_mcps = set(prefs.get("disabled_mcp_servers") or [])

        result_list = []
        all_ids = sorted(list(NATIVE_CONNECTOR_IDS | SHIPPED_MCP_SERVER_IDS))

        for cid in all_ids:
            schema = get_connector_schema(cid) or {}
            name = schema.get("name", cid.capitalize())
            ctype = schema.get("type", "native" if cid in NATIVE_CONNECTOR_IDS else "mcp")
            desc = schema.get("description", "")

            if cid in NATIVE_CONNECTOR_IDS:
                status_info = native_statuses.get(cid, {})
                configured = status_info.get("configured", False)
                enabled = status_info.get("enabled", False)
            else:
                enabled = cid in effective_mcps
                configured = cid not in disabled_mcps or cid in effective_mcps

            result_list.append({
                "id": cid,
                "name": name,
                "type": ctype,
                "configured": configured,
                "enabled": enabled,
                "description": desc,
            })

        return {"success": True, "connectors": result_list}

    def _get_schema(self, connector_id: Optional[str]) -> Dict[str, Any]:
        """Retrieve required field schema for a target connector."""
        if not connector_id:
            return {"success": False, "error": "connector_id is required for action 'get_schema'"}

        cid = str(connector_id).strip().lower()
        schema = get_connector_schema(cid)
        if not schema:
            return {
                "success": False,
                "error": f"Unknown connector_id '{connector_id}'. Available: {sorted(list(CONNECTOR_SCHEMAS.keys()))}",
            }

        return {"success": True, "schema": schema}

    async def _test_connector(self, connector_id: Optional[str], inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate credentials for a connector without persisting."""
        if not connector_id:
            return {"success": False, "error": "connector_id is required for action 'test'"}

        cid = str(connector_id).strip().lower()
        schema = get_connector_schema(cid)

        # Merge with existing secrets in config if key is omitted from inputs
        for field in (schema.get("fields") if schema else []) or []:
            fkey = field["key"]
            if fkey not in inputs:
                existing = ConfigManager.get_secret(fkey)
                if existing:
                    inputs[fkey] = existing

        if cid == "canvas":
            base_url = str(inputs.get("CANVAS_BASE_URL") or "")
            token = str(inputs.get("CANVAS_TOKEN") or "")
            email = str(inputs.get("CANVAS_CONTACT_EMAIL") or "")
            return test_canvas_connection(base_url, token, email)

        elif cid == "telegram":
            bot_token = str(inputs.get("telegram.bot_token") or "")
            return await test_telegram_token(bot_token)

        elif cid == "groupme":
            token = str(inputs.get("GROUPME_ACCESS_TOKEN") or "")
            return await test_groupme_token(token)

        elif cid == "outlook":
            return test_outlook_connection()

        elif cid == "stock-images":
            pexels = str(inputs.get("PEXELS_API_KEY") or "")
            unsplash = str(inputs.get("UNSPLASH_ACCESS_KEY") or "")
            pixabay = str(inputs.get("PIXABAY_API_KEY") or "")
            return test_stock_images_connection(pexels, unsplash, pixabay)

        elif cid == "finance":
            client_id = str(inputs.get("PLAID_CLIENT_ID") or "")
            secret = str(inputs.get("PLAID_SECRET") or "")
            env = str(inputs.get("PLAID_ENV") or "sandbox")
            return test_plaid_connection(client_id, secret, env)

        elif cid == "github":
            token = str(inputs.get("GITHUB_PERSONAL_ACCESS_TOKEN") or "").strip()
            if not token:
                return {"success": False, "error": "GITHUB_PERSONAL_ACCESS_TOKEN is required"}
            return await self._test_http_get(
                "https://api.github.com/user",
                headers={"Authorization": f"token {token}", "User-Agent": "ChadAssistant"},
                err_msg="GitHub PAT rejected or invalid",
            )

        elif cid == "notion":
            token = str(inputs.get("NOTION_API_TOKEN") or "").strip()
            if not token:
                return {"success": False, "error": "NOTION_API_TOKEN is required"}
            return await self._test_http_get(
                "https://api.notion.com/v1/users/me",
                headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
                err_msg="Notion token rejected or invalid",
            )

        elif cid == "todoist":
            token = str(inputs.get("TODOIST_API_TOKEN") or "").strip()
            if not token:
                return {"success": False, "error": "TODOIST_API_TOKEN is required"}
            return await self._test_http_get(
                "https://api.todoist.com/rest/v2/projects",
                headers={"Authorization": f"Bearer {token}"},
                err_msg="Todoist token rejected or invalid",
            )

        elif cid == "brave-search":
            key = str(inputs.get("BRAVE_API_KEY") or "").strip()
            if not key:
                return {"success": False, "error": "BRAVE_API_KEY is required"}
            return await self._test_http_get(
                "https://api.search.brave.com/res/v1/web/search?q=test&count=1",
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
                err_msg="Brave Search API key rejected or invalid",
            )

        elif cid == "excalidraw":
            return await self._test_http_get("http://localhost:3000", headers={}, err_msg="Excalidraw Docker sidecar on port 3000 is unreachable")

        else:
            return {"success": True, "message": f"Connector '{cid}' has no standalone API test method. Credentials check passed."}

    async def _test_http_get(self, url: str, headers: dict, err_msg: str) -> Dict[str, Any]:
        """Generic HTTP test helper for API key validation."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 401 or res.status_code == 403:
                    return {"success": False, "error": f"Unauthorized (HTTP {res.status_code}): {err_msg}"}
                if res.status_code >= 400:
                    return {"success": False, "error": f"API returned HTTP {res.status_code}: {err_msg}"}
                return {"success": True, "message": f"Connection test successful (HTTP {res.status_code})"}
        except Exception as e:
            return {"success": False, "error": f"Could not connect: {e}"}

    async def _configure_connector(self, connector_id: Optional[str], inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Test, persist secrets to secrets.yaml, and enable a target connector."""
        if not connector_id:
            return {"success": False, "error": "connector_id is required for action 'configure'"}

        cid = str(connector_id).strip().lower()

        # Step 1: Run connection test first
        test_result = await self._test_connector(cid, inputs)
        if not test_result.get("success"):
            return {
                "success": False,
                "error": f"Connection test failed for '{cid}': {test_result.get('error')}",
                "test_details": test_result,
            }

        # Step 2: Use Facade methods for Native connectors if available
        from src.application.facades.assistant.config import ConfigFacade

        facade = ConfigFacade()

        if cid == "canvas":
            res = await facade.save_canvas_connector(
                base_url=inputs.get("CANVAS_BASE_URL", ""),
                token=inputs.get("CANVAS_TOKEN", ""),
                contact_email=inputs.get("CANVAS_CONTACT_EMAIL", ""),
            )
            return res

        elif cid == "telegram":
            res = await facade.save_telegram_connector(bot_token=inputs.get("telegram.bot_token", ""))
            return res

        elif cid == "stock-images":
            res = await facade.save_stock_images_connector(
                pexels_key=inputs.get("PEXELS_API_KEY", ""),
                unsplash_key=inputs.get("UNSPLASH_ACCESS_KEY", ""),
                pixabay_key=inputs.get("PIXABAY_API_KEY", ""),
            )
            return res

        elif cid == "finance":
            res = await facade.save_finance_connector(
                client_id=inputs.get("PLAID_CLIENT_ID", ""),
                secret=inputs.get("PLAID_SECRET", ""),
                env=inputs.get("PLAID_ENV", "sandbox"),
            )
            return res

        elif cid in NATIVE_CONNECTOR_IDS:
            ok = await facade.enable_native_connector(cid)
            return {"success": ok, "message": f"Native connector '{cid}' enabled."}

        # Step 3: MCP Connector configuration
        persist_connector_secrets(inputs)

        repo = ConfigRepository()
        prefs = repo.load("preferences", use_cache=False)
        prefs = ensure_connector_defaults(dict(prefs))

        disabled = set(prefs.get("disabled_mcp_servers") or [])
        disabled.discard(cid)
        prefs["disabled_mcp_servers"] = sorted(disabled)
        prefs["connectors_opt_in_v1"] = True

        repo.save("preferences", prefs, update_cache=True)
        ConfigManager.load_config()

        masked_inputs = {k: mask_secret_value(str(v)) for k, v in inputs.items()}
        return {
            "success": True,
            "message": f"Connector '{cid}' successfully configured and enabled!",
            "connector_id": cid,
            "saved_fields": masked_inputs,
        }

    def _disconnect_connector(self, connector_id: Optional[str]) -> Dict[str, Any]:
        """Disable a connector and remove it from active configuration."""
        if not connector_id:
            return {"success": False, "error": "connector_id is required for action 'disconnect'"}

        cid = str(connector_id).strip().lower()
        repo = ConfigRepository()
        prefs = repo.load("preferences", use_cache=False)
        prefs = ensure_connector_defaults(dict(prefs))

        if cid in NATIVE_CONNECTOR_IDS:
            from src.application.facades.assistant.config import ConfigFacade

            # Use sync wrapper or run async delete
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(ConfigFacade().delete_native_connector(cid))
            else:
                loop.run_until_complete(ConfigFacade().delete_native_connector(cid))
            return {"success": True, "message": f"Native connector '{cid}' disconnected."}

        disabled = set(prefs.get("disabled_mcp_servers") or [])
        disabled.add(cid)
        prefs["disabled_mcp_servers"] = sorted(disabled)
        repo.save("preferences", prefs, update_cache=True)
        ConfigManager.load_config()

        return {"success": True, "message": f"MCP connector '{cid}' disabled."}
