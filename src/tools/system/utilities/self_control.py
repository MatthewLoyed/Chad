"""
Self Control Tool - Manage the AI Assistant's own process
"""

import sys
import os
import asyncio
import time
from typing import Dict, Any, Optional

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("SelfControl")

class SelfControl(BaseTool):
    """Tool for managing the AI Assistant's own lifecycle (shutdown, restart)."""
    
    name = "self_control"
    description = "Manage the AI Assistant's process (shutdown, restart)"
    category = "system"
    
    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["shutdown", "restart"],
                        "description": "Action to perform: 'shutdown' to close the assistant, 'restart' to restart it."
                    }
                },
                "required": ["action"]
            }
        }
    
    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        """
        Execute the self-control action.
        
        Args:
            action: 'shutdown' or 'restart'
            
        Returns:
            Result dictionary
        """
        try:
            if action == "shutdown":
                # Schedule shutdown to allow response to complete
                asyncio.create_task(self._delayed_shutdown())
                
                return {
                    "success": True,
                    "result": "Initiating system shutdown sequence...",
                    "caption": "SYSTEM SHUTDOWN INITIATED"
                }
                
            elif action == "restart":
                asyncio.create_task(self._delayed_restart())
                
                return {
                    "success": True,
                    "result": "Initiating system restart sequence...",
                    "caption": "SYSTEM RESTART INITIATED"
                }
                
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}"
                }
                
        except Exception as e:
            logger.error(f"Self control error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _delayed_shutdown(self):
        """Wait briefly then exit."""
        logger.warning("Shutdown requested via voice command.")
        # Wait for TTS or response to potentially start/finish
        await asyncio.sleep(3) 
        
        logger.warning("Exiting process now.")
        # Force kill the process
        os._exit(0)

    async def _delayed_restart(self):
        """Wait briefly then restart."""
        logger.warning("Restart requested via voice command.")
        await asyncio.sleep(3)
        
        logger.warning("Restarting process now.")
        # Restart execution
        # Re-execute the current script with the same arguments
        os.execl(sys.executable, sys.executable, *sys.argv)

    def validate_params(self, action: str = None, **kwargs) -> tuple[bool, Optional[str]]:
        """Validate input parameters."""
        if not action or action not in ["shutdown", "restart"]:
            return False, "Invalid action. Must be 'shutdown' or 'restart'."
        return True, None
