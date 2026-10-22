"""
GroupMe Tools (Read-Only)
Optimized for local models: compact responses, server-side filtering by sender/links/query,
and automatic extraction of URLs and media attachments.
"""

import os
import re
import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("GroupMeTools")

_URL_REGEX = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+', re.IGNORECASE)


def _get_groupme_token() -> Optional[str]:
    """Retrieve the GroupMe Personal Access Token from config or environment."""
    token = (
        ConfigManager.get_secret("groupme.access_token")
        or os.getenv("GROUPME_ACCESS_TOKEN")
    )
    return token.strip() if token else None


def _resolve_group(group_id_or_name: str, headers: Dict[str, str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a group name substring or ID to (group_id, group_name, error)."""
    group_id = group_id_or_name.strip()
    if group_id.isdigit():
        return group_id, group_id, None

    try:
        url = "https://api.groupme.com/v3/groups?per_page=50&omit=memberships"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            all_groups = data.get("response", []) or []

        q_lower = group_id.lower()
        # 1. Exact match first
        for g in all_groups:
            name = g.get("name") or ""
            if name.lower() == q_lower:
                return str(g.get("id")), name, None

        # 2. Substring match
        for g in all_groups:
            name = g.get("name") or ""
            if q_lower in name.lower():
                return str(g.get("id")), name, None

        available = [g.get("name") for g in all_groups[:8]]
        return None, None, f"No group found matching '{group_id}'. Available groups: {', '.join(filter(None, available))}"
    except Exception as e:
        return None, None, f"Failed resolving group: {e}"


def _extract_and_filter_messages(
    raw_msgs: List[Dict[str, Any]],
    sender_filter: Optional[str] = None,
    query_filter: Optional[str] = None,
    has_links_only: bool = False,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """
    Process, filter, and extract links/attachments from raw messages.
    Returns compact, high-density dictionaries tailored for local LLM token budgets.
    """
    sender_clean = sender_filter.strip().lower() if sender_filter else None
    query_clean = query_filter.strip().lower() if query_filter else None

    filtered = []

    for m in raw_msgs:
        sender = (m.get("name") or "Unknown").strip()
        text = (m.get("text") or "").strip()
        system = bool(m.get("system", False))

        # Skip system membership joins if we're filtering
        if system and (sender_clean or query_clean or has_links_only):
            continue

        # Filter by sender if specified
        if sender_clean and sender_clean not in sender.lower():
            continue

        # Extract links from text
        text_links = _URL_REGEX.findall(text)

        # Extract attachment URLs (images, shared files, polls)
        attachment_links = []
        attachments = m.get("attachments") or []
        for att in attachments:
            if isinstance(att, dict):
                att_type = att.get("type", "attachment")
                att_url = att.get("url")
                if att_url:
                    attachment_links.append(f"{att_url} ({att_type})")
                elif att_type == "poll":
                    poll_title = att.get("title") or "Poll"
                    attachment_links.append(f"[Poll: {poll_title}]")

        all_links = list(dict.fromkeys(text_links + attachment_links))

        # Filter by has_links if specified
        if has_links_only and not all_links:
            continue

        # Filter by keyword if specified
        if query_clean:
            matches_text = query_clean in text.lower()
            matches_link = any(query_clean in l.lower() for l in all_links)
            matches_sender = query_clean in sender.lower()
            if not (matches_text or matches_link or matches_sender):
                continue

        created_at = m.get("created_at")
        dt_str = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if created_at else ""

        entry: Dict[str, Any] = {
            "sender": sender,
            "text": text if text else ("[Attachment only]" if attachments else "[Empty]"),
            "time": dt_str,
        }
        if all_links:
            entry["links"] = all_links

        filtered.append(entry)
        if len(filtered) >= limit:
            break

    # Return chronological order (oldest to newest)
    return list(reversed(filtered))


@tool_display("Listing GroupMe groups")
class ListGroupMeGroupsTool(BaseTool):
    """List accessible GroupMe group chats."""

    name = "list_groupme_groups"
    description = (
        "List your accessible GroupMe group chats with their names and IDs. "
        "Use this to find a group before reading messages."
    )
    category = "messaging"
    is_side_effecting = False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        token = _get_groupme_token()
        if not token:
            return {"success": False, "error": "GroupMe access token not configured."}

        url = "https://api.groupme.com/v3/groups?per_page=50&omit=memberships"
        try:
            req = urllib.request.Request(url, headers={"X-Access-Token": token, "User-Agent": "ChadAI/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_groups = data.get("response", []) or []
                
                groups = [
                    {"id": str(g.get("id")), "name": g.get("name")}
                    for g in raw_groups
                ]

                return {
                    "success": True,
                    "count": len(groups),
                    "groups": groups,
                }
        except Exception as e:
            logger.error(f"Failed to list GroupMe groups: {e}")
            return {"success": False, "error": str(e)}


@tool_display("Reading GroupMe messages")
class ReadGroupMeMessagesTool(BaseTool):
    """Read recent messages from a GroupMe group chat with optional sender or link filtering."""

    name = "read_groupme_messages"
    description = (
        "Read recent messages from a GroupMe group chat. "
        "Supports filtering by sender name (e.g. sender='Savannah') or link status (has_links=True) "
        "to return concise, compact answers without overwhelming the context window."
    )
    category = "messaging"
    is_side_effecting = False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "group": {
                        "type": "string",
                        "description": "Group name (e.g. 'ACFresh', 'Algorithms') or group ID.",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Optional: Filter messages to a specific sender name (e.g. 'Savannah').",
                    },
                    "has_links": {
                        "type": "boolean",
                        "description": "Optional: If True, only returns messages that contain links/URLs or media attachments.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of matching messages to return (default: 15, max: 30).",
                        "default": 15,
                    },
                },
                "required": ["group"],
            },
        }

    async def execute(
        self,
        group: str,
        sender: Optional[str] = None,
        has_links: bool = False,
        limit: int = 15,
        **kwargs
    ) -> Dict[str, Any]:
        token = _get_groupme_token()
        if not token:
            return {"success": False, "error": "GroupMe access token not configured."}

        headers = {"X-Access-Token": token, "User-Agent": "ChadAI/1.0"}
        group_id, group_name, err = _resolve_group(group, headers)
        if err:
            return {"success": False, "error": err}

        # If filtering by sender or links, fetch up to 100 messages from API to find matches
        fetch_limit = 100 if (sender or has_links) else max(1, min(30, int(limit)))
        url = f"https://api.groupme.com/v3/groups/{group_id}/messages?limit={fetch_limit}"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_msgs = data.get("response", {}).get("messages", []) or []

            output_limit = max(1, min(30, int(limit)))
            filtered = _extract_and_filter_messages(
                raw_msgs,
                sender_filter=sender,
                has_links_only=has_links,
                limit=output_limit
            )

            return {
                "success": True,
                "group_name": group_name,
                "count": len(filtered),
                "messages": filtered,
            }
        except Exception as e:
            logger.error(f"Failed to read GroupMe messages: {e}")
            return {"success": False, "error": str(e)}


@tool_display("Searching GroupMe messages")
class SearchGroupMeMessagesTool(BaseTool):
    """Search for specific topics, keywords, or shared links in a GroupMe group chat."""

    name = "search_groupme_messages"
    description = (
        "Search GroupMe messages by keyword, topic, or sender in a specific group. "
        "Useful for questions like 'What link did Savannah send?' or 'Did anyone post about homework?'"
    )
    category = "messaging"
    is_side_effecting = False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "group": {
                        "type": "string",
                        "description": "Group name (e.g. 'ACFresh', 'Algorithms') or group ID.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Keyword, phrase, or topic to search for (e.g. 'bible', 'poll', 'schedule').",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Optional: Filter search to a specific sender name (e.g. 'Savannah').",
                    },
                    "has_links": {
                        "type": "boolean",
                        "description": "Optional: If True, only returns results containing links or attachments.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matching results to return (default: 10, max: 20).",
                        "default": 10,
                    },
                },
                "required": ["group"],
            },
        }

    async def execute(
        self,
        group: str,
        query: Optional[str] = None,
        sender: Optional[str] = None,
        has_links: bool = False,
        limit: int = 10,
        **kwargs
    ) -> Dict[str, Any]:
        token = _get_groupme_token()
        if not token:
            return {"success": False, "error": "GroupMe access token not configured."}

        headers = {"X-Access-Token": token, "User-Agent": "ChadAI/1.0"}
        group_id, group_name, err = _resolve_group(group, headers)
        if err:
            return {"success": False, "error": err}

        # Fetch up to 100 recent messages from GroupMe to search through
        url = f"https://api.groupme.com/v3/groups/{group_id}/messages?limit=100"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_msgs = data.get("response", {}).get("messages", []) or []

            output_limit = max(1, min(20, int(limit)))
            filtered = _extract_and_filter_messages(
                raw_msgs,
                sender_filter=sender,
                query_filter=query,
                has_links_only=has_links,
                limit=output_limit
            )

            return {
                "success": True,
                "group_name": group_name,
                "matches_count": len(filtered),
                "messages": filtered,
            }
        except Exception as e:
            logger.error(f"Failed to search GroupMe messages: {e}")
            return {"success": False, "error": str(e)}
