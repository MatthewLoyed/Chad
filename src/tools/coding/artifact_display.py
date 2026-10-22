"""
Structured display payload for verified web artifacts (auto-open preview panel).

Mirrors show_youtube_video.youtube_display_payload — tools set auto_preview on the
result dict; executors broadcast it on tool_end for the UI to open the artifact panel.
"""

from __future__ import annotations

import os
from typing import Any, Dict

_PREVIEWABLE_TOOLS = frozenset({"build_html_file", "write_file"})
_HTML_EXTENSIONS = frozenset({".html", ".htm"})


def mark_auto_preview_if_html(result: Dict[str, Any], filename: str) -> None:
    """Set auto_preview on a successful tool result when the artifact is previewable HTML."""
    if not result.get("success"):
        return
    if not result.get("download_token"):
        return
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _HTML_EXTENSIONS:
        result["auto_preview"] = True


def artifact_display_payload(tool_name: str, result: Any) -> Dict[str, Any]:
    """Extract UI fields from a tool result for stream broadcast."""
    if not isinstance(result, dict):
        return {}
    if not result.get("auto_preview"):
        return {}
    if not result.get("download_token") or not result.get("filename"):
        return {}
    if tool_name not in _PREVIEWABLE_TOOLS:
        return {}
    return {
        "auto_preview": True,
        "download_token": result["download_token"],
        "filename": result["filename"],
    }
