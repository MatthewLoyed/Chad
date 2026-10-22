"""
🧠 Daemon Executor Bridge
Server-side manager that handles routing OS-level primitives to connected Hands daemons.
Provides clean, asynchronous API for tools without knowing websocket details.
"""

import asyncio
import json
import logging
from typing import Dict, Optional, Any

from src.shared.logger import setup_logger
from src.daemons.protocol import DaemonRequest, DaemonResponse

logger = setup_logger("DaemonBridge")

class DaemonOfflineError(Exception):
    """Raised when a tool tries to run local OS actions but the Hands Daemon is not connected."""
    pass

class DaemonExecutorBridge:
    """
    Manages the persistent WebSocket connection to the Hands Daemon.
    Executes requests using async Futures to correlate responses with requests.
    """
    
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DaemonExecutorBridge, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Ensure init runs only once in the singleton lifespan
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        self._active_ws = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._initialized = True
        logger.info("[ DAEMON BRIDGE ] : Module instantiated and listening.")

    @property
    def is_ready(self) -> bool:
        """Check if a Daemon client is actively connected."""
        return self._active_ws is not None

    def register_daemon(self, websocket):
        """Bind the active daemon WebSocket transport."""
        self._active_ws = websocket
        logger.warning("[ DAEMON ] : Hands Daemon linked successfully. OS control active.")

    def unregister_daemon(self, websocket):
        """Unbind the socket and fail any pending executions."""
        if self._active_ws == websocket:
            self._active_ws = None
            logger.error("[ DAEMON ] : Severed connection! Refusing OS commands.")
            
            # Purge any stuck futures with a clean cancellation
            for req_id, fut in list(self._pending_requests.items()):
                if not fut.done():
                    fut.set_exception(DaemonOfflineError("Hands Daemon disconnected mid-execution."))
            self._pending_requests.clear()

    async def handle_daemon_response(self, response_dict: Dict[str, Any]):
        """Match inbound response frame from daemon with waiting future."""
        try:
            resp = DaemonResponse.from_dict(response_dict)
            req_id = resp.request_id
            
            if req_id in self._pending_requests:
                future = self._pending_requests.pop(req_id)
                if not future.done():
                    if resp.success:
                        future.set_result(resp.result)
                    else:
                        future.set_exception(RuntimeError(resp.error or "Unknown execution failure."))
            else:
                logger.warning(f"[ DAEMON ] : Orphan response ignored: req_id={req_id[:8]}")
        except Exception as e:
            logger.error(f"[ DAEMON ] Error unpacking response: {e}")

    async def execute_primitive(self, action: str, payload: Dict[str, Any], timeout: float = 5.0) -> Any:
        """
        Dispatches a command to the remote daemon and awaits the async result.
        
        Args:
            action: String name of command (e.g. 'type_text')
            payload: Inputs passed to command.
            timeout: Strict cap for network/OS response.
        """
        if not self.is_ready:
            raise DaemonOfflineError("The Windows 'Hands' Daemon is currently offline. Local system operations are unavailable.")

        req = DaemonRequest(action=action, payload=payload, timeout=timeout)
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[req.request_id] = future
        
        logger.info(f"[ DAEMON RUN ] : Sending action={action}, req_id={req.request_id[:8]}")
        
        try:
            # Serialize and dispatch across network
            await self._active_ws.send(req.to_json())
            
            # Await future with tight timeout loop
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"[ DAEMON OK ] : req_id={req.request_id[:8]} executed successfully.")
            return result
            
        except asyncio.TimeoutError:
            # Garbage collect future to prevent memory leak
            self._pending_requests.pop(req.request_id, None)
            logger.error(f"[ DAEMON TIMEOUT ] : Task {req.request_id[:8]} exceeded {timeout}s limit.")
            raise TimeoutError(f"Local execution timed out after {timeout}s.")
            
        except Exception as e:
            self._pending_requests.pop(req.request_id, None)
            logger.error(f"[ DAEMON FAIL ] : Task {req.request_id[:8]} aborted: {e}")
            raise
