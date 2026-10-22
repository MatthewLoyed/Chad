"""
Chad Cloud Server (Headless Brain)
Lightweight hosting shell that delegates system construction to RemoteFactory
and handles incoming network routing through the LimbCoordinator.
"""

if __name__ == '__main__':
    # PyInstaller workers reuse this executable; do not boot another Brain.
    import multiprocessing
    multiprocessing.freeze_support()

import asyncio
import sys
import logging
import os
import ssl
import json
import time
import threading
from collections import defaultdict
from pathlib import Path
from typing import Dict, Set, Optional, Any
from contextlib import asynccontextmanager

# When packaged as Chad-Brain.exe (PyInstaller), point the process at the real
# repo (CHAD_HOME) and chdir into it BEFORE any cwd/project-root-relative paths
# are computed below. No-op from a source checkout. Mirrors main_gui.py.
from src.shared.frozen_bootstrap import bootstrap_frozen
bootstrap_frozen()

# Windows-specific event loop policy enforcement
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Startup timing collector — imported as early as possible so _T0 reflects
# process/module-import start, the earliest point we can measure from inside
# the process itself (external launch-to-import time is captured by the
# benchmark harness via process spawn timestamp).
from src.shared.startup_timing import mark as _timing_mark, dump_summary as _timing_dump_summary
_timing_mark("module_import_start")

# Ensure the root directory is in the path so we can import from src
root_dir = str(Path(__file__).parents[2])
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Stamp which git SHA this Brain process actually loaded (before any hot code paths).
from src.shared.runtime_identity import record_runtime_identity
record_runtime_identity("brain")

# Set log level for server process
os.environ["CHAD_LOG_LEVEL"] = os.getenv("CHAD_LOG_LEVEL", "INFO")

# Optional: Starlette + uvicorn
try:
    from starlette.applications import Starlette
    from starlette.websockets import WebSocket
    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False

from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.application.events import EventBus
from src.remote.protocol import ChadMessage, MessageType, _StarletteWSAdapter
from src.remote.endpoints import build_starlette_routes
from src.remote.limb_coordinator import LimbCoordinator
from src.remote.remote_factory import RemoteFactory
from src.remote import auth as _auth
from src.buddy import BuddyEngine

logger = setup_logger("ChadServer")


import functools


def _tailscale_exe() -> Optional[str]:
    """First `tailscale` binary that answers, or None if it isn't installed/running."""
    import subprocess
    for exe in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=3)
        except Exception:
            continue
        if out.returncode == 0:
            return exe
    return None


def _tailscale_magicdns_origin() -> tuple:
    """Origin for this machine's MagicDNS name, e.g. ``https://chad.tail1234.ts.net``.

    This is the origin a browser sends when the app is reached through
    ``tailscale serve`` — Serve terminates TLS on 443 under the MagicDNS hostname and
    proxies to the Brain, so the request arrives with a *hostname* Origin, never the
    tailnet-IP one ``_tailscale_ip_origins`` covers. Without this, Serve traffic is
    rejected by the /ws origin check even though it's the same machine on the same
    tailnet, which is exactly the setup an installable phone PWA needs (a service
    worker requires a secure context, and only Serve provides real HTTPS here).

    Port-less on purpose: Serve listens on 443, and browsers omit the default port
    from Origin.
    """
    exe = _tailscale_exe()
    if not exe:
        return ()
    import json
    import subprocess
    try:
        out = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0 or not out.stdout.strip():
            return ()
        name = str((json.loads(out.stdout).get("Self") or {}).get("DNSName") or "").strip()
    except Exception:
        return ()
    name = name.rstrip(".")
    if not name:
        return ()
    # Both schemes: Serve is always HTTPS, but a plain-HTTP reverse proxy under the
    # same name shouldn't be silently unreachable.
    return (f"https://{name}", f"http://{name}")


def _tailscale_ip_origins() -> tuple:
    """Browser origins for this machine's tailnet IPv4 address(es) — ``http(s)://<ip>:5173``
    (Vite dev) and ``:<PORT>`` (Brain-served PWA)."""
    import subprocess
    exe = _tailscale_exe()
    if not exe:
        return ()
    brain_port = int(os.getenv("PORT", 8767))
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=3)
    except Exception:
        return ()
    if out.returncode != 0 or not out.stdout.strip():
        return ()
    origins = []
    for ip in out.stdout.split():
        ip = ip.strip()
        if ip:
            origins.extend((
                f"http://{ip}:5173", f"https://{ip}:5173",
                f"http://{ip}:{brain_port}", f"https://{ip}:{brain_port}",
            ))
    return tuple(origins)


