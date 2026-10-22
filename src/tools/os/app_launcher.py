"""
🚀 App Tool - Application Discovery and Window Management
Delegates application execution and window scaling to the Hands Daemon.
"""

import os
from typing import Dict, Any, List, Optional

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.remote.daemon_bridge import DaemonExecutorBridge

logger = setup_logger("AppTool")

class ApplicationConfigLookup:
    """Looks up application definitions specifically within config overrides."""
    
    @staticmethod
    def find_configured_app(query: str) -> Optional[str]:
        """
        Checks if a target app has a custom absolute path defined in config.
        Runs server-side to leverage shared Profile Config.
        """
        query = query.lower().strip()
        apps_cfg = ConfigManager.get("apps", {})
        
        if not apps_cfg:
            return None
            
        # Direct key lookup
        if query in apps_cfg:
            raw_path = apps_cfg[query].get("path")
            if raw_path:
                return ConfigManager.expand_path(raw_path)
                
        # Substring key lookup
        for key, data in apps_cfg.items():
            if query in key:
                raw_path = data.get("path")
                if raw_path:
                    return ConfigManager.expand_path(raw_path)
        return None

@tool_display({
    "launch": "Launching {name}...",
    "switch": "Switching to {name}...",
    "resize": "Resizing {name} window...",
    "move": "Moving {name} window..."
})
class AppTool(BaseTool):
    """
    Consolidated tool for application launching and window management.
    Routes file system crawling and binary spawning to the local Hands Daemon.
    """
    
    name = "open_app"
    description = (
        "PRIMARY tool for finding and launching desktop applications (Discord, Chrome, Spotify, Slack, Notepad, Calculator, etc.) by name. "
        "It searches the Start Menu, system PATH, and custom profiles. Use this to START an app."
    )
    category = "os"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["launch", "resize", "switch", "move"],
                        "description": "Mode of operation.",
                        "default": "launch"
                    },
                    "name": {
                        "type": "string",
                        "description": "The name of the application or window title."
                    },
                    "x": {
                        "type": "integer",
                        "description": "Move mode: explicit absolute screen X coordinate. For monitor-targeted moves (e.g. 'move to my second monitor'), use window_management(action='move_to_monitor') instead — it can discover real monitor positions first via list_monitors."
                    },
                    "y": {
                        "type": "integer",
                        "description": "Move mode: explicit absolute screen Y coordinate."
                    },
                    "width": {
                        "type": "integer",
                        "description": "Target width for resize mode."
                    },
                    "height": {
                        "type": "integer",
                        "description": "Target height for resize mode."
                    },
                    "url": {
                        "type": "string",
                        "description": "Optional: URL to open with the application."
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: Command line arguments."
                    }
                },
                "required": ["name"]
            }
        }

    async def execute(self, name: str, mode: str = "launch",
                      width: Optional[int] = None, height: Optional[int] = None,
                      url: Optional[str] = None, args: List[str] = None,
                      x: Optional[int] = None, y: Optional[int] = None,
                      **kwargs) -> Dict[str, Any]:
        
        # Fallbacks for orchestrator compatibility
        effective_mode = kwargs.get("action") or mode or "launch"
        
        try:
            bridge = DaemonExecutorBridge()
            
            if effective_mode == "launch":
                logger.info(f"[ APP ] : Resolving launch profile for '{name}'.")
                
                # 1. Resolve path server-side using local configs
                explicit_path = ApplicationConfigLookup.find_configured_app(name)
                if explicit_path:
                    logger.info(f"[ APP ] Found custom profile path for '{name}': {explicit_path}")
                
                # 2. Delegate the system lookup and spawn to the hands daemon
                res = await bridge.execute_primitive(
                    action="launch_app",
                    payload={
                        "name": name,
                        "explicit_path": explicit_path,
                        "url": url,
                        "args": args or []
                    },
                    timeout=10.0 # Binary startup can take a few seconds
                )
                
                return {
                    "success": True, 
                    "message": f"Launched '{name}'. {res}",
                    "result": res,
                    "ephemeral": True
                }
                
            elif effective_mode == "resize":
                logger.info(f"[ APP ] : Delegating window resizing of '{name}' to {width}x{height}.")
                res = await bridge.execute_primitive(
                    action="resize_window",
                    payload={
                        "window_title": name,
                        "width": int(width) if width else None,
                        "height": int(height) if height else None
                    },
                    timeout=5.0
                )
                return {
                    "success": True,
                    "message": res,
                    "result": res,
                    "ephemeral": True
                }
                
            elif effective_mode == "move":
                logger.info(f"[ APP ] : Delegating window move of '{name}' (x={x}, y={y}).")
                res = await bridge.execute_primitive(
                    action="move_window",
                    payload={
                        "window_title": name,
                        "x": x,
                        "y": y,
                    },
                    timeout=5.0
                )
                return {
                    "success": True,
                    "message": res,
                    "result": res,
                    "ephemeral": True
                }

            elif effective_mode == "switch":
                logger.info(f"[ APP ] : Delegating focus shift to '{name}'.")
                res = await bridge.execute_primitive(
                    action="switch_window",
                    payload={
                        "window_title": name
                    },
                    timeout=5.0
                )
                return {
                    "success": True,
                    "message": res,
                    "result": res,
                    "ephemeral": True
                }
                
            else:
                return {"success": False, "error": f"Unsupported mode: {effective_mode}"}
                
        except Exception as e:
            logger.error(f"[ APP FAIL ] Execution of {effective_mode} crashed. Error={e}")
            return {
                "success": False,
                "error": f"Hands Daemon failed task: {str(e)}"
            }
