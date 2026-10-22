"""
Heartbeat Manager Tool - Configure periodic monitoring checks

Allows users to add/remove/list heartbeat checks through natural conversation.

Examples:
  "Let me know when I get a new email that's important"
  "Stop checking Twitter for Dream posts"
  "What are you monitoring in heartbeat?"
"""

from typing import Dict, Any, Optional, List
import json
from datetime import datetime
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.infrastructure.repositories.state_repository import StateRepository
from src.infrastructure.database.supabase_manager import get_supabase_manager
# from src.infrastructure.tools.registry import ToolRegistry (Moved to method to avoid circularity during discovery)

logger = setup_logger("HeartbeatManager")


class ManageHeartbeatTool(BaseTool):
    """Tool for configuring heartbeat monitoring checks using Supabase."""

    name = "manage_heartbeat"
    description = "Manage periodic monitoring checks (emails, social, etc.) performed every 30 mins."
    category = "system"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.state_repo = StateRepository()
        self.state_key = "heartbeat_config"
        self.supabase = get_supabase_manager()

    def _load_config(self) -> Dict[str, Any]:
        """Load heartbeat configuration from Supabase."""
        return self.state_repo.get_value(self.state_key, default={
            "checks": [],
            "preferences": {},
            "last_updated": datetime.now().isoformat()
        })

    def _save_config(self, config: Dict[str, Any]):
        """Save heartbeat configuration to Supabase and regenerate persona entry."""
        config["last_updated"] = datetime.now().isoformat()
        self.state_repo.set_value(self.state_key, config)
        
        # Also update the human-readable persona_config for the AI to read every 30 mins
        markdown = self._generate_heartbeat_md(config)
        self.supabase.update("persona_config", filters={"id": "eq.heartbeat"}, data={"content": markdown})

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "remove", "list", "trigger", "update_preferences"],
                        "description": "Action: add, remove, list, trigger, or update_preferences."
                    },
                    "check_type": {
                        "type": "string",
                        "enum": ["email", "social", "calendar", "canvas", "system_health", "custom"],
                        "description": "Monitor type (email, social, etc)."
                    },
                    "check_id": {
                        "type": "string",
                        "description": "Unique ID for removal."
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Monitor-specific parameters (keywords, platform, etc)."
                    },
                    "preferences": {
                        "type": "object",
                        "description": "User notification preferences."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(
        self,
        action: str,
        check_type: Optional[str] = None,
        check_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        preferences: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        try:
            if action == "add":
                return await self._add_check(check_type, parameters or {})
            elif action == "remove":
                return await self._remove_check(check_id)
            elif action == "list":
                return await self._list_checks()
            elif action == "trigger":
                return await self._trigger_heartbeat()
            elif action == "update_preferences":
                return await self._update_preferences(preferences or {})
            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"Heartbeat manager action '{action}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _add_check(self, check_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new heartbeat check."""
        if not check_type:
            return {"success": False, "error": "check_type is required for 'add' action"}

        config = self._load_config()

        # Generate check ID
        import uuid
        check_id = str(uuid.uuid4())[:8]

        # Create check definition
        check = {
            "id": check_id,
            "type": check_type,
            "parameters": parameters,
            "enabled": True,
            "added_at": datetime.now().isoformat()
        }

        # Map check type to tool and generate description
        check_info = self._get_check_info(check_type, parameters)
        tool_name = check_info["tool"]
        
        # VALIDATION: Check if tool exists in the registry
        from src.infrastructure.tools.registry import ToolRegistry
        registry = ToolRegistry()
        if not registry.get_tool(tool_name):
            return {
                "success": False, 
                "error": f"Tool '{tool_name}' is not in the registry. I cannot monitor something I don't know how to do."
            }

        check["tool"] = tool_name
        check["description"] = check_info["description"]

        config["checks"].append(check)
        self._save_config(config)

        return {
            "success": True,
            "result": f"Added heartbeat check: {check['description']} to Supabase Cloud.",
            "check_id": check_id,
            "check": check
        }

    async def _trigger_heartbeat(self) -> Dict[str, Any]:
        """Manually trigger the heartbeat execution."""
        from src.remote.server import ChadServer
        
        if not ChadServer.instance:
            return {"success": False, "error": "ChadServer instance not found."}
            
        if not ChadServer.instance._heartbeat_service:
            return {"success": False, "error": "Heartbeat service not initialized on server."}
            
        # We fire and forget or wait?
        # To observe in the logs/gui, we should probably wait for it to start.
        # But _execute_heartbeat is async and we can await it.
        try:
            # We wrap it in a task so the tool finishes and Chad can then 
            # "see" the heartbeat turn happen in parallel or following.
            # Actually, the user wants to OBSERVE, so let's await it.
            logger.info("Manual heartbeat trigger started via tool.")
            asyncio.create_task(ChadServer.instance._heartbeat_service._execute_heartbeat())
            
            return {
                "success": True, 
                "result": "Heartbeat triggered successfully. Observe the logs or story mode for the 'SYSTEM' turn."
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to trigger heartbeat: {e}"}

    async def _remove_check(self, check_id: str) -> Dict[str, Any]:
        """Remove a heartbeat check."""
        if not check_id:
            return {"success": False, "error": "check_id is required for 'remove' action"}

        config = self._load_config()

        # Find and remove check
        original_count = len(config["checks"])
        config["checks"] = [c for c in config["checks"] if c["id"] != check_id]

        if len(config["checks"]) == original_count:
            return {"success": False, "error": f"Check not found: {check_id}"}

        self._save_config(config)

        return {
            "success": True,
            "result": f"Removed heartbeat check: {check_id} from Supabase Cloud."
        }

    async def _list_checks(self) -> Dict[str, Any]:
        """List all active heartbeat checks."""
        config = self._load_config()

        if not config["checks"]:
            return {
                "success": True,
                "result": "No heartbeat checks configured in Supabase. Heartbeat is idle.",
                "checks": []
            }

        # Format check list
        check_list = []
        for check in config["checks"]:
            status = "[Active]" if check.get("enabled") else "[Disabled]"
            check_list.append({
                "id": check["id"],
                "description": check["description"],
                "type": check["type"],
                "status": status,
                "added_at": check.get("added_at")
            })

        result_text = f"Heartbeat monitoring ({len(check_list)} checks active in Cloud):\n"
        for i, chk in enumerate(check_list, 1):
            result_text += f"\n{i}. [{chk['id']}] {chk['description']} ({chk['status']})"

        return {
            "success": True,
            "result": result_text,
            "checks": check_list
        }

    async def _update_preferences(self, preferences: Dict[str, Any]) -> Dict[str, Any]:
        """Update user preferences for heartbeat notifications."""
        config = self._load_config()

        # Merge with existing preferences
        config["preferences"].update(preferences)
        self._save_config(config)

        return {
            "success": True,
            "result": "Updated heartbeat preferences in Supabase Cloud.",
            "preferences": config["preferences"]
        }

    def _get_check_info(self, check_type: str, parameters: Dict[str, Any]) -> Dict[str, str]:
        """Get tool and description for a check type."""
        check_map = {
            "email": {
                "tool": "gmail_check_inbox",
                "description": lambda p: f"Check Gmail for {', '.join(p.get('keywords', ['important']))} emails"
            },
            "canvas": {
                "tool": "get_canvas_dashboard_todos",
                "description": lambda p: "Check Canvas dashboard for upcoming assignments and tasks"
            },
            "social": {
                "tool": lambda p: "twitter_check_user" if p.get("platform") == "twitter" else "youtube_check_channel",
                "description": lambda p: f"Check {p.get('platform', 'social')} for {p.get('username', 'updates')}"
            },
            "calendar": {
                "tool": "google_calendar_check",
                "description": lambda p: f"Check calendar for events in next {p.get('hours_ahead', 2)} hours"
            },
            "system_health": {
                "tool": "system_health_check",
                "description": lambda p: "Check system health (tasks, quota, database)"
            },
            "custom": {
                "tool": lambda p: p.get("tool", "unknown"),
                "description": lambda p: p.get("description", "Custom check")
            }
        }

        if check_type not in check_map:
            return {"tool": "unknown", "description": f"Unknown check type: {check_type}"}

        mapping = check_map[check_type]

        # Resolve tool (might be function)
        tool = mapping["tool"]
        if callable(tool):
            tool = tool(parameters)

        # Resolve description (might be function)
        description = mapping["description"]
        if callable(description):
            description = description(parameters)

        return {"tool": tool, "description": description}

    def _generate_heartbeat_md(self, config: Dict[str, Any]) -> str:
        """Generate human-readable Markdown from configuration."""
        # Build checklist from config
        checks_md = ""
        for check in config["checks"]:
            if not check.get("enabled", True):
                continue

            checks_md += f"## {check.get('type', 'Check').capitalize()}: {check['description']}\n"
            checks_md += f"- Tool: `{check['tool']}`\n"

            # Add parameters
            if check.get("parameters"):
                checks_md += f"- Parameters: {json.dumps(check['parameters'])}\n"

            checks_md += "\n"

        # If no checks, add placeholder
        if not checks_md:
            checks_md = """## No Checks Configured

Heartbeat is currently idle. Use `manage_heartbeat` to add monitoring checks.
"""

        # Generate full file
        content = f"""# Heartbeat Checklist (Cloud-Managed)

This file defines what Chad checks every 30 minutes while running autonomously.

**Last updated:** {config.get('last_updated', 'Unknown')}

---

{checks_md}

---

## Instructions for AI

**How to process this checklist:**
1. Check ALL active items above in ONE turn (batched for efficiency).
2. If nothing important found: Return exactly "HEARTBEAT_OK".
3. If important events found: Send ONE notification with a concise summary.
4. Format notifications as: "[Event Type] - [Summary] - [Action Required/Link]"

**User Preferences:**
{json.dumps(config.get('preferences', {}), indent=2)}
"""
        return content