@functools.lru_cache(maxsize=1)
def _tailscale_origins() -> tuple:
    """Auto-detect browser origins that reach this machine over the tailnet, so a
    phone/PWA on the same tailnet is accepted on /ws and REST without manually
    setting CHAD_ALLOWED_ORIGINS.

    Covers both ways in: the raw tailnet IP (``http://100.x.y.z:5173`` / ``:<PORT>``)
    and the MagicDNS hostname that ``tailscale serve`` presents. Only THIS host's
    addresses are added — never a wildcard — and callers still authenticate with
    CHAD_SERVER_TOKEN. Best-effort: returns () if Tailscale isn't installed/running.
    Cached for the process lifetime; CHAD_ALLOWED_ORIGINS still augments for
    non-Tailscale setups.
    """
    origins = _tailscale_ip_origins() + _tailscale_magicdns_origin()
    if origins:
        logger.warning(f"[ NET ] : Auto-allowing Tailscale origins: {list(origins)}")
    return origins


def _prewarm_heavy_imports() -> None:
    """Pre-import the heavy ML stack on a background thread, off the boot critical path.

    Cold-start profiling (issue #182) showed ~16s of the ~22s to ``brain_ready`` was a
    single synchronous import: ``RemoteFactory`` lazily imports ``turn_coordinator`` ->
    ``langgraph_engine`` -> ``lc_wrapper`` -> ``langchain_core.language_models.base``,
    which does a module-level ``from transformers import GPT2TokenizerFast`` (a fallback
    GPT-2 tokenizer we never use), dragging in transformers + torch. On a cold disk this
    is dominated by file I/O and AV scanning of hundreds of .pyd/.dll files — work that
    releases the GIL. Running it in a daemon thread started before ``uvicorn.serve()``
    overlaps that cost with the ~3s socket bind and config load instead of serializing on
    the boot path; by the time ``_safe_initialize`` reaches the import, it's cached and
    returns instantly. Disable with ``CHAD_PREWARM_IMPORTS=false`` (e.g. for tests).
    """
    if os.getenv("CHAD_PREWARM_IMPORTS", "true").lower() in ("false", "0", "no"):
        return

    def _run():
        import time as _time
        from src.shared.startup_timing import record_duration as _rec, set_flag as _flag
        start = _time.perf_counter()
        try:
            # Exact module the boot path imports first; warms the whole langchain/
            # langgraph/transformers/torch chain transitively.
            import src.core.orchestration.turn_coordinator  # noqa: F401
            _flag("import_prewarm", "ok")
        except Exception as e:  # never let a prewarm failure affect boot
            _flag("import_prewarm", f"error:{type(e).__name__}")
            logger.warning(f"[ PREWARM ] : Heavy-import prewarm failed (non-fatal): {e}")
        finally:
            _rec("prewarm_heavy_imports_thread", _time.perf_counter() - start)

    import threading
    threading.Thread(target=_run, name="chad-import-prewarm", daemon=True).start()
    logger.info("[ PREWARM ] : Heavy-import prewarm thread started (off boot critical path).")


def _generate_and_persist_server_token() -> str:
    """Generate a random CHAD_SERVER_TOKEN and persist it to secrets.yaml.

    Runs once, on the first boot where no token is configured, so a fresh
    install never has a window where the 0.0.0.0-bound port accepts
    unauthenticated non-loopback connections.
    """
    import secrets as _secrets
    from src.infrastructure.repositories.config_repository import ConfigRepository

    token = _secrets.token_urlsafe(32)
    try:
        ConfigRepository().update_secret("CHAD_SERVER_TOKEN", token)
    except Exception as e:
        logger.error(f"[ SECURITY ] : Failed to persist generated CHAD_SERVER_TOKEN: {e}")
    return token


