"""
Wait Tool - Generic pause for agentic workflows.
"""

import asyncio
from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("WaitTool")

class WaitTool(BaseTool):
    """
    Generic tool to make the agent wait for a specified duration.
    Useful after launching apps, starting long-running processes,
    or waiting for UI elements to stabilize.
    """
    
    name = "wait"
    description = "Pauses execution for a specified number of seconds. Use this after launching an application or starting a process that takes time to initialize."
    category = "system"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Number of seconds to wait.",
                        "default": 10
                    },
                    "reason": {
                        "type": "string",
                        "description": "The reason for waiting (e.g., 'waiting for Rider to load')."
                    }
                },
                "required": ["seconds"]
            }
        }
    
    async def execute(self, seconds: int = 10, reason: str = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"Waiting for {seconds}s. Reason: {reason or 'none'}")
        await asyncio.sleep(seconds)
        return {
            "success": True, 
            "result": f"Waited for {seconds} seconds.",
            "reason": reason
        }
