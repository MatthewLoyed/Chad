"""
Update Dashboard Tool - Lets Chad append to his own "my life" dashboard feed.

Available from any turn (heartbeat, Gmail webhook investigation, normal chat,
research) — tool selection is LLM-driven, so Chad decides when something is
worth surfacing rather than each call site hardcoding dashboard writes.
"""

from typing import Any, Dict, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("UpdateDashboardTool")


class UpdateDashboardTool(BaseTool):
    """
    Call this to append an entry to the Dashboard workspace — the user's living
    "my life" feed. Use 'thought' for a note/observation, 'life' for a personal
    widget (pin it), 'status' for automation health, 'discovery' for something
    interesting you found, or 'action' to propose a concrete next step the user
    can run with one click (set the `action` field).
    """

    name = "update_dashboard"
    description = (
        "Append an entry to the user's Dashboard feed. kind='thought' for a "
        "passing observation, 'life' for a personal widget (set pinned=true), "
        "'status' for automation/system health, 'discovery' for something "
        "interesting worth resurfacing, or 'action' for a proposed next step "
        "with a one-click runnable `action`. Don't overuse this — only call it "
        "when something is genuinely worth keeping on the dashboard."
    )
    category = "system"
    is_side_effecting = True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["thought", "life", "status", "discovery", "action"],
                        "description": "What kind of dashboard entry this is."
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the entry/card."
                    },
                    "body": {
                        "type": "string",
                        "description": "The entry content (markdown supported)."
                    },
                    "action": {
                        "type": "object",
                        "description": (
                            "Only for kind='action'. A single runnable step: "
                            "{type: 'tool'|'fast_track', target: <tool name or fast-track alias>, "
                            "params: {...}, label: '<button text>'}."
                        ),
                        "properties": {
                            "type": {"type": "string", "enum": ["tool", "fast_track"]},
                            "target": {"type": "string"},
                            "params": {"type": "object"},
                            "label": {"type": "string"}
                        }
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "Pin this as an always-on widget (e.g. a personal-life countdown)."
                    },
                    "expires_in_hours": {
                        "type": "number",
                        "description": "Optional: auto-expire this entry from the feed after N hours."
                    }
                },
                "required": ["kind", "title", "body"]
            }
        }

    async def execute(
        self,
        kind: str,
        title: str,
        body: str,
        action: Optional[Dict[str, Any]] = None,
        pinned: bool = False,
        expires_in_hours: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        from src.core.intelligence.dashboard_feed import append_entry

        entry = await append_entry(
            kind=kind, title=title, body=body,
            action=action, pinned=bool(pinned), expires_in_hours=expires_in_hours)

        if not entry:
            logger.warning(f"[ UPDATE_DASHBOARD ] Failed to append entry: {title!r} ({kind})")
            return {"success": False, "error": "Failed to write dashboard entry"}

        logger.info(f"[ UPDATE_DASHBOARD ] Appended {kind} entry: {title!r}")
        return {"success": True, "result": f"Dashboard entry added: {title}", "entry_id": entry.get("id")}
