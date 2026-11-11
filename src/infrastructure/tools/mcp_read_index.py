"""Index live MCP read capabilities and reauthorize them before execution."""

import asyncio
import copy
import re
import time
from pathlib import PureWindowsPath
from typing import Any

from src.core.tools import failure, limits
from src.infrastructure.tools.schema.argument_validation import validate_arguments
from src.tools.base_tool import BaseTool


_GITHUB_PACKAGE = "@modelcontextprotocol/server-github"
# Exact read operations audited for the legacy 2025.4.8 GitHub server.
_GITHUB_READS = frozenset({
    "search_repositories", "get_file_contents", "list_commits", "list_issues",
    "search_code", "search_issues", "search_users", "get_issue", "get_pull_request",
    "list_pull_requests", "get_pull_request_files", "get_pull_request_status",
    "get_pull_request_comments", "get_pull_request_reviews",
})
_ALIAS_PART = re.compile(r"[a-zA-Z0-9_-]+")


def _read_allowed(server: Any, tool: dict) -> bool:
    annotations = tool.get("annotations") or {}
    if annotations.get("readOnlyHint") is False or annotations.get("destructiveHint") is True:
        return False
    name = getattr(server, "name", None)
    command = getattr(server, "command", ()) or ()
    if name == "github" and any(arg in (_GITHUB_PACKAGE, f"{_GITHUB_PACKAGE}@2025.4.8") for arg in command):
        return tool["name"] in _GITHUB_READS
    if annotations.get("readOnlyHint") is True:
        return True
    # Preserve Scout's existing inspection tools on their built-in connectors.
    if name == "playwright" and "@playwright/mcp" in command:
        return tool["name"] == "browser_snapshot"
    return (
        name == "windows-semantic-ui" and bool(command)
        and PureWindowsPath(command[0]).name.lower() == "sbroenne.windowsmcp.exe"
        and tool["name"] in {"ui_find", "ui_read"}
    )


def approved_reads(server: Any, tools: list[dict]) -> dict[str, dict]:
    excluded = set((getattr(server, "config", None) or {}).get("excluded_tools") or [])
    return {
        tool["name"]: tool for tool in tools
        if tool.get("name") and tool["name"] not in excluded and _read_allowed(server, tool)
    }


async def list_live_tools(server: Any) -> list[dict]:
    return await asyncio.wait_for(server.list_tools(), timeout=limits.get("tool_timeout_default_s"))


def binding_failure(name: str, reason: str, *, failure_class: str = failure.UNAVAILABLE) -> dict:
    return failure.failure(
        reason, failure_class=failure_class, retryable=False, tool_name=name,
        dispatched=False, binding_unavailable=True,
        next_actions=["Report the capability gap or choose another approved read for the brief."],
    )


async def execute_read(server: Any, metadata: dict, name: str, arguments: dict, registry: Any) -> Any:
    args, invalid = validate_arguments(
        name, arguments, metadata.get("inputSchema") or {"type": "object"}, None, registry=registry,
    )
    if invalid is not None:
        return invalid
    tool = {**metadata, "mcp_server": server, "source": "mcp"}
    executor = registry._routing_engine.route_to_executor(tool)
    return await executor.execute(tool, args)


class MCPReadTool(BaseTool):
    is_side_effecting = False
    category = "mcp_reads"

    def __init__(self, index: "MCPReadIndex", server: Any, metadata: dict):
        super().__init__()
        self.index = index
        self.server = server
        self.operation = metadata["name"]
        self.name = f"{server.name}__{self.operation}"
        self.description = f"{server.name}: {metadata.get('description') or self.operation}"
        self.metadata = copy.deepcopy(metadata)

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.metadata.get("inputSchema") or {"type": "object"},
        }

    async def execute(self, **kwargs: Any) -> Any:
        current = await self.index.live_binding(self)
        if current is None:
            return binding_failure(self.name, "MCP read is no longer available or approved.")
        if kwargs.get("tool_registry") is self.index.registry:
            del kwargs["tool_registry"]
        return await execute_read(current.server, current.metadata, self.name, kwargs, self.index.registry)


def named_mcp_reads(query: str, candidates: dict) -> dict:
    """Keep reads for a named connector in the hybrid menu for the model to select."""
    query_words = set(re.findall(r"[a-z0-9_-]+", (query or "").lower()))
    return {
        name: tool for name, tool in candidates.items()
        if isinstance(tool, MCPReadTool) and (tool.server.name.lower() in query_words or name.lower() in query_words)
    }


class MCPReadIndex:
    def __init__(self, registry: Any):
        self.registry = registry
        self.refreshed: dict[str, tuple[Any, float]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def servers(self) -> dict:
        mcp = getattr(self.registry, "mcp_registry", None)
        return dict(mcp.get_servers()) if mcp is not None else {}

    def replace(self, server: Any, tools: list[dict]) -> None:
        registry = self.registry
        aliases = {}
        if _ALIAS_PART.fullmatch(server.name) and "__" not in server.name and not server.name.endswith("_"):
            for operation, metadata in approved_reads(server, tools).items():
                if _ALIAS_PART.fullmatch(operation):
                    tool = MCPReadTool(self, server, metadata)
                    aliases[tool.name] = tool

        names = registry._categories.get("mcp_reads", [])
        existing = {
            name for name in names
            if isinstance((tool := registry._tools.get(name)), MCPReadTool) and tool.server.name == server.name
        }
        for name in existing - aliases.keys():
            registry._tools.pop(name)
            registry._tool_sources.pop(name, None)
            names.remove(name)
        for name, tool in aliases.items():
            registry.register_tool(tool, category=tool.category, force=name in existing)
        self.refreshed[server.name] = (server, time.monotonic())

    async def _refresh_server(self, server: Any, *, force: bool = False) -> None:
        async with self.locks.setdefault(server.name, asyncio.Lock()):
            previous, updated = self.refreshed.get(server.name, (None, 0.0))
            if not force and previous is server and time.monotonic() - updated < limits.get("mcp_read_index_ttl_s"):
                return
            try:
                tools = await list_live_tools(server)
            except Exception:
                tools = []
            if self.servers().get(server.name) is server:
                self.replace(server, tools)

    async def refresh(self) -> None:
        servers = self.servers()
        for name, (previous, _) in list(self.refreshed.items()):
            if servers.get(name) is not previous:
                self.replace(previous, [])
                self.refreshed.pop(name)
        await asyncio.gather(*(self._refresh_server(server) for server in servers.values()))

    async def live_binding(self, tool: MCPReadTool) -> MCPReadTool | None:
        if self.servers().get(tool.server.name) is not tool.server:
            return None
        await self._refresh_server(tool.server, force=True)
        current = self.registry._tools.get(tool.name)
        if self.servers().get(tool.server.name) is tool.server and isinstance(current, MCPReadTool):
            if current.server is tool.server and current.operation == tool.operation:
                return current
        return None


def read_index(registry: Any) -> MCPReadIndex:
    index = getattr(registry, "_mcp_read_index", None)
    if not isinstance(index, MCPReadIndex):
        index = registry._mcp_read_index = MCPReadIndex(registry)
    return index


async def refresh_mcp_reads(registry: Any) -> None:
    if isinstance(getattr(registry, "_tools", None), dict) and getattr(registry, "mcp_registry", None) is not None:
        await read_index(registry).refresh()
