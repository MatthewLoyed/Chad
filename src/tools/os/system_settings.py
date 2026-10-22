"""
🖥️ System Settings Tool - Hardware Control (Brightness/Volume)
Delegates direct OS hardware manipulation to the Hands Daemon.
"""

from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.remote.daemon_bridge import DaemonExecutorBridge

logger = setup_logger("SystemSettings")

class SystemSettingsTool(BaseTool):
    """
    Control local PC hardware settings like monitor screen brightness and system audio volume.
    Delegates execution to the persistent Hands Daemon to isolate direct COM-object dependencies.
    """
    
    name = "system_settings"
    description = (
        "Set monitor screen brightness to an exact level (0-100); also sets an exact volume "
        "level (prefer control_volume for increase/decrease/mute/unmute). Does NOT control "
        "room devices (smart lamps/lights) — use hubspace_control for those."
    )
    category = "os"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set_brightness", "set_volume"],
                        "description": "The setting to adjust."
                    },
                    "level": {
                        "type": "integer",
                        "description": "Level (0-100)."
                    }
                },
                "required": ["action", "level"]
            }
        }

    async def execute(self, action: str, level: int, **kwargs) -> Dict[str, Any]:
        level = max(0, min(100, int(level)))
        
        try:
            bridge = DaemonExecutorBridge()
            logger.info(f"[ HARDWARE ] : Routing {action} (val={level}) to Hands Daemon.")
            
            res = await bridge.execute_primitive(
                action=action,
                payload={"level": level},
                timeout=4.0
            )
            
            return {
                "success": True,
                "result": res
            }
            
        except Exception as e:
            logger.error(f"[ HARDWARE FAIL ] : Remote adjustment crashed. Error={e}")
            return {
                "success": False,
                "error": f"Failed executing {action} over daemon: {str(e)}"
            }
