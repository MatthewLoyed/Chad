"""
Chad Lite Client (Limb)
Connects to the Cloud Brain and executes local hardware/OS tools.
"""

import asyncio
import os
import subprocess
import sys
import time
import websockets
import logging
from typing import Optional, Any
from pathlib import Path
from urllib.parse import urlparse

# Ensure the root directory is in the path
root_dir = str(Path(__file__).parents[2])
if root_dir not in sys.path:
    sys.path.append(root_dir)

from src.remote.protocol import ChadMessage, MessageType
from src.shared.logger import setup_logger
from src.core.tools.tool_timeouts import get_tool_timeout
from src.shared.config_manager import ConfigManager
from src.shared.frozen_bootstrap import is_frozen
from src.shared.paths import get_project_root

logger = setup_logger("ChadLimb")
logger.setLevel(logging.INFO)  # Enable detailed logging for troubleshooting

# Reconnection backoff: initial delay, then exponential (capped) for subsequent retries
INITIAL_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 60.0

# Minimum time between local-Brain autostart attempts. A freshly spawned Brain
# takes several seconds to bind :8767; this stops the connect() retry loop
# from spawning a second/third one while the first is still booting.
BRAIN_AUTOSTART_COOLDOWN_S = 45.0

# Consecutive failed health probes required before we conclude the Brain is
# genuinely down and autostart a local one. A single 1.5s probe miss is NOT
# enough: in prod the Brain is already running under the supervisor, and a
# momentary miss (heavy turn, GC, mid-restart) would otherwise spawn a competing
# Brain. That competitor calls free_tcp_port() on boot and KILLS the incumbent —
# dropping our just-established connection and producing the classic
# "connect -> disconnect -> connect" flap. Requiring several consecutive misses
# means a truly-absent Brain (dev, nothing listening) still autostarts, while a
# transiently-slow-but-alive Brain never gets clobbered.
BRAIN_AUTOSTART_MIN_CONSECUTIVE_FAILURES = 3


def _resolve_server_url(override: Optional[str] = None) -> str:
    """Server URL: explicit override > env CHAD_SERVER_URL > config chad.server_url > default."""
    if override is not None and override.strip():
        return override.strip()
    url = os.getenv("CHAD_SERVER_URL")
    if url is not None and url.strip():
        return url.strip()
    url = ConfigManager.get("chad.server_url")
    if url is not None and str(url).strip():
        final_url = str(url).strip()
    else:
        final_url = "ws://localhost:8767"

    # Hybrid hygiene: Ensure cloud URLs use /ws to avoid HTTP route collisions on /
    # but only if original had no path
    from urllib.parse import urlparse
    parsed = urlparse(final_url)
    if not parsed.path or parsed.path == "/":
        final_url = final_url.rstrip("/") + "/ws"
    
    return final_url


def resolve_http_base(override: Optional[str] = None) -> str:
    """HTTP origin for REST calls (no WebSocket /ws path suffix)."""
    from urllib.parse import urlparse

    url = _resolve_server_url(override)
    normalized = url.replace("ws://", "http://").replace("wss://", "https://")
    parsed = urlparse(normalized)
    return f"{parsed.scheme}://{parsed.netloc}"


