"""Supervisor prompt fragment: which MCP connectors are live this process."""
from __future__ import annotations

from typing import Any, Iterable, Optional

# Short capability lines for known connectors. Unknown servers still appear by name.
_MCP_HINTS: dict[str, str] = {
    "canva": "graphic design — generate/edit/export via call_mcp_tool; load_skill('canva') for SOP",
    "excalidraw": "live whiteboard — call_mcp_tool; load_skill('excalidraw')",
    "github": "repos/PRs/issues — call_mcp_tool; load_skill('github') for multi-step",
    "notion": "pages/databases — call_mcp_tool; load_skill('notion')",
    "google-drive": "Drive MCP (prefer native google_drive_* tools when available)",
    "playwright": "browser automation MCP (prefer delegate_to_browser_agent for named sites)",
    "brave-search": "Brave search MCP (prefer native web_search)",
    "todoist": "tasks — call_mcp_tool; load_skill('todoist')",
    "fetch": "fetch URL text — call_mcp_tool",
    "supabase": "Supabase project tools — call_mcp_tool",
    "spotify": "playback/playlists — call_mcp_tool; load_skill('spotify') when available",
}


def connected_mcp_server_names(tool_registry: Any) -> list[str]:
    """Names registered on the live MCP registry (enabled + credentialed at boot/connect)."""
    mcp_registry = getattr(tool_registry, "mcp_registry", None) if tool_registry else None
    if not mcp_registry:
        return []
    try:
        servers = mcp_registry.get_servers() or {}
    except Exception:
        return []
    return sorted(str(name) for name in servers.keys())


def build_active_mcp_roster(
    server_names: Optional[Iterable[str]] = None,
    *,
    tool_registry: Any = None,
) -> str:
    """Return a compact ### ACTIVE MCP CONNECTORS block for the supervisor system prompt."""
    names = list(server_names) if server_names is not None else connected_mcp_server_names(tool_registry)
    lines = [
        "\n\n### ACTIVE MCP CONNECTORS",
        "Use `call_mcp_tool(server_name=..., tool_name=..., arguments=...)` for these. "
        "Discover tool schemas under the on-disk `mcp/<server>/` folder if needed. "
        "Missing a server → tell the user Settings → Connectors (do not invent tools).",
    ]
    if not names:
        lines.append("- *(none connected this session)*")
        return "\n".join(lines)

    for name in names:
        hint = _MCP_HINTS.get(name)
        lines.append(f"- **{name}**" + (f": {hint}" if hint else ""))
    return "\n".join(lines)
