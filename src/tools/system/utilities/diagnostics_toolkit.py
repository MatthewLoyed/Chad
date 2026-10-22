"""
Diagnostics Toolkit - System Integrity and Overlay Validation
"""

import logging
from typing import Dict, Any, Optional

# Add project root directory to path (go up from tests/unit/ to project root)

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("TestToolkit")

class DiagnosticsTool(BaseTool):
    """
    Diagnostic tools for verifying system integrity and visual outputs.
    """
    
    name = "test_toolkit"
    description = "Diagnostic tools for system verification, including holographic HUD tests."
    category = "system"
    
    def __init__(self, session_manager=None, config=None):
        super().__init__(config)
        self.session_manager = session_manager

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["trigger_test_notification"],
                        "description": "The diagnostic action to perform."
                    },
                    "title": {
                        "type": "string",
                        "description": "Custom title for the notification."
                    },
                    "message": {
                        "type": "string",
                        "description": "Custom message for the notification."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        if action == "trigger_test_notification":
            return await self.trigger_test_notification(
                kwargs.get("title", "DIAGNOSTIC UPLINK"),
                kwargs.get("message", "Hello. Holographic systems are operational.")
            )
        return {"success": False, "error": f"Unknown action: {action}"}

    async def trigger_test_notification(self, title: str, message: str) -> Dict[str, Any]:
        """Triggers a holographic HUD notification on the desktop for testing purposes."""
        logger.warning("[ TEST ] : Initiating manual overlay override...")
        
        if not self.session_manager:
            return {"success": False, "error": "SessionManager not linked to TestToolkit"}
            
        try:
            # Trigger the notification via SessionManager
            self.session_manager.show_notification(
                title=title,
                message=message,
                icon="🛠️",
                duration=7
            )
            
            logger.warning("[ TEST ] : Visual confirmation required on Monitor 1.")
            return {
                "success": True, 
                "result": f"Notification '{title}' deployed to desktop. Please confirm visibility."
            }
        except Exception as e:
            logger.error(f"Test notification failed: {e}")
            return {"success": False, "error": str(e)}





