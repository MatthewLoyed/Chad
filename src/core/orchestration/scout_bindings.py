"""Planner dispatch schema and Scout's read-only MCP gateway."""

from typing import Any

from langchain_core.tools import StructuredTool

from src.core.orchestration.scout_tool_policy import scout_tool_allowed
from src.core.tools import failure
from src.infrastructure.tools.mcp_read_index import (
    MCPReadTool,
    approved_reads,
    binding_failure,
    execute_read,
    list_live_tools,
)
from src.infrastructure.tools.schema.argument_validation import wrapper_feedback
from src.infrastructure.tools.schema.schema_builder import attach_parameters_schema
from src.tools.system.dispatch_scout import DispatchScoutTool


def build_dispatch_scout_tool(registry: Any = None, menu: list[dict] | None = None) -> StructuredTool:
    native = DispatchScoutTool()
    approved = {
        entry["name"] for entry in menu or []
        if entry.get("name") and scout_tool_allowed(entry["name"], registry)
    }

    async def dispatch(brief: str, tools: list[str] | None = None) -> dict:
        requested = list(dict.fromkeys(tools or []))
        for name in requested:
            if name not in approved or not scout_tool_allowed(name, registry):
                return binding_failure(name, "Tool is outside the confirmed Scout read menu.", failure_class=failure.BLOCKED)
            raw = getattr(registry, "_tools", {}).get(name)
            if isinstance(raw, MCPReadTool) and await raw.index.live_binding(raw) is None:
                return binding_failure(name, "MCP read is no longer available or approved.")
            try:
                bound = registry.get_langchain_tool_by_name(name)
            except Exception:
                bound = None
            if bound is None:
                return binding_failure(name, "Tool schema could not be bound for Scout.")
        result = await native.execute(brief=brief)
        return {**result, "requested_tools": requested}

    tool = StructuredTool.from_function(
        name=native.name, coroutine=dispatch,
        description=native.description + " Include exact approved read tools for Scout when available.",
    )
    schema = tool.args_schema.model_json_schema()
    schema["properties"]["tools"] = (
        {"type": "array", "items": {"type": "string", "enum": sorted(approved)}} if approved
        else {"type": "array", "items": {"type": "string"}, "maxItems": 0}
    )
    tool.handle_validation_error = wrapper_feedback(native.name, schema)
    return attach_parameters_schema(tool, schema)


def build_mcp_read_gateway(registry: Any) -> StructuredTool | None:
    mcp = getattr(registry, "mcp_registry", None)
    servers = dict(mcp.get_servers()) if mcp is not None else {}
    if not servers:
        return None

    async def scout_mcp_read(server_name: str, tool_name: str = "", arguments: dict | None = None) -> Any:
        server = mcp.get(server_name)
        if server is None:
            return binding_failure("scout_mcp_read", "MCP server is unavailable.")
        try:
            tools = await list_live_tools(server)
        except Exception:
            return binding_failure("scout_mcp_read", "Unable to list the server's MCP reads.")
        if mcp.get(server_name) is not server:
            return binding_failure("scout_mcp_read", "MCP server changed during discovery.")
        reads = approved_reads(server, tools)
        if not tool_name:
            return {"success": True, "server": server_name, "tools": list(reads.values())}
        if tool_name not in reads:
            return binding_failure(
                "scout_mcp_read", "MCP operation is outside the approved read catalog.", failure_class=failure.BLOCKED,
            )
        return await execute_read(server, reads[tool_name], tool_name, arguments or {}, registry)

    return StructuredTool.from_function(
        name="scout_mcp_read", coroutine=scout_mcp_read,
        description=(
            f"Read from MCP servers: {', '.join(sorted(servers))}. "
            "Use server_name alone to list approved reads, then supply an exact tool_name and its arguments."
        ),
    )
