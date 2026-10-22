"""
Screen Capture Tool - Capture screenshots for vision analysis
Supports full screen capture or targeting specific windows by keyword.
"""

import io
import time
from typing import Dict, Any, Optional, List, Tuple

import base64
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.remote.daemon_bridge import DaemonExecutorBridge

logger = setup_logger("ScreenCapture")

class ScreenCapture(BaseTool):
    """
    Tool for capturing screenshots with smart window targeting.
    Delegates capture execution to the local OS Hands Daemon over RPC.
    """
    
    name = "capture_screen"
    description = (
        "Capture a screenshot. Use for 'look at this', 'what's on my screen', "
        "'check my YouTube window', 'see this picture', or when you need visual context "
        "for ANY task (e.g., 'look at my screen and help me debug', 'check the Chrome window'). "
        "If target_window is specified, captures only that window. Otherwise captures full screen."
    )
    category = "system"
    
    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_window": {
                        "type": "string",
                        "description": (
                            "Optional keyword to find a specific window (e.g., 'YouTube', 'Chrome', "
                            "'Notepad', 'Discord'). If not provided, captures full screen."
                        )
                    }
                },
                "required": []
            }
        }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        Capture a screenshot of the screen or a specific window.
        Delegates process execution to the Windows Hands Daemon.
        """
        target_window = kwargs.get('target_window', None)
        
        try:
            bridge = DaemonExecutorBridge()
            logger.info(f"[ SCREEN CAPTURE ] : Delegating task to Hands Daemon (target_window={target_window}).")
            
            # Dispatch to the active daemon connection
            res = await bridge.execute_primitive(
                action="capture_screen",
                payload={"target_window": target_window},
                timeout=8.0 # Screens can take a moment to scale and serialize
            )
            
            # Unpack the base64 image response payload
            img_bytes = base64.b64decode(res["image_b64"])
            
            logger.info(f"[ SCREEN OK ] : Received {len(img_bytes)} bytes from {res.get('source')}.")
            
            return {
                "success": True,
                "result": f"Screenshot captured from {res.get('source')}",
                "image_bytes": img_bytes,
                "width": res["width"],
                "height": res["height"],
                "format": "jpeg",
                "source": res.get("source")
            }
            
        except Exception as e:
            error_msg = f"Failed to capture remote screenshot: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "error_type": "capture_failed"
            }
    
    def validate_params(self, **kwargs) -> tuple[bool, str | None]:
        """Validate parameters."""
        return True, None

