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
        "when something is genuinely worth keeping on the dashboard. "
        "The Feed displays discoveries as short posts: a title and 2–3 source-grounded "
        "summary sentences. Set metadata.url to the original source, selection_reason "
        "to the actual interest match, and image_url to a source-provided image if available. "
        "Use content_type=source_summary. The UI displays the source link, image and reason "
        "separately. Do not write a long article, tutorial or suggested experiment. Read the "
        "source and omit unsupported claims; a headline or popularity stats are not evidence."
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
                    "metadata": {
                        "type": "object",
                        "description": "Source and provenance for a Feed read. Use only sources actually read.",
                        "properties": {
                            "url": {"type": "string", "description": "Original HTTP(S) source URL."},
                            "category": {"type": "string", "description": "Relevant interest category."},
                            "selection_reason": {"type": "string", "description": "Brief, factual reason this matches an interest; no invented personal history."},
                            "image_url": {"type": "string", "description": "Optional HTTP(S) image from the source page, never a fabricated URL."},
                            "content_type": {"type": "string", "enum": ["source_summary", "source_excerpt"]},
                        },
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
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        from src.core.intelligence.dashboard_feed import append_entry

        entry = await append_entry(
            kind=kind, title=title, body=body,
            metadata=metadata,
            action=action, pinned=bool(pinned), expires_in_hours=expires_in_hours)

        if not entry:
            logger.warning(f"[ UPDATE_DASHBOARD ] Failed to append entry: {title!r} ({kind})")
            return {"success": False, "error": "Failed to write dashboard entry"}

        logger.info(f"[ UPDATE_DASHBOARD ] Appended {kind} entry: {title!r}")
        return {"success": True, "result": f"Dashboard entry added: {title}", "entry_id": entry.get("id")}