class ChadLimb:
    def __init__(self, server_url: Optional[str] = None, tool_registry: Optional[Any] = None):
        self.server_url = _resolve_server_url(server_url)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Support both unified token name and legacy name
        self.token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        if not self.token:
            logger.error("[ CRITICAL ] : No CHAD_SERVER_TOKEN (or CHAD_REMOTE_TOKEN) found. Authorization will fail.")
        self.is_running = True
        self.status_callback = None  # Optional[Callable[[str, dict], None]]
        
        # Local Tool Registry for executing Brain requests
        if tool_registry:
            logger.debug("Limb: Reusing existing ToolRegistry instance.")
            self.registry = tool_registry
        else:
            logger.info("Limb: Creating new ToolRegistry instance.")
            from src.infrastructure.tools import ToolRegistry
            self.registry = ToolRegistry()
            self.registry.discover_all()
            
        self.mcp_registry: Optional['MCPRegistry'] = None
        self._reconnect_event = asyncio.Event()
        self._brain_autostart_attempted_at: Optional[float] = None

    async def _background_mcp_discovery(self, mcp_config):
        """Run MCP discovery in background to unblock connection."""
        try:
            for name, cfg in mcp_config.items():
                # Resolve env vars
                env = cfg.get('env', {})
                resolved_env = {}
                for k, v in env.items():
                    if isinstance(v, str) and v.startswith('${') and v.endswith('}'):
                        resolved_env[k] = os.getenv(v[2:-1], '')
                    else:
                        resolved_env[k] = v
                self.mcp_registry.register(name, cfg['command'], resolved_env, cfg)
            
            await self.registry.discover_mcp_servers(self.mcp_registry)
            logger.debug(f"Limb: Discovered {len(mcp_config)} MCP servers.")
        except Exception as e:
            logger.error(f"Failed to discover MCP servers in Limb: {e}")

    def _is_server_healthy(self) -> bool:
        """Perform a lightweight, silent HTTP GET to check if the server is up."""
        import urllib.request
        from urllib.parse import urlparse
        try:
            parsed = urlparse(self.server_url)
            scheme = "https" if parsed.scheme in ["wss", "https"] else "http"
            health_url = f"{scheme}://{parsed.netloc}/health"
            
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "ChadLimb-HealthProbe/1.0"}
            )
            with urllib.request.urlopen(req, timeout=1.5) as response:
                return response.status == 200
        except Exception:
            return False

    def _is_local_brain_url(self) -> bool:
        """True when server_url points at this machine — never autostart a
        Brain for a remote/hosted URL."""
        from urllib.parse import urlparse
        host = urlparse(self.server_url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1")

    def _try_autostart_brain(self) -> None:
        """Best-effort spawn of a local Brain when none is reachable.

        Only fires for localhost server URLs and only once per cooldown window,
        so it never fights with an existing (dev or prod-supervised) Brain — the
        health check that gates this call already confirms nothing answered
        /health, and the cooldown stops repeat spawns while the new one boots.
        """
        if not self._is_local_brain_url():
            return
        child = getattr(self, "_autostarted_brain", None)
        if child is not None and child.poll() is None:
            return
        now = time.monotonic()
        if (
            self._brain_autostart_attempted_at is not None
            and (now - self._brain_autostart_attempted_at) < BRAIN_AUTOSTART_COOLDOWN_S
        ):
            return

        # Final guard against the connect->disconnect->connect flap: re-probe health
        # right before spawning. If the Brain answered in the gap since the caller's
        # last probe, it's alive — and spawning a competitor now would kill it (its
        # free_tcp_port() claims :8767 on boot). Bail and let the connect loop use it.
        if self._is_server_healthy():
            logger.info("Brain became reachable before autostart — skipping spawn.")
            return

        self._brain_autostart_attempted_at = now

        home = get_project_root()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(home))
        env.setdefault("CHAD_LOG_LEVEL", "INFO")
        # Spawn the Brain on the same port we're actually targeting, not
        # whatever PORT happens to be inherited (defaults to 8767) — otherwise
        # a GUI pointed at a non-default CHAD_SERVER_URL (e.g. a second,
        # isolated instance) silently autostarts a Brain on the wrong port and
        # the health check never succeeds.
        try:
            target_port = urlparse(self.server_url).port
        except Exception:
            target_port = None
        if target_port:
            env["PORT"] = str(target_port)

        if is_frozen():
            # dist/Chad/Chad.exe and dist/Chad-Brain/Chad-Brain.exe are sibling
            # folders under dist/ (see scripts/prod/build_exe.ps1).
            brain_exe = Path(sys.executable).resolve().parent.parent / "Chad-Brain" / "Chad-Brain.exe"
            if not brain_exe.exists():
                logger.warning(f"Brain unreachable and no local Chad-Brain.exe found at {brain_exe}; not autostarting.")
                return
            cmd = [str(brain_exe)]
        else:
            server_script = home / "src" / "remote" / "server.py"
            if not server_script.exists():
                logger.warning(f"Brain unreachable and no local server.py found at {server_script}; not autostarting.")
                return
            cmd = [sys.executable, str(server_script)]

        log_dir = home / "data" / "gui_logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Brain unreachable at {self.server_url}; autostarting local Brain: {cmd}")
            with open(log_dir / "brain_autostart.log", "a", encoding="utf-8") as out_log, \
                 open(log_dir / "brain_autostart.err.log", "a", encoding="utf-8") as err_log:
                self._autostarted_brain = subprocess.Popen(
                    cmd,
                    cwd=str(home),
                    env=env,
                    stdout=out_log,
                    stderr=err_log,
                    creationflags=creationflags,
                )
        except Exception as e:
            logger.error(f"Failed to autostart local Brain: {e}")

    def trigger_reconnect(self):
        """Immediately trigger a reconnection attempt.

        Sets the reconnect event (wakes health-poll backoff) and force-closes any
        open websocket so a hung AUTH/bootstrap wait cannot swallow the request.
        """
        logger.info("Immediate reconnect requested by user.")
        self._reconnect_event.set()
        ws = self.websocket
        if ws is None:
            return
        self.websocket = None
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._force_close_ws(ws))
        except RuntimeError:
            # Called from a non-async thread (pywebview bridge). Best-effort close.
            try:
                close = getattr(ws, "close", None)
                if close is None:
                    return
                result = close()
                if asyncio.iscoroutine(result):
                    # No loop here — drop the coroutine; the peer will time out.
                    result.close()  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug(f"force-close ws failed: {e}")

    async def _force_close_ws(self, ws) -> None:
        try:
            await ws.close()
        except Exception:
            pass

    async def connect(self):
        """Connect to the Chad Server and maintain the connection with exponential backoff."""
        # Initialize MCP Tools if configured
        if not self.mcp_registry:
            from src.core.tools.mcp_registry import MCPRegistry
            self.mcp_registry = MCPRegistry()
            mcp_config = ConfigManager.get('mcp_servers', {})
            if mcp_config:
                asyncio.create_task(self._background_mcp_discovery(mcp_config))

        retry_delay = INITIAL_RECONNECT_DELAY
        while self.is_running:
            # 1. Quietly poll the /health endpoint first to avoid loud WebSocket connection stack traces
            is_ready = False
            first_attempt = True
            consecutive_failures = 0
            while self.is_running and not is_ready:
                is_ready = await asyncio.to_thread(self._is_server_healthy)
                if not is_ready:
                    consecutive_failures += 1
                    if first_attempt:
                        logger.warning(f"Server not ready at {self.server_url} yet. Entering exponential retry loop...")
                        first_attempt = False

                    # Only autostart after the Brain has missed several probes in a
                    # row — a single transient miss against a live (prod-supervised)
                    # Brain must never spawn a competitor that would kill it. See
                    # BRAIN_AUTOSTART_MIN_CONSECUTIVE_FAILURES.
                    if consecutive_failures >= BRAIN_AUTOSTART_MIN_CONSECUTIVE_FAILURES:
                        await asyncio.to_thread(self._try_autostart_brain)

                    if self.status_callback:
                        self.status_callback("reconnecting", {"error": "Server offline", "next_retry": retry_delay})
                    
                    # Sleep in 0.5s increments to be responsive to self.is_running changes
                    sleep_time = float(retry_delay)
                    steps = max(1, int(sleep_time * 2))
                    for _ in range(steps):
                        if not self.is_running:
                            break
                        if self._reconnect_event.is_set():
                            break
                        await asyncio.sleep(0.5)
                    
                    if self._reconnect_event.is_set():
                        self._reconnect_event.clear()
                        retry_delay = INITIAL_RECONNECT_DELAY
                        break  # Break the health poll loop to try connecting right now!
                    
                    retry_delay = min(retry_delay * 2, MAX_RECONNECT_DELAY)

            if not self.is_running:
                break

            # Reset retry delay for websocket attempt after successful health probe
            retry_delay = INITIAL_RECONNECT_DELAY

            # 2. Once the server is healthy, establish the persistent WebSocket connection
            try:
                logger.info(f"Connecting to Chad Brain at {self.server_url}...")
                if self.status_callback:
                    self.status_callback("connecting", {"url": self.server_url})
                
                # Use ping settings to prevent silent disconnects/timeouts
                # open_timeout: 45s to allow for Cold Boot / Handshake processing
                # ping_interval/timeout: 12s/30s to stay ahead of persistent Cloud proxies
                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Origin": "http://localhost:5173"
                }
                
                # Compatibility for websockets v14+ (additional_headers) vs older (extra_headers)
                connect_kwargs = {
                    "open_timeout": 45,
                    "ping_interval": 20,
                    "ping_timeout": 60
                }
                
                # We try both names to be robust across versions
                try:
                    async with websockets.connect(self.server_url, additional_headers=headers, **connect_kwargs) as ws:
                        retry_delay = INITIAL_RECONNECT_DELAY  # Reset backoff on success
                        await self._handle_ws_connection(ws)
                except TypeError as e:
                    if "additional_headers" in str(e):
                        async with websockets.connect(self.server_url, extra_headers=headers, **connect_kwargs) as ws:
                            retry_delay = INITIAL_RECONNECT_DELAY  # Reset backoff on success
                            await self._handle_ws_connection(ws)
                    else:
                        raise e

            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, TimeoutError, asyncio.TimeoutError) as e:
                msg = f"Connection failed: {e}. Retrying in {retry_delay}s..."
                logger.error(msg)
                if self.status_callback:
                    self.status_callback("reconnecting", {"error": str(e), "next_retry": retry_delay})
                self.websocket = None
                
                # Sleep using increments check for _reconnect_event
                sleep_time = float(retry_delay)
                steps = max(1, int(sleep_time * 2))
                for _ in range(steps):
                    if not self.is_running:
                        break
                    if self._reconnect_event.is_set():
                        break
                    await asyncio.sleep(0.5)
                
                if self._reconnect_event.is_set():
                    self._reconnect_event.clear()
                    retry_delay = INITIAL_RECONNECT_DELAY
                else:
                    retry_delay = min(retry_delay * 2, MAX_RECONNECT_DELAY)
            except Exception as e:
                msg = f"Unexpected error in Limb: {e}. Retrying in {retry_delay}s..."
                logger.error(msg, exc_info=True)
                if self.status_callback:
                    self.status_callback("error", {"error": str(e), "next_retry": retry_delay})
                self.websocket = None
                
                # Sleep using increments check for _reconnect_event
                sleep_time = float(retry_delay)
                steps = max(1, int(sleep_time * 2))
                for _ in range(steps):
                    if not self.is_running:
                        break
                    if self._reconnect_event.is_set():
                        break
                    await asyncio.sleep(0.5)
                
                if self._reconnect_event.is_set():
                    self._reconnect_event.clear()
                    retry_delay = INITIAL_RECONNECT_DELAY
                else:
                    retry_delay = min(retry_delay * 2, MAX_RECONNECT_DELAY)

    async def _handle_ws_connection(self, ws):
        """Internal handler for an active WebSocket connection."""
        self.websocket = ws
        # Reset backoff on success (handled in caller, but we can set self.websocket here)
        logger.debug(f"WebSocket connected successfully. Sending auth...")
        if self.status_callback:
            self.status_callback("authenticating", {})

        # 1. Send Auth Handshake
        auth_msg = ChadMessage(MessageType.AUTH, auth_token=self.token)
        logger.debug(f"Sending auth token: {(self.token or '')[:10]}...")
        await ws.send(auth_msg.to_json())
        logger.debug("Auth message sent. Waiting for server response...")

        # 2. Listen for messages
        async for message_str in ws:
            # logger.info(f"Received message: {message_str[:100]}...")
            msg = ChadMessage.from_json(message_str)
            await self.handle_message(msg)

    async def handle_message(self, msg: ChadMessage):
        """Handle messages from the Brain."""
        # logger.info(f"Received message from Brain: {msg.type}")
        
        if msg.type == MessageType.AUTH.value:
            if msg.payload.get("status") == "success":
                print("\n[ SYSTEM ] : Authenticated and linked with Chad Cloud Brain.")
                print("[ SYSTEM ] : Type a message and press Enter to chat with Chad.\n")
            else:
                logger.error(f"[ AUTH ] : Authentication failed: {msg.payload.get('error')}")

        elif msg.type in [MessageType.ERROR.value, MessageType.SYSTEM_ERROR.value]:
            error_msg = msg.payload.get("error", "Unknown server error")
            print(f"\n[ ERROR ] : {error_msg}\n")
            logger.error(f"[ SERVER ERROR ] : {error_msg}")

        elif msg.type == MessageType.TOOL_REQUEST.value:
            tool_name = msg.payload.get("tool")
            params = msg.payload.get("params", {})
            timeout = get_tool_timeout(tool_name)
            print(f"  [ LOCAL TOOL ] : Executing {tool_name}...")

            try:
                # Execute locally
                result = await asyncio.wait_for(
                    self.registry.execute_tool(tool_name, params),
                    timeout=timeout
                )

                response = ChadMessage(
                    MessageType.TOOL_RESPONSE,
                    result,
                    correlation_id=msg.id
                )
                await self.websocket.send(response.to_json())
            except asyncio.TimeoutError:
                logger.error(f"Tool '{tool_name}' execution timed out after {timeout}s")
                error_resp = ChadMessage(
                    MessageType.TOOL_RESPONSE,
                    {"success": False, "error": f"Tool execution timed out after {timeout}s"},
                    correlation_id=msg.id
                )
                await self.websocket.send(error_resp.to_json())
            except Exception as e:
                logger.error(f"Error executing local tool: {e}")
                error_resp = ChadMessage(
                    MessageType.TOOL_RESPONSE,
                    {"success": False, "error": str(e)},
                    correlation_id=msg.id
                )
                await self.websocket.send(error_resp.to_json())

        elif msg.type == MessageType.AI_THOUGHT.value:
            # Brain is 'thinking' or 'starting tool'
            state = msg.payload.get("state")
            if state == "tool_start":
                print(f"  [ CHAD ] : {msg.payload.get('action')}")
            elif state == "thinking":
                # print(f"  (Thinking: {msg.payload.get('text')})", end="\r")
                pass
            elif state == "reasoning":
                 # print(f"  (Reasoning: {msg.payload.get('text')[:50]}...)", end="\r")
                 pass

        elif msg.type == MessageType.AI_RESPONSE.value:
            text = msg.payload.get("text")
            print(f"\n[ CHAD ] : {text}\n")
            print("You: ", end="", flush=True)

    async def send_voice_state(self, listening: bool) -> bool:
        """Tell the Brain the voice-mode listening state (drives the desktop buddy)."""
        if not self.websocket:
            return False
        try:
            msg = ChadMessage(MessageType.VOICE_STATE, {"listening": bool(listening)})
            await self.websocket.send(msg.to_json())
            return True
        except Exception as e:
            logger.debug(f"Failed to send voice state: {e}")
            return False

    async def send_fast_track_invoke(self, alias: str) -> bool:
        """Invoke an allowlisted '/<alias>' fast-track command, skipping the LLM/planner."""
        if not self.websocket:
            return False
        try:
            msg = ChadMessage(MessageType.FAST_TRACK_INVOKE, {"alias": alias})
            await self.websocket.send(msg.to_json())
            return True
        except Exception as e:
            logger.debug(f"Failed to send fast-track invoke: {e}")
            return False

    async def send_workflow_run(self, workflow_id: str) -> bool:
        """Trigger a saved workflow, skipping the LLM/planner."""
        if not self.websocket:
            return False
        try:
            msg = ChadMessage(MessageType.WORKFLOW_RUN, {"workflow_id": workflow_id})
            await self.websocket.send(msg.to_json())
            return True
        except Exception as e:
            logger.debug(f"Failed to send workflow run: {e}")
            return False

    async def interactive_loop(self):
        """Main loop for user input."""
        while self.is_running:
            try:
                # Use to_thread to avoid blocking event loop with input()
                text = await asyncio.to_thread(input, "You: ")
                if text.lower() in ['exit', 'quit']:
                    self.is_running = False
                    break
                
                if text.strip():
                    await self.send_input(text)
                    # wait a bit for processing to avoid overlapping "You:" prompts
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Input error: {e}")

    async def send_input(self, text: str, image_base64: Optional[str] = None, content_blocks: Optional[list] = None, conversation_id: Optional[str] = None, category: Optional[str] = None, turn_id: Optional[str] = None, thinking_enabled: Optional[bool] = None, delivery: Optional[str] = None, domain: Optional[str] = None, retry: Optional[bool] = None, keep_count: Optional[int] = None, metadata: Optional[dict] = None) -> bool:
        """Helper to send user input to the Brain. Returns True if sent."""
        if self.websocket:
            try:
                from src.shared.turn_delivery import resolve_delivery
                payload = {"text": text}
                if image_base64:
                    payload["image_base64"] = image_base64
                if content_blocks:
                    payload["content_blocks"] = content_blocks
                if conversation_id:
                    payload["conversation_id"] = conversation_id
                if category:
                    payload["category"] = category
                if turn_id:
                    payload["turn_id"] = turn_id
                if thinking_enabled is not None:
                    payload["thinking_enabled"] = thinking_enabled
                if domain:
                    payload["domain"] = domain
                if retry:
                    payload["retry"] = True
                if keep_count is not None:
                    payload["keep_count"] = keep_count
                if metadata:
                    payload["metadata"] = metadata
                payload["delivery"] = resolve_delivery(category, delivery)
                    
                msg = ChadMessage(MessageType.USER_INPUT, payload)
                await self.websocket.send(msg.to_json())
                return True
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                return False
        else:
            if not getattr(self, '_suppress_disconnect_msg', False):
                print("[ ERROR ] : Not connected to Brain.")
            return False

    async def send_ui_turn_mode(self, ui_mode: str) -> bool:
        """Notify the Brain of the chat-input mode dropdown (auto/agent/chat/plan)."""
        if self.websocket:
            try:
                from src.remote.protocol import ChadMessage, MessageType
                msg = ChadMessage(
                    MessageType.SET_UI_TURN_MODE,
                    {"ui_mode": (ui_mode or "auto").strip().lower()},
                )
                await self.websocket.send(msg.to_json())
                return True
            except Exception as e:
                logger.error(f"Failed to send UI turn mode: {e}")
                return False
        return False

if __name__ == "__main__":
    limb = ChadLimb()
    
    async def main():
        # RUN BOTH the connection and the interactive loop
        await asyncio.gather(
            limb.connect(),
            limb.interactive_loop()
        )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting Chad Limb...")
