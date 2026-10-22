"""
📜 Scroll Tool - Universal Window Scrolling
Delegates OS-level keyboard/mouse injection events to the Hands Daemon.
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.remote.daemon_bridge import DaemonExecutorBridge

logger = setup_logger("ScrollTool")

class ScrollTool(BaseTool):
    """
    Simulates mouse wheel or keyboard scrolling.
    Delegates input automation requests to the Hands Daemon over RPC.
    """
    
    name = "scroll"
    description = "Scrolls the active window or a target window. Use 'up' or 'down' with a specific amount of steps."
    category = "os"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Direction to scroll."
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Number of scroll steps (e.g., 3, 5, 10)."
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Optional: Target window to focus before scrolling."
                    }
                },
                "required": ["direction"]
            }
        }
    
    async def execute(self, direction: str, amount: int = 3, window_title: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Execute the scroll action by proxying through DaemonExecutorBridge."""
        logger.info(f"[ SCROLL ] : Delegating {direction} x{amount} to Hands Daemon.")
        
        try:
            bridge = DaemonExecutorBridge()
            
            # First perform window activation if requested
            if window_title:
                logger.info(f"[ SCROLL ] : Requesting pre-scroll focus on window: {window_title}")
                await bridge.execute_primitive(
                    action="switch_window",
                    payload={"window_title": window_title},
                    timeout=3.0
                )
            
            # Dispatch core scroll event
            res = await bridge.execute_primitive(
                action="scroll",
                payload={
                    "direction": direction,
                    "amount": int(amount)
                },
                timeout=4.0
            )
            
            return {
                "success": True, 
                "result": res,
                "caption": f"Scrolling {direction.capitalize()}"
            }
            
        except Exception as e:
            logger.error(f"[ SCROLL ERROR ] : Remote automation failed: {e}")
            return {
                "success": False, 
                "error": f"Remote scrolling execution error: {str(e)}"
            }
