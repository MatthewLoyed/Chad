import os
import asyncio
import queue
from typing import Any
from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories import ConfigRepository
from src.shared.logger import setup_logger
from src.application.commands import Command

logger = setup_logger("WebViewAPI")

# Queue for dispatching main-thread tasks (like file dialogs)
_dialog_queue = queue.Queue()


class RemoteCommandHandler:
    """Stateless Command Handler that serializes and forwards application commands to the Brain over HTTP."""
    def __init__(self, server_url: str, token: str):
        # Sanitize and construct REST URL
        self.base_url = server_url.replace("ws://", "http://").replace("wss://", "https://")
        
        # Hygiene: trim any trailing ws slash
        if "/ws" in self.base_url:
            self.base_url = self.base_url.rstrip("/").replace("/ws", "")
        else:
            self.base_url = self.base_url.rstrip("/")
            
        self.target_url = f"{self.base_url}/api/command"
        self.token = token

    async def execute(self, command, timeout: float = 12.0) -> Any:
        import dataclasses
        import httpx
        try:
            # 1. Serialize Command dataclass to primitives
            args = dataclasses.asdict(command) if dataclasses.is_dataclass(command) else {}
            payload = {
                "command_class": command.__class__.__name__,
                "command_args": args
            }
            
            # 2. Route via REST POST using unified secure gateway
            headers = {"x-chad-token": self.token} if self.token else {}
            http_timeout = max(5.0, float(timeout)) if timeout else 12.0
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.target_url, json=payload, headers=headers, timeout=http_timeout)
                
                if resp.status_code != 200:
                    err_msg = f"HTTP {resp.status_code} from server: {resp.text}"
                    logger.error(f"[ COMMAND RELAY ] : {err_msg}")
                    raise RuntimeError(err_msg)
                    
                res_data = resp.json()
                return res_data.get("result")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RequestError) as conn_err:
            logger.warning(f"[ COMMAND RELAY ] : Server offline or unreachable for command '{command.__class__.__name__}'")
            raise conn_err
        except Exception as err:
            logger.error(f"[ COMMAND RELAY ] : Failed relaying command '{command.__class__.__name__}' -> {err}")
            raise err


class WebViewAPIBase:
    """Base class for WebViewAPI providing shared command dispatch logic and state."""
    def __init__(self, main_window_ref):
        self.main_window_ref = main_window_ref
        self._command_handler = None
        self._config_repo = ConfigRepository()

    def _get_command_handler(self):
        """Get the headless Remote Command Handler configured for Brain Server relay."""
        if self._command_handler is None:
            # Explicit resolve
            url = os.getenv("CHAD_SERVER_URL") or ConfigManager.get("chad.server_url", "ws://localhost:8767")
            token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
            self._command_handler = RemoteCommandHandler(url, token)
            logger.info(f"[ BRIDGE ] : Headless HTTP Command Relay initialized referencing Brain at {url}")
        return self._command_handler

    def _execute_command(
        self,
        command: Command,
        timeout: float = 2.0,
        default_fallback: Any = None,
        raise_on_error: bool = False
    ) -> Any:
        """
        Thread-safe command execution relay helper.
        """
        handler = self._get_command_handler()
        if not handler:
            if raise_on_error:
                raise RuntimeError("Command handler not available")
            return default_fallback

        window = self.main_window_ref()
        if not window or not window.app_reference:
            if raise_on_error:
                raise RuntimeError("Main window or app reference lost")
            return default_fallback

        loop = window.app_reference.loop
        future = asyncio.run_coroutine_threadsafe(handler.execute(command, timeout=timeout), loop)
        try:
            return future.result(timeout=timeout + 3.0)
        except Exception as e:
            import httpx
            cmd_name = command.__class__.__name__
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RequestError)) or "connection" in str(e).lower():
                logger.warning(f"{cmd_name} failed (Server offline or unreachable): {e}")
            else:
                logger.error(f"{cmd_name} failed ({type(e).__name__}): {e}", exc_info=True)
            
            if raise_on_error:
                raise
            return default_fallback
