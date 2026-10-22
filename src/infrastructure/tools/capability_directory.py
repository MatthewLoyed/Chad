"""Small supervisor directory for native tools not already covered by agent/MCP rosters."""
from __future__ import annotations

import re
from typing import Any

from src.infrastructure.tools.delegate_routing import is_supervisor_delegate_only

_FAMILIES = (
    ("Personal memory", {"memory"}),
    ("Gmail / email", {"gmail", "email", "inbox"}),
    ("Contacts", {"contacts", "contact"}),
    ("Google Drive / documents", {"drive", "docs", "document", "sheets"}),
    ("Calendar", {"calendar", "event", "events"}),
    ("Public web", {"web", "youtube"}),
    ("Local files", {"file", "directory", "local", "filesystem", "pdf"}),
    ("Apps / windows", {"app", "window", "windows"}),
    ("Finance", {"financial", "transactions", "subscriptions", "liabilities", "investment"}),
)


def build_capability_directory(registry: Any, loaded_names: set[str]) -> str:
    """Advertise registered, supervisor-callable native tools without binding them."""
    groups: dict[str, list[str]] = {}
    categories = getattr(registry, "_categories", {})
    sources = getattr(registry, "_tool_sources", {})
    for schema in registry.get_all_schemas():
        if not isinstance(schema, dict):
            continue
        name = schema.get("name") or (schema.get("function") or {}).get("name")
        if not name or is_supervisor_delegate_only(name, categories, sources):
            continue
        if str(sources.get(name, "")).startswith("mcp"):
            continue  # covered once by the live connector roster
        tokens = set(re.split(r"[^a-z0-9]+", name.lower()))
        for label, keywords in _FAMILIES:
            if tokens & keywords:
                groups.setdefault(label, []).append(name)
                break
    lines = ["\n\n### NATIVE CAPABILITY DIRECTORY",
             "Registered examples only; access is confirmed by tool results. "
             "For DISCOVER entries, use search_for_tool with the service and operation, "
             "then follow the returned schema. Other services may also be discoverable."]
    for label, _ in _FAMILIES:
        names = sorted(set(groups.get(label, [])), key=lambda n: (n not in loaded_names, n))
        if names:
            examples = ", ".join(f"{name} [{'LOADED' if name in loaded_names else 'DISCOVER'}]" for name in names[:2])
            lines.append(f"- {label}: {examples}")
    if not groups:
        lines.append("- No native capability groups available in this registry.")
    return "\n".join(lines)