def _build_ssl_context() -> Optional[ssl.SSLContext]:
    pem = os.getenv("CHAD_SSL_PEM") or ConfigManager.get("chad.ssl_pem")
    cert = os.getenv("CHAD_SSL_CERT") or ConfigManager.get("chad.ssl_cert")
    key = os.getenv("CHAD_SSL_KEY") or ConfigManager.get("chad.ssl_key")
    
    if pem:
        pem = str(pem).strip()
        if not Path(pem).exists():
            logger.error(f"[ SSL ] : CHAD_SSL_PEM file not found: {pem}")
            raise SystemExit(1)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(pem)
        return ctx
    
    if cert and key:
        cert, key = str(cert).strip(), str(key).strip()
        if not Path(cert).exists() or not Path(key).exists():
            logger.error(f"[ SSL ] : SSL cert or key file not found.")
            raise SystemExit(1)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        return ctx
        
    return None


class ConnectionRateLimiter:
    def __init__(self, rate: float = 2.0, capacity: float = 5.0):
        self.rate = rate
        self.capacity = capacity
        self.buckets = defaultdict(lambda: (capacity, time.time()))
        self._lock = asyncio.Lock()

    async def acquire(self, ip: str) -> bool:
        async with self._lock:
            now = time.time()
            tokens, last_update = self.buckets[ip]
            elapsed = now - last_update
            replenished = tokens + elapsed * self.rate
            tokens = min(self.capacity, replenished)
            
            if tokens >= 1.0:
                self.buckets[ip] = (tokens - 1.0, now)
                return True
            else:
                self.buckets[ip] = (tokens, now)
                return False


