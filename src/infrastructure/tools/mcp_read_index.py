"""Live, read-filtered MCP aliases for planning selection and Scout bindings."""
import asyncio
import copy
import re
import time
from src.tools.base_tool import BaseTool
from src.core.tools import failure
from src.core.orchestration.mcp_classification import read_allowed


class MCPReadTool(BaseTool):
    is_side_effecting = False
    category = "mcp_reads"

    def __init__(self, index, server_name, metadata):
        super().__init__()
        self.index = index
        self.server_name = server_name
        self.operation = metadata["name"]
        self.name = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name + "__" + self.operation)
        self.description = f"{server_name}: " + metadata.get("description", self.operation)
        self.parameters = copy.deepcopy(metadata.get("inputSchema") or {"type": "object"})

    def get_schema(self):
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    async def execute(self, **kwargs):
        from src.core.tools.repetition import current_guard
        guard = current_guard()
        blocked = getattr(guard, "_mcp_unavailable", {})
        if self.name in blocked:
            return blocked[self.name]
        def remember(result):
            blocked[self.name] = result
            guard._mcp_unavailable = blocked
            return result
        invalid = await self.index.validate_binding(self.name)
        if invalid:
            return remember(invalid)
        current = self.index.registry._tools.get(self.name)
        from src.infrastructure.tools.schema.argument_validation import validate_arguments
        args, invalid = validate_arguments(self.name, {k: v for k, v in kwargs.items() if k != "tool_registry"}, current.parameters)
        if invalid:
            return invalid
        try:
            from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
            server = self.index.servers().get(self.server_name)
            result = await MCPExecutor().execute({"name": self.operation, "mcp_server": server, "source": "mcp"}, args)
            if isinstance(result, dict) and result.get("success") is False:
                # Never silently retry an MCP transport failure in this binding.
                return remember({**result, "retryable": False, "binding_unavailable": True})
            return result
        except Exception as exc:
            return remember(binding_failure(self.name, type(exc).__name__))


def binding_failure(name, reason):
    return failure.failure("Requested Scout binding unavailable: " + reason,
        failure_class=failure.CONFIG, retryable=False, tool_name=name,
        dispatched=False, binding_unavailable=True,
        next_actions=["Report this capability gap to Planner; do not retry or replace it with web search."])


class MCPReadIndex:
    def __init__(self, registry):
        self.registry = registry
        self.refreshed = {}
        self.locks = {}
        self.failures = {}

    def servers(self):
        mcp = getattr(self.registry, "mcp_registry", None)
        return mcp.get_servers() if mcp else {}

    def replace(self, server, tools):
        # Replace atomically between awaits; remove revoked/deleted entries and wrapper caches.
        for name, tool in list(self.registry._tools.items()):
            if isinstance(tool, MCPReadTool) and tool.server_name == server.name:
                self.registry._tools.pop(name, None)
                self.registry._tool_sources.pop(name, None)
                for names in self.registry._categories.values():
                    if name in names:
                        names.remove(name)
        excluded = set((getattr(server, "config", {}) or {}).get("excluded_tools", []))
        for metadata in tools:
            if metadata.get("name") not in excluded and read_allowed(server, metadata):
                tool = MCPReadTool(self, server.name, metadata)
                existing = self.registry._tools.get(tool.name)
                if existing is not None:
                    raise ValueError("MCP read alias collision")
                self.registry.register_tool(tool, category="mcp_reads", source="local", force=True)
        self.refreshed[server.name] = time.monotonic()

    async def refresh(self, server_name=None, force=False):
        from src.shared.config_manager import ConfigManager
        ttl = float(ConfigManager.get("tools.mcp_read_index_ttl_s", 30))
        servers = dict(self.servers())
        # Disconnected servers cannot remain advertised.
        for name in set(self.refreshed) - set(servers):
            self.replace(type("Gone", (), {"name": name, "config": {}})(), [])
            self.refreshed.pop(name, None)
        async def one(name, server):
            lock = self.locks.setdefault(name, asyncio.Lock())
            async with lock:
                if not force and time.monotonic() - self.refreshed.get(name, float("-inf")) < ttl:
                    return
                try:
                    self.replace(server, await server.list_tools())
                    self.failures.pop(name, None)
                except Exception as exc:
                    self.replace(server, [])
                    self.failures[name] = type(exc).__name__
        await asyncio.gather(*(one(n, s) for n, s in servers.items() if server_name is None or n == server_name))

    async def validate_binding(self, name):
        tool = self.registry._tools.get(name)
        if not isinstance(tool, MCPReadTool):
            return binding_failure(name, "not in current approved read index")
        await self.refresh(tool.server_name, force=True)
        if name not in self.registry._tools:
            return binding_failure(name, self.failures.get(tool.server_name, "server disconnected or tool removed/revoked"))
        return None


def read_index(registry):
    index = getattr(registry, "_mcp_read_index", None)
    if index is None:
        index = MCPReadIndex(registry)
        registry._mcp_read_index = index
    return index
