"""
Show YouTube Video — display tool for inline chat player cards.

Supervisor calls this after research_agent (or web_search) returns a real YouTube URL.
The UI renders YouTubePlayerCard from the structured tool result — no markdown tags.
"""

import re
from typing import Dict, Any, Optional

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display

_YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
_URL_PATTERNS = (
    re.compile(r"(?:v=|/)([0-9A-Za-z_-]{11})"),
    re.compile(r"youtu\.be/([0-9A-Za-z_-]{11})"),
    re.compile(r"embed/([0-9A-Za-z_-]{11})"),
)


def extract_youtube_video_id(value: str) -> Optional[str]:
    """Extract an 11-char YouTube video ID from a raw ID or URL."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if _YOUTUBE_ID_RE.match(raw):
        return raw
    lower = raw.lower()
    if "youtube.com" not in lower and "youtu.be" not in lower:
        return None
    for pattern in _URL_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    return None


@tool_display("Showing YouTube video: {title}...")
class ShowYouTubeVideoTool(BaseTool):
    """Display a YouTube video inline in the chat UI."""

    name = "show_youtube_video"
    description = (
        "Display a YouTube video as an inline player card in the chat. "
        "Call this on the supervisor after web research returns a real youtube.com/watch URL. "
        "Provide the video_id or full URL plus the exact title from search results — never invent IDs."
    )
    category = "display"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Video title exactly as returned by web_search (required).",
                    },
                    "video_id": {
                        "type": "string",
                        "description": "11-character YouTube video ID (preferred if known).",
                    },
                    "url": {
                        "type": "string",
                        "description": "Full YouTube watch URL — used when video_id is not provided.",
                    },
                },
                "required": ["title"],
            },
        }

    def validate_params(
        self,
        title: str = None,
        video_id: Optional[str] = None,
        url: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[bool, Optional[str]]:
        if not title or not str(title).strip():
            return False, "title is required."
        if not video_id and not url:
            return False, "Provide video_id or url."
        candidate = extract_youtube_video_id(video_id or url or "")
        if not candidate:
            return False, "Could not parse a valid YouTube video ID from video_id/url."
        return True, None

    async def execute(
        self,
        title: str,
        video_id: Optional[str] = None,
        url: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        resolved_id = extract_youtube_video_id(video_id or url or "")
        if not resolved_id:
            return {"success": False, "error": "Invalid YouTube video_id or url."}

        clean_title = str(title).strip()
        return {
            "success": True,
            "result": f"Showing: {clean_title}",
            "video_id": resolved_id,
            "youtube_video_id": resolved_id,
            "youtube_title": clean_title,
            "caption": clean_title,
        }


def youtube_display_payload(result: Any) -> Dict[str, str]:
    """Extract UI payload fields from a tool result dict for stream broadcast."""
    if not isinstance(result, dict):
        return {}
    fields: Dict[str, str] = {}
    vid = result.get("youtube_video_id") or result.get("video_id")
    title = result.get("youtube_title")
    if vid:
        fields["youtube_video_id"] = str(vid)
    if title:
        fields["youtube_title"] = str(title)
    return fields