class ChadServer:
    instance = None  # Static instance needed for REST endpoint references

    def __init__(self, host="0.0.0.0", port=None):
        ChadServer.instance = self
        
        # Connection rate limiting
        self.rate_limiter = ConnectionRateLimiter(rate=2.0, capacity=5.0)
        
        # Server Networking Config
        self.host = host
        self.port = port or int(os.getenv("PORT", 8767))
        self.ssl_context = _build_ssl_context()

        # Reference to the running uvicorn server, set in start(). Used by the
        # /api/update endpoint to request a graceful restart (supervisor relaunches).
        self._uvicorn = None
        
        # Global Identity Security
        self.token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        auth_mode = _auth.get_auth_mode()
        if auth_mode == "token" and not self.token:
            # Fresh install, nobody has set a token yet. The Brain binds
            # 0.0.0.0 (needed so a phone on the same Tailscale/LAN can reach
            # it), so leaving this unset must not mean "accept unauthenticated
            # connections from anyone who can reach the port" — generate one
            # now and persist it so the gap never exists, even for one boot.
            # Loopback callers (the paired desktop GUI) never needed this
            # token anyway; it only gates non-loopback callers (see auth.py).
            self.token = _generate_and_persist_server_token()
            logger.warning(
                f"[ SECURITY ] : No CHAD_SERVER_TOKEN was configured — generated "
                "one and saved it to config/secrets.yaml. Use Settings to pair "
                "a phone/PWA client. The token is not written to logs."
            )
        logger.info(f"[ AUTH ] : Mode = {auth_mode.upper()}")

        # Connected clients: ws → user_id (None in token mode, UUID string in jwt mode)
        self.authenticated_clients: Dict[Any, Optional[str]] = {}
        
        # Orchestration Event Bus
        self._event_bus = EventBus()
        
        # Verification: Ensure EventBus has 'get_instance' classmethod to prevent planning component failures
        if not hasattr(EventBus, 'get_instance'):
            logger.critical("🚨 CRITICAL ORCHESTRATION ERROR: EventBus lacks 'get_instance' classmethod! "
                            "This will break planning event publishing in supervisor.py. "
                            "Please verify EventBus singleton implementation immediately.")
            raise RuntimeError("EventBus lacks 'get_instance' classmethod")
        
        # System Status Control
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self.initialization_ready = asyncio.Event()
        
        # Coordinate inbound/outbound Limb communication
        self.coordinator = LimbCoordinator(self._event_bus, self.initialization_ready)
        self.coordinator.setup_event_subscriptions()
        self.buddy_engine = BuddyEngine(self._event_bus)
        self.buddy_bridge = self.buddy_engine  # legacy alias
        self.buddy_observer = None

        # System components populated by RemoteFactory during boot
        self.components: Dict[str, Any] = {}
        
        # Legacy aliases for cross-file references
        self.session_manager = None
        self._heartbeat_service = None

    def is_limb_connected(self) -> bool:
        """True if any desktop GUI limb / viewer is actively connected over WebSocket."""
        return bool(self.coordinator and self.coordinator.has_viewers)

    async def _safe_initialize(self):
        """Perform the comprehensive boot loop via RemoteFactory precisely once."""
        async with self._init_lock:
            if self._initialized:
                return
            
            logger.warning("[ BOOT ] : Safe initialize triggered. Invoking Factory bootstrap...")
            _timing_mark("safe_initialize_start")
            try:
                self.components = await RemoteFactory.initialize_remote_system(
                    event_bus=self._event_bus,
                    limb_coordinator=self.coordinator,
                    initialization_ready_event=self.initialization_ready
                )
                
                # Wire components back to coordinator for inbound routing
                self.coordinator.bind_components(
                    self.components.get("turn_coordinator"),
                    self.components.get("registry"),
                )
                
                # Store backward compatible references
                self.session_manager = self.components.get("session_manager")
                self._heartbeat_service = self.components.get("heartbeat_service")
                
                self._initialized = True
                _timing_mark("safe_initialize_complete")
                logger.warning("[ BOOT ] : Bootstrap successfully resolved.")

                from src.buddy.observer import BuddyObserver
                self.buddy_observer = BuddyObserver(self._event_bus, self.buddy_engine)
                await self.buddy_observer.start()

                _timing_mark("brain_ready")
                try:
                    _timing_dump_summary(logger=logger)
                except Exception as dump_err:
                    logger.error(f"[ BOOT ] : Failed to dump startup timing summary: {dump_err}")

                # Optional self-updating: poll origin/master on an interval when
                # `updates.auto_update` is enabled. Prod Brain only (port 8767) —
                # dev stack must never auto-pull master over an active feature branch.
                port = os.environ.get("PORT", "8767")
                if port == "8767":
                    self._auto_update_task = asyncio.create_task(self._auto_update_loop())
                else:
                    logger.info("[ BOOT ] : Auto-update disabled on non-prod Brain (port %s)", port)
            except Exception as e:
                logger.error(f"[ BOOT ] : Startup sequence failed: {e}", exc_info=True)
                # Unblock AUTH waiters. Without this, a failed bootstrap leaves every
                # GUI hung forever on "Connecting..." while /health still returns ok.
                if not self.initialization_ready.is_set():
                    self.initialization_ready.set()

    async def _auto_update_loop(self):
        """Periodically pull + apply updates from origin/master when
        `updates.auto_update` is enabled. Reuses the same routine as the one-click
        button, so it only restarts when actually behind. Interval is
        `updates.auto_update_interval_minutes` (default 60, min 5). The config is
        re-read each tick so toggling the setting takes effect without a restart."""
        from src.shared.config_manager import ConfigManager
        from src.remote.endpoints import perform_self_update
        # Small initial delay so a freshly-restarted Brain settles before checking.
        await asyncio.sleep(120)
        while True:
            try:
                enabled = bool(ConfigManager.get("updates.auto_update", False))
                interval_min = ConfigManager.get("updates.auto_update_interval_minutes", 60)
                try:
                    interval_min = max(5, int(interval_min))
                except (TypeError, ValueError):
                    interval_min = 60
                if enabled:
                    result = await perform_self_update(server=self)
                    if not result.get("success", True):
                        # A failed auto-update must never pass silently — the user never
                        # saw a modal, so push it to the Dashboard feed (and logs) so a
                        # broken/aborted update is visible after the fact.
                        err = result.get("error") or "unknown error"
                        logger.error(f"[ AUTO-UPDATE ] : Update FAILED: {err}")
                        await self._surface_update_failure(err, result.get("steps"))
                    elif result.get("updated") or result.get("restarting"):
                        logger.warning(
                            f"[ AUTO-UPDATE ] : Applied update {result.get('from')} → {result.get('to')}; "
                            f"restarting (reasons={result.get('restart_reasons')})."
                        )
                        return  # restart is scheduled; stop looping in this (dying) process
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[ AUTO-UPDATE ] : Check failed: {e}")
            await asyncio.sleep(interval_min * 60)

    async def _surface_update_failure(self, error: str, steps=None) -> None:
        """Make a failed (auto-)update visible to the user via the Dashboard feed.

        The one-click button shows failures inline, but an auto-update runs with no UI in
        front of it. With the atomic rebuild the previous UI is preserved, so the only sign
        of trouble would be a buried log line — this pins a Dashboard entry instead.

        Prior auto_update alerts are dismissed first so a stuck dirty worktree doesn't
        stack a new identical card on every poll interval.
        """
        try:
            from src.core.intelligence import dashboard_feed
            await dashboard_feed.dismiss_by_metadata_source("auto_update")
            body = error if not steps else f"{error}\n\nSteps:\n" + "\n".join(f"• {s}" for s in steps)
            await dashboard_feed.append_entry(
                kind="status",
                title="Auto-update failed",
                body=body[:3000],
                metadata={"source": "auto_update", "level": "error"},
                pinned=True,
                expires_in_hours=72,
            )
        except Exception as e:
            logger.error(f"[ AUTO-UPDATE ] : Could not surface failure to Dashboard: {e}")

    async def handler(self, websocket):
        """Gateway managing raw protocol state and authentication."""
        logger.info(f"[ CONNECT ] : Attempt from {getattr(websocket, 'remote_address', 'Unknown')}")
        
        try:
            async for message_str in websocket:
                try:
                    msg = ChadMessage.from_json(message_str)
                    
                    # --- AUTHENTICATION BLOCK ---
                    if msg.type == MessageType.AUTH.value:
                        remote_addr = getattr(websocket, "remote_address", None)
                        client_host = remote_addr[0] if remote_addr else None
                        is_valid, user_id = _auth.verify_token(
                            msg.auth_token or "", self.token, client_host=client_host
                        )
                        if is_valid:
                            self.authenticated_clients[websocket] = user_id
                            payload = msg.payload or {}
                            client_type = payload.get("client_type", "desktop")
                            # Eval harness: agent (default) / plan when specified — never GUI auto
                            # (auto would JIT-load intent-router gemma-1b on every suite connect).
                            preload_ui_mode = payload.get("ui_mode") or payload.get("mode")
                            self.coordinator.register_active_websocket(
                                websocket,
                                client_type=client_type,
                                preload_ui_mode=preload_ui_mode,
                            )

                            if not self._initialized:
                                logger.warning("[ AUTH ] : Token verified. Waiting for server bootstrap to complete before finalizing uplink...")
                                try:
                                    await asyncio.wait_for(self.initialization_ready.wait(), timeout=90.0)
                                except asyncio.TimeoutError:
                                    logger.error("[ AUTH ] : Bootstrap still not ready after 90s — rejecting client.")
                                    # code=not_ready, NOT invalid_token: the token was already
                                    # verified above. A client that can't tell these apart treats
                                    # "still starting up" as a rejected credential and discards a
                                    # perfectly good token (the phone/PWA did exactly that, which
                                    # is why re-pairing kept being necessary).
                                    await websocket.send(ChadMessage(MessageType.ERROR, {
                                        "error": "Brain is still starting up. Try again in a moment.",
                                        "code": "not_ready",
                                    }).to_json())
                                    await websocket.close()
                                    continue

                            if not self._initialized:
                                logger.error("[ AUTH ] : Bootstrap finished without success — rejecting client.")
                                await websocket.send(ChadMessage(MessageType.ERROR, {
                                    "error": "Brain failed to start. Check server logs, then restart.",
                                    "code": "not_ready",
                                }).to_json())
                                await websocket.close()
                                continue

                            uid_label = f" (user={user_id})" if user_id else ""
                            logger.warning(f"[ AUTH ] : Client successfully linked to Brain logic{uid_label}.")
                            registry = self.components.get("registry")
                            fast_track_commands = registry.get_fast_track_commands() if registry else []
                            planner_enabled = bool(ConfigManager.get("planner.enabled", False))
                            await websocket.send(ChadMessage(MessageType.AUTH, {
                                "status": "success",
                                "fast_track_commands": fast_track_commands,
                                "planner_enabled": planner_enabled,
                            }).to_json())
                        else:
                            logger.error("[ AUTH ] : FAILED attempt.")
                            # The ONLY path that means "this credential is bad". Clients key off
                            # code=invalid_token to decide whether to discard a stored token, so
                            # nothing else may send this code.
                            await websocket.send(ChadMessage(MessageType.ERROR, {
                                "error": "Invalid token",
                                "code": "invalid_token",
                            }).to_json())
                            await websocket.close()
                        continue
                    
                    # Ensure auth enforced
                    if websocket not in self.authenticated_clients:
                        logger.warning("[ AUTH ] : Discarding unauthorized frame.")
                        await websocket.close()
                        return

                    # --- VOICE MODE STATE (drives the desktop buddy) ---
                    if msg.type == MessageType.VOICE_STATE.value:
                        if self.buddy_bridge:
                            self.buddy_bridge.set_listening(bool(msg.payload.get("listening")))
                        continue

                    # --- DELEGATE MESSAGE ---
                    await self.coordinator.handle_incoming_message(msg, websocket)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[ WS ] Failed decoding frame: {e}")

        except Exception as e:
            logger.info(f"[ WS ] Closed or disconnected: {e}")
        finally:
            self.authenticated_clients.pop(websocket, None)
            await self.coordinator.unregister_websocket(websocket)

    async def daemon_handler(self, websocket):
        """Handler exclusively for /ws/daemon endpoint managing Hands execution primitives."""
        logger.warning(f"[ DAEMON CONNECT ] : Attempt from {getattr(websocket, 'remote_address', 'Unknown')}")
        from src.remote.daemon_bridge import DaemonExecutorBridge
        bridge = DaemonExecutorBridge()
        
        try:
            # 1. Immediate Auth Handshake (expected first frame)
            async for auth_str in websocket:
                try:
                    payload = json.loads(auth_str)
                    if payload.get("type") == "auth" and payload.get("auth_token") == self.token:
                        logger.warning("[ DAEMON AUTH ] : Hands Daemon successfully linked to Brain.")
                        await websocket.send(json.dumps({"status": "authenticated"}))
                        bridge.register_daemon(websocket)
                        break
                    else:
                        logger.error("[ DAEMON AUTH ] : Token verification rejected.")
                        await websocket.send(json.dumps({"status": "error", "message": "Invalid token"}))
                        await websocket.close()
                        return
                except Exception as e:
                    logger.error(f"Failed parsing daemon auth frame: {e}")
                    await websocket.close()
                    return
            
            # 2. Main Response Listener Loop
            async for response_str in websocket:
                try:
                    resp_dict = json.loads(response_str)
                    await bridge.handle_daemon_response(resp_dict)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[ DAEMON WS ] Dropped malformed response: {e}")
                    
        except Exception as e:
            logger.info(f"[ DAEMON WS ] Disconnected: {e}")
        finally:
            bridge.unregister_daemon(websocket)

    def _make_starlette_app(self):
        """Wrap Starlette stack around factory endpoints and gateway."""
        server = self

        async def validate_ws_connection(ws: WebSocket) -> bool:
            ip = ws.client.host if ws.client else "unknown"
            if not await server.rate_limiter.acquire(ip):
                logger.warning(f"[ SECURITY ] : Connection rate limit exceeded for IP: {ip}")
                await ws.close(code=1008)
                return False

            origin = ws.headers.get("origin")
            allowed_origins = {
                "http://localhost:5173", "http://127.0.0.1:5173",
                "http://localhost:8767", "http://127.0.0.1:8767",
                "null",
            }
            allowed_origins.update(_tailscale_origins())
            env_origins = os.getenv("CHAD_ALLOWED_ORIGINS")
            if env_origins:
                for o in env_origins.split(","):
                    allowed_origins.add(o.strip())

            if origin not in allowed_origins:
                logger.warning(f"[ SECURITY ] : Rejected WebSocket connection from unauthorized origin: {origin}")
                await ws.close(code=1008)
                return False

            return True

        # 1. Visual Display / App Client WebSocket adapter
        async def starlette_ws_route(ws: WebSocket):
            if not await validate_ws_connection(ws):
                return
            await ws.accept()
            adapter = _StarletteWSAdapter(ws)
            try:
                await server.handler(adapter)
            except Exception as e:
                logger.error(f"Socket handling error: {e}", exc_info=True)
            finally:
                server.authenticated_clients.pop(adapter, None)

        # 2. OS Automation / Hands Daemon WebSocket adapter
        async def daemon_ws_route(ws: WebSocket):
            if not await validate_ws_connection(ws):
                return
            await ws.accept()
            adapter = _StarletteWSAdapter(ws)
            try:
                await server.daemon_handler(adapter)
            except Exception as e:
                logger.error(f"Daemon socket handling error: {e}", exc_info=True)

        # Gather consolidated routes from standalone endpoints module
        routes = build_starlette_routes(starlette_ws_route, daemon_ws_route)

        @asynccontextmanager
        async def lifespan(app):
            # Kick off heavy asynchronous boot sequence immediately
            _timing_mark("lifespan_startup")
            # Bring up lmstudio-gate (cross-process LM Studio priority queue, issue #478)
            # off the boot critical path. Best-effort: if it can't start, inference just
            # goes direct to LM Studio.
            def _spawn_gate():
                try:
                    from src.infrastructure.llm.gate_client import ensure_gate_running
                    ensure_gate_running()
                except Exception as e:
                    logger.warning(f"[ GATE ] lmstudio-gate spawn failed (non-fatal): {e}")
            threading.Thread(target=_spawn_gate, name="chad-lmstudio-gate", daemon=True).start()
            asyncio.create_task(server._safe_initialize())
            yield
            # Trigger graceful system stop on termination
            await server.stop()

        from starlette.middleware.cors import CORSMiddleware
        app = Starlette(debug=True, routes=routes, lifespan=lifespan)
        app.state.server = server
        # CORS origins mirror the WS origin allowlist (validate_ws_connection): the
        # localhost dev origins, the auto-detected Tailscale IP origins, plus any in
        # CHAD_ALLOWED_ORIGINS (comma-separated), so a remote/PWA client (e.g. a phone
        # on the tailnet) is accepted on both /ws and the REST endpoints with zero
        # manual config. We authenticate with x-chad-token, not cookies, so
        # credentials are not enabled.
        cors_origins = [
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://localhost:8767", "http://127.0.0.1:8767",
            *_tailscale_origins(),
        ]
        env_origins = os.getenv("CHAD_ALLOWED_ORIGINS")
        if env_origins:
            cors_origins.extend(o.strip() for o in env_origins.split(",") if o.strip())
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        return app

    async def stop(self):
        """Graceful disassembly of running service nodes."""
        logger.info("[ SHUTDOWN ] : Initiating server power-off cycle...")
        
        # Sequentially kill running components provided by factory
        if self.components.get("heartbeat_service"):
            await self.components["heartbeat_service"].stop()
            
        if self.components.get("cron_scheduler"):
            await self.components["cron_scheduler"].stop()
            
        if self.components.get("task_repository"):
            self.components["task_repository"].close()
            
        if self.session_manager:
            await self.session_manager.cleanup()

        mcp_registry = self.components.get("mcp_registry")
        if mcp_registry:
            await mcp_registry.cleanup()

        logger.info("[ SHUTDOWN ] : Process safely decommissioned.")

    async def start(self):
        """Synchronous startup path (used for pure terminal python launch)."""
        if not HAS_STARLETTE:
            logger.error("Starlette is required for modern deployments.")
            return

        # Overlap the heavy ML import (~16s cold) with socket bind + config load (issue #182).
        _prewarm_heavy_imports()

        import uvicorn
        app = self._make_starlette_app()
        config = uvicorn.Config(
            app, 
            host=self.host, 
            port=self.port,
            ws_ping_interval=20.0,
            ws_ping_timeout=60.0,
            ssl_keyfile=os.getenv("CHAD_SSL_KEY"),
            ssl_certfile=os.getenv("CHAD_SSL_CERT")
        )
        server = uvicorn.Server(config)
        self._uvicorn = server
        _timing_mark("uvicorn_serve_call")
        await server.serve()

    def request_restart(self):
        """Ask the running uvicorn server to shut down gracefully.

        The external supervisor (scripts/prod/chad_supervisor.ps1) relaunches the
        process, so this effects a clean restart that picks up freshly pulled code.
        Returns True if a server reference was available to signal.
        """
        if self._uvicorn is not None:
            logger.warning("[ UPDATE ] : Graceful restart requested — signalling uvicorn shutdown.")
            self._uvicorn.should_exit = True
            return True
        logger.error("[ UPDATE ] : No uvicorn reference available; cannot signal restart.")
        return False


# Application Singleton setup for external uvicorn injection
server_instance = ChadServer()
app = server_instance._make_starlette_app()

if __name__ == "__main__":
    asyncio.run(server_instance.start())
