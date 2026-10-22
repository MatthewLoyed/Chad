# core/mcp_registry.py
"""
MCP Registry - Lazy-loaded Model Context Protocol server management.
Uses the official MCP Python SDK for stdio-based servers.

Architecture:
- MCPServerEntry: Lazy wrapper for a single server (connects on-demand)
- MCPRegistry: Central registry with pre-warm cache support
"""

import asyncio
import subprocess
import os
import logging
import shutil
import time
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.shared.child_reaper import (
    register_children,
    snapshot_own_children,
    unregister_child,
)

try:
    import psutil
except ImportError:  # optional at import time; only used to skip already-dead pids
    psutil = None

logger = logging.getLogger("MCPRegistry")


def _resolve_executable(command: str) -> str:
    """Resolve a bare launcher name (``npx``, ``npm``, ``uvx``) to its real path.

    On Windows these are ``.cmd``/``.bat`` shims (``npx`` is really ``npx.CMD``). Python's
    ``asyncio.create_subprocess_exec`` — unlike the shell — does NOT append PATHEXT extensions,
    so spawning bare ``npx`` dies with ``WinError 2: The system cannot find the file specified``,
    silently killing every npx-based stdio MCP (github, brave-search, google-drive, fetch, …).
    ``shutil.which`` honors PATHEXT, so it finds ``npx.CMD``. No-op on POSIX and for commands that
    already include a path/extension or that ``which`` can't find (left as-is so the original error
    surfaces)."""
    expanded = os.path.expandvars(command)
    if os.name != "nt" or os.path.dirname(expanded) or os.path.splitext(expanded)[1]:
        return expanded
    return shutil.which(expanded) or expanded


