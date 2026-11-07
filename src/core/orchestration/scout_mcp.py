"""Scout MCP gateway: discover live schemas and dispatch only declared reads."""
from langchain_core.tools import StructuredTool
from src.core.tools import failure
from src.infrastructure.tools.schema.argument_validation import validate_arguments


def gateway(registry):
    mcp = getattr(registry, "mcp_registry", None)
    if mcp is None or not mcp.get_servers():
        return None

    async def scout_mcp_read(server_name: str, tool_name: str = "", arguments: dict | None = None) -> dict:
        """Discover approved read schemas, or call an exact listed operation."""
        server = mcp.get(server_name)
        if server is None:
            return failure.failure("MCP server is not connected", failure_class=failure.CONFIG,
                                   tool_name="scout_mcp_read", dispatched=False)
        try:
            tools = await server.list_tools()
            from src.core.orchestration.mcp_classification import read_allowed
            reads = [t for t in tools if read_allowed(server, t)]
            if not tool_name:
                return {"success": True, "server": server_name, "tools": reads,
                        "blocked_count": len(tools) - len(reads),
                        "policy": "Audited GitHub reads or explicit readOnlyHint=true; writes and unknown operations denied."}
            tool = next((t for t in reads if t.get("name") == tool_name), None)
            if tool is None:
                return failure.failure("MCP operation is not an approved read-only tool", failure_class=failure.BLOCKED,
                                       tool_name="scout_mcp_read", dispatched=False)
            args, invalid = validate_arguments(tool_name, arguments or {}, tool.get("inputSchema") or {})
            if invalid:
                return invalid
            from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
            return await MCPExecutor().execute({"name": tool_name, "mcp_server": server, "source": "mcp"}, args)
        except Exception as exc:
            return failure.failure("MCP read failed: " + type(exc).__name__, failure_class=failure.UNKNOWN,
                                   tool_name="scout_mcp_read")

    names = ", ".join(sorted(mcp.get_servers()))
    return StructuredTool.from_function(coroutine=scout_mcp_read, name="scout_mcp_read",
        description="Read-only MCP access. Connected servers: " + names +
        ". First call with server_name only to discover approved schemas. "
        "Then call an exact listed tool_name with arguments. Unknown and write operations are blocked.")