class MCPServerEntry:
    """Lazy-loaded MCP server wrapper. Server only spawns when connect() is called."""
    
    def __init__(self, name: str, command: List[str], env: Dict[str, str] = None, config: Dict[str, Any] = None):
        self.name = name
        # Playwright is hard-pinned to ChadBrowser — never the user's real Chrome.
        if name == "playwright":
            from src.shared.chad_browser_profile import enforce_playwright_command
            command = enforce_playwright_command(command, server_name=name)
        self.command = list(command)
        self.env = env or {}
        self.config = config or {}
        self.session: Optional[ClientSession] = None
        self._stdio_context = None
        self._session_context = None
        self._tools: List[Dict] = []
        self._call_lock = asyncio.Lock()
        self._last_call_time = 0.0
        self._managed_pids: List[int] = []
    
    async def connect(self) -> "MCPServerEntry":
        """Spawn the server process and complete handshake (on-demand)."""
        if self.session:
            return self
        
        # Create an event to signal when the connection is ready or failed
        ready_event = asyncio.Event()
        error_info = {"exception": None}
        self._stop_event = asyncio.Event()
        self._managed_pids = []
        # Diff our own children across the spawn to learn the server's pid — `stdio_client`
        # keeps the process object private and hands back only the read/write streams.
        pids_before = set(snapshot_own_children())

        async def _connection_task():
            try:
                # Merge with current environment and expand placeholders (e.g. %VAR% or $VAR)
                expanded_env = {k: os.path.expandvars(str(v)) for k, v in self.env.items()}
                merged_env = {**os.environ, **expanded_env}
                expanded_command = _resolve_executable(self.command[0])
                expanded_args = [os.path.expandvars(arg) for arg in self.command[1:]]
                
                params = StdioServerParameters(
                    command=expanded_command,
                    args=expanded_args,
                    env=merged_env
                )
                
                logger.debug(f"[ MCP ] : Connecting to '{self.name}'...")
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.session = session
                        self._snapshot_managed_pids(pids_before)
                        logger.debug(f"[ MCP ] : Connected to '{self.name}' successfully.")
                        ready_event.set()
                        
                        # Keep connection alive until disconnect is called
                        await self._stop_event.wait()
                        
                        # Graceful exit via context managers
                        self.session = None
                        logger.debug(f"[ MCP ] : Disconnected from '{self.name}'.")
            except Exception as e:
                logger.error(f"[ MCP ] : Connection to '{self.name}' failed: {e}")
                error_info["exception"] = e
                ready_event.set() # Wake up the waiter even on failure
            finally:
                self.session = None

        # Start the background task
        self._task = asyncio.create_task(_connection_task())
        
        # Wait for either success or failure. OAuth bridges (e.g. mcp-remote) need
        # time for the user to complete browser sign-in on first connect.
        connect_timeout = float(self.config.get('connect_timeout_seconds', 25.0))
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=connect_timeout)
        except asyncio.TimeoutError:
            self._task.cancel()
            self._force_kill_children()
            raise TimeoutError(
                f"Connection to MCP server '{self.name}' timed out after {connect_timeout}s"
            )
        
        if error_info["exception"]:
            self._force_kill_children()
            raise error_info["exception"]
            
        if not self.session:
            self._force_kill_children()
            raise RuntimeError(f"Failed to establish session with MCP server '{self.name}'")
            
        return self

    def _claim_by_cmdline(self, pids: List[int]) -> List[int]:
        """Narrow a child-PID diff to the processes that are actually OURS.

        Servers connect concurrently (`get_all_tool_definitions` gathers them), so a plain
        before/after diff can attribute a sibling's node process to this entry — and then
        disconnecting one server would kill another's. Matching the child's command line
        against our own distinctive arguments (the package name, the script path) removes
        that cross-talk.

        Registration with the reaper deliberately happens on the UNFILTERED set elsewhere:
        over-registering only means a process gets reaped at exit, which is correct for
        every MCP child regardless of which entry owns it.
        """
        distinctive = [
            str(a) for a in self.command[1:]
            if isinstance(a, str) and len(a) > 3 and not a.startswith("-")
        ]
        if not psutil or not distinctive:
            return list(pids)
        claimed = []
        for pid in pids:
            try:
                cmdline = " ".join(psutil.Process(pid).cmdline()).lower()
            except Exception:  # noqa: BLE001 — race: the child may already be gone
                continue
            if any(token.lower() in cmdline for token in distinctive):
                claimed.append(pid)
        return claimed

    def _snapshot_managed_pids(self, pids_before: Optional[set] = None) -> None:
        """Record PIDs we must reap on disconnect, and hand them to the child reaper.

        Used to be Playwright-only, which meant every *other* server (a node process per
        npx-based MCP) had no owner at all: nothing tracked it and nothing killed it. On a
        hard process death — the #675 symptom — the whole fleet was orphaned, and repeated
        eval runs stacked fleet on fleet until the box had 42 stray node processes.

        The reaper registration is the part that survives a crash; `_managed_pids` is just
        for the graceful path.
        """
        try:
            appeared = [p for p in snapshot_own_children() if p not in (pids_before or set())]
            new_pids = self._claim_by_cmdline(appeared)
            self._managed_pids = new_pids
            # Kernel-level insurance: kill-on-job-close reaps these even if this process
            # is killed outright and neither disconnect() nor atexit ever runs. Registered
            # on `appeared`, not `new_pids`: at exit we want EVERY MCP child gone, and a
            # process wrongly attributed to a sibling would otherwise slip through.
            register_children(appeared)
            if new_pids:
                logger.debug(f"[ MCP ] : '{self.name}' owns child pid(s) {new_pids}.")
        except Exception as e:
            logger.debug(f"[ MCP ] : Could not snapshot pids for '{self.name}': {e}")

        if self.name == "playwright":
            # Playwright additionally spawns chrome out-of-tree (it can reparent), so the
            # child diff alone misses it — keep the profile-based lookup as well.
            try:
                from src.shared.chad_browser_profile import pids_locking_profile
                extra = [p for p in pids_locking_profile() if p not in self._managed_pids]
                self._managed_pids.extend(extra)
                register_children(extra)
            except Exception as e:
                logger.debug(f"[ MCP ] : Could not snapshot playwright pids: {e}")

    def _force_kill_children(self) -> None:
        """Reap server children that survive stdio teardown (Windows zombies)."""
        pids, self._managed_pids = list(self._managed_pids), []
        if self.name == "playwright":
            try:
                from src.shared.chad_browser_profile import kill_profile_locks
                killed = kill_profile_locks(extra_pids=pids)
                if killed:
                    logger.info(f"[ MCP ] : Reaped {killed} playwright/ChadBrowser process tree(s).")
                for pid in pids:
                    unregister_child(pid)
                return
            except Exception as e:
                logger.debug(f"[ MCP ] : Force-kill children failed: {e}")

        from src.core.utils.system_utils import kill_process_tree
        for pid in pids:
            try:
                if psutil and not psutil.pid_exists(pid):
                    unregister_child(pid)
                    continue
                kill_process_tree(pid)
                unregister_child(pid)
                logger.debug(f"[ MCP ] : Reaped '{self.name}' child tree pid={pid}.")
            except Exception as e:
                logger.debug(f"[ MCP ] : Could not reap pid={pid} for '{self.name}': {e}")


    async def disconnect(self) -> None:
        """Gracefully close the server connection and reap child processes."""
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        
        if hasattr(self, '_task'):
            # Give it a moment to clean up
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                logger.warning(f"[ MCP ] : Force-cancelled connection task for '{self.name}'")
            except Exception as e:
                logger.debug(f"[ MCP ] : Disconnect error for '{self.name}': {e}")
        
        self.session = None
        # stdio context exit is best-effort; on Windows chrome.exe often survives.
        self._force_kill_children()
    
    async def list_tools(self) -> List[Dict]:
        """Get available tools from the server."""
        if not self.session:
            await self.connect()
        
        result = await self.session.list_tools()
        self._tools = [t.model_dump() for t in result.tools]
        return self._tools
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool and return the result."""
        if not self.session:
            await self.connect()
        
        # Read timeout override from config, default to 120s safety cap
        timeout = float(self.config.get('timeout_seconds', 120.0))
        min_delay = self.config.get('min_delay_seconds', 0)

        # Inner executor wrapped in safety timeout
        async def _run_task():
            try:
                result = await asyncio.wait_for(
                    self.session.call_tool(tool_name, arguments), 
                    timeout=timeout
                )
                
                # REFINEMENT: If we only have one text block, return just the text for token efficiency
                return [c.model_dump() if hasattr(c, 'model_dump') else str(c) for c in result.content]
            except asyncio.TimeoutError:
                # Provide descriptive text for upstream error handlers
                raise TimeoutError(f"MCP execution timed out after {timeout}s.")

        # Standard path: Fast asynchronous dispatch
        if min_delay <= 0:
            return await _run_task()

        # Throttled path: Controlled serialization
        async with self._call_lock:
            now = time.monotonic()
            time_since_last = now - self._last_call_time
            if time_since_last < min_delay:
                wait_time = min_delay - time_since_last
                logger.debug(f"[ MCP ] Rate limiting '{self.name}': waiting {wait_time:.2f}s before {tool_name}.")
                await asyncio.sleep(wait_time)
            
            try:
                return await _run_task()
            finally:
                # Guaranteed release and timestamp capture for pacing
                self._last_call_time = time.monotonic()


class MCPRegistry:
    """
    Registry for lazy-loaded MCP servers.
    
    Usage:
        registry = MCPRegistry()
        from src.shared.paths import resolve_path
        registry.register("filesystem", ["npx", "-y", "@anthropic/mcp-filesystem-server", resolve_path("%USERPROFILE%/Documents")])
        await registry.prewarm_cache()  # Background package download
        
        # Later, on-demand:
        fs = await registry.get("filesystem").connect()
        result = await fs.call_tool("read_file", {"path": "README.md"})
    """
    
    def __init__(self):
        self._servers: Dict[str, MCPServerEntry] = {}
        self._tool_map: Dict[str, MCPServerEntry] = {}  # O(1) tool -> server lookup
        self._tool_cache: List[Dict] = None  # Cached tool definitions
        self._prewarm_procs: List[asyncio.subprocess.Process] = []
        self._prewarm_warmed: set = set()
    
    def register(self, name: str, command: List[str], env: Dict[str, str] = None, config: Dict[str, Any] = None) -> None:
        """Register a server configuration (does not spawn the process)."""
        # MCPServerEntry enforces ChadBrowser for playwright at construction time.
        self._servers[name] = MCPServerEntry(name, command, env, config)
        if name == "playwright":
            from src.shared.chad_browser_profile import extract_user_data_dir
            udd = extract_user_data_dir(self._servers[name].command)
            logger.info(f"[ MCP ] : Registered playwright with user-data-dir={udd}")
        else:
            logger.debug(f"[ MCP ] : Registered server '{name}'")
    
    def get(self, name: str) -> Optional[MCPServerEntry]:
        """Get a server entry by name (call .connect() to spawn)."""
        return self._servers.get(name)
    
    def list_servers(self) -> List[str]:
        """List all registered server names."""
        return list(self._servers.keys())
    
    def get_servers(self) -> Dict[str, MCPServerEntry]:
        """Return the dictionary of registered servers."""
        return self._servers

    async def disconnect_all(self) -> int:
        """Disconnect every connected server and reap its children. Returns how many.

        Nothing called ``MCPServerEntry.disconnect()`` anywhere in the codebase before
        this (#675) — the fleet was spawned and then simply abandoned, on clean exits as
        much as crashes. Call this from any owner's teardown path.

        Best-effort per server: one server refusing to close must not strand the rest.
        """
        connected = [e for e in self._servers.values() if getattr(e, "session", None)]
        for entry in connected:
            try:
                await entry.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ MCP ] : disconnect_all: '{entry.name}' failed: {e}")
        # Prewarm spawns (npx package downloads) are children too, and a run that ends
        # while one is still resolving leaves it behind.
        for proc in self._prewarm_procs:
            try:
                if proc.returncode is None:
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._prewarm_procs = []
        if connected:
            logger.info(f"[ MCP ] : Disconnected {len(connected)} server(s).")
        return len(connected)
    
    async def get_all_tool_definitions(self, use_cache: bool = True) -> List[Dict]:
        """Aggregate tool definitions from ALL registered servers.
        
        Args:
            use_cache: If True, return cached definitions if available.
        """
        # Return cached definitions if available and requested
        if use_cache and self._tool_cache is not None:
            logger.debug(f"[ MCP ] : Returning {len(self._tool_cache)} cached tool definitions.")
            return self._tool_cache
        
        all_tools = []
        self._tool_map.clear()
        
        async def _load_entry(entry):
            try:
                if not entry.session:
                    await entry.connect()
                return await entry.list_tools()
            except Exception as e:
                logger.error(f"[ MCP ] Failed to load tools from '{entry.name}': {e}")
                return []

        # Connect and query all servers in parallel
        results = await asyncio.gather(*[_load_entry(entry) for entry in self._servers.values()])
        
        for entry, tools in zip(self._servers.values(), results):
            for tool in tools:
                # Store server mapping BEFORE sanitization (for routing)
                self._tool_map[tool['name']] = entry
                
                # Sanitize tool schema for Gemini compatibility
                sanitized = self._sanitize_tool_schema(tool)
                all_tools.append(sanitized)
        
        # Cache the results
        self._tool_cache = all_tools
        logger.debug(f"[ MCP ] : Aggregated and cached {len(all_tools)} tools from {len(self._servers)} servers.")
        return all_tools
    
    def invalidate_cache(self) -> None:
        """Clear the tool cache. Call this when MCP servers change."""
        self._tool_cache = None
        logger.info("[ MCP ] : Tool cache invalidated.")
    
    def _sanitize_tool_schema(self, tool: Dict) -> Dict:
        """
        Convert MCP tool schema to Gemini-compatible function declaration.
        Gemini only accepts: name, description, parameters
        """
        name = tool.get('name', 'unknown')
        description = tool.get('description', '')
        # ------------------------------------

        sanitized = {
            'name': name,
            'description': description
        }
        
        # MCP uses 'inputSchema', Gemini uses 'parameters'
        input_schema = tool.get('inputSchema') or tool.get('parameters')
        if input_schema:
            # Extract only the fields Gemini supports
            sanitized['parameters'] = {
                'type': input_schema.get('type', 'object'),
                'properties': input_schema.get('properties', {}),
                'required': input_schema.get('required', [])
            }
        else:
            # Default empty parameters
            sanitized['parameters'] = {
                'type': 'object',
                'properties': {},
                'required': []
            }
        
        return sanitized
    
    async def route_and_call(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """O(1) routing via tool map."""
        entry = self._tool_map.get(tool_name)
        if not entry:
            raise ValueError(f"Tool '{tool_name}' not found in any registered MCP server.")
        return await entry.call_tool(tool_name, args)
    
    async def prewarm_cache(self) -> None:
        """
        Download npx MCP packages with bounded subprocess lifetimes.
        MCP stdio servers hang forever if left running, so each prewarm is
        timed out and killed rather than fire-and-forgot.
        """
        if os.getenv("CHAD_MCP_PREWARM", "true").lower() == "false":
            logger.debug("[ MCP ] : Package prewarm disabled via CHAD_MCP_PREWARM=false")
            return

        await asyncio.gather(
            *[self._cache_download(entry.command, entry.name) for entry in self._servers.values()],
            return_exceptions=True,
        )
    
    async def _cache_download(self, command: List[str], name: str) -> None:
        """Run npx to download a package, then kill the process if it hangs."""
        _t_start = time.monotonic()
        try:
            from src.shared.startup_timing import record_duration as _timing_record
        except Exception:
            _timing_record = None

        if len(command) < 3 or command[0].lower() not in ("npx", "npm"):
            return

        package_key = os.path.expandvars(str(command[2]))
        if package_key in self._prewarm_warmed:
            return

        try:
            # Only npx -y <package>; server flags would start a hanging stdio server.
            cache_cmd = [_resolve_executable(command[0]), *[os.path.expandvars(a) for a in command[1:3]]]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            proc = await asyncio.create_subprocess_exec(
                *cache_cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._prewarm_procs.append(proc)
            logger.debug(f"[ MCP ] : Pre-warming cache for '{name}' (pid={proc.pid})")

            try:
                await asyncio.wait_for(proc.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning(f"[ MCP ] : Prewarm for '{name}' timed out; killing pid={proc.pid}")
                from src.core.utils.system_utils import kill_process_tree
                kill_process_tree(proc.pid)
            finally:
                if proc in self._prewarm_procs:
                    self._prewarm_procs.remove(proc)

            self._prewarm_warmed.add(package_key)
        except Exception as e:
            logger.warning(f"[ MCP ] : Cache pre-warm failed for '{name}': {e}")
        finally:
            if _timing_record:
                _timing_record(f"  mcp_prewarm[{name}]", time.monotonic() - _t_start)
    
    async def apply_config_diff(self, new_config: Dict[str, Any]) -> None:
        """
        Hot-reload MCP servers from a new config dict without restarting.

        Disconnects servers that were removed or whose command/env changed,
        then registers new/changed servers and invalidates the tool cache.

        Args:
            new_config: Dict mapping server name -> {command, env, ...} (same shape as config.yaml mcp_servers)
        """
        old_names = set(self._servers.keys())
        new_names = set(new_config.keys())

        # Disconnect servers that are removed or have a changed command/env
        for name in list(old_names):
            entry = self._servers[name]
            new_entry_cfg = new_config.get(name, {})
            new_command = new_entry_cfg.get('command', [])
            new_env = new_entry_cfg.get('env', {})
            removed = name not in new_names
            changed = (entry.command != new_command or entry.env != new_env)
            if removed or changed:
                if entry.session:
                    await entry.disconnect()
                del self._servers[name]
                logger.info(f"[ MCP ] : {'Removed' if removed else 'Reloading changed'} server '{name}'")

        # Register new or changed servers
        for name, cfg in new_config.items():
            if name not in self._servers:
                command = cfg.get('command', [])
                env = cfg.get('env', {})
                config = {k: v for k, v in cfg.items() if k not in ('command', 'env')}
                self.register(name, command, env, config)
                logger.info(f"[ MCP ] : Registered server '{name}' via hot-reload")

        self.invalidate_cache()

    async def cleanup(self) -> None:
        """Disconnect all active servers and kill any lingering prewarm subprocesses."""
        from src.core.utils.system_utils import kill_process_tree

        for proc in list(self._prewarm_procs):
            if proc.returncode is None:
                try:
                    kill_process_tree(proc.pid)
                except Exception as e:
                    logger.debug(f"[ MCP ] : Failed to kill prewarm pid={proc.pid}: {e}")
        self._prewarm_procs.clear()

        for entry in self._servers.values():
            # Always disconnect playwright (even if session already None) so
            # _force_kill_children runs and no ChadBrowser chrome is left behind.
            if entry.session or entry.name == "playwright":
                await entry.disconnect()

        # Belt-and-suspenders: reap any orphaned playwright MCP / ChadBrowser locks.
        try:
            from src.shared.chad_browser_profile import kill_profile_locks
            kill_profile_locks()
        except Exception as e:
            logger.debug(f"[ MCP ] : Final profile-lock sweep failed: {e}")

        logger.info("[ MCP ] : All servers disconnected.")
