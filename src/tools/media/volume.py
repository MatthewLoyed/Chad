import sys
import logging
import asyncio
from typing import Dict, Any
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("VolumeTool")

# Windows-specific imports
if sys.platform == 'win32':
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        import pythoncom
    except ImportError:
        logger.warning("pycaw or comtypes not found. Volume control will be limited.")

class VolumeTool(BaseTool):
    """Tool for controlling system master volume via the active endpoint."""
    
    name = "control_volume"
    description = (
        "Control SYSTEM master volume: increase/decrease by a step, mute, unmute, or set an "
        "exact level. Use for 'turn the volume up/down', 'mute', 'unmute'. For screen "
        "brightness, use system_settings instead — this tool does NOT touch brightness."
    )
    category = "system"
    
    def __init__(self, config=None):
        super().__init__(config)
        self.audio_interface = None

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "increase", "decrease", "mute", "unmute", "get"],
                        "description": "Action to perform."
                    },
                    "level": {
                        "type": "number",
                        "description": "Specific volume level (0-100) or change magnitude."
                    }
                },
                "required": ["action"]
            }
        }

    def _get_active_interface(self):
        """Robustly gets the current default audio endpoint interface."""
        if sys.platform != 'win32': return None
        
        try:
            pythoncom.CoInitialize()
            # Always get the CURRENT default speaker (handles mid-session swaps)
            devices = AudioUtilities.GetSpeakers()
            if not devices: return None
            
            # Check if pycaw already wrapped it and exposed EndpointVolume
            if hasattr(devices, 'EndpointVolume'):
                return devices.EndpointVolume
                
            # Fallback to manual activation
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return interface.QueryInterface(IAudioEndpointVolume)
        except Exception as e:
            logger.error(f"Failed to get audio interface: {e}")
            return None

    async def execute(self, action: str, level: float = None, **kwargs) -> Dict[str, Any]:
        interface = self._get_active_interface()
        if not interface:
            return {"success": False, "error": "System audio interface unavailable."}

        try:
            if action == "get":
                current = interface.GetMasterVolumeLevelScalar() * 100
                muted = interface.GetMute()
                
                return {"success": True, "result": f"Volume is {current:.0f}%", "volume": current, "muted": muted}
            
            elif action == "set":
                if level is None: return {"success": False, "error": "Level (0-100) required."}
                interface.SetMasterVolumeLevelScalar(level / 100.0, None)
                return {"success": True, "result": f"Volume set to {level:.0f}%", "caption": f"Volume: {level:.0f}%"}
            
            elif action == "increase":
                change = level if level else 10
                current = interface.GetMasterVolumeLevelScalar() * 100
                new_level = min(100, current + change)
                interface.SetMasterVolumeLevelScalar(new_level / 100.0, None)
                return {"success": True, "result": f"Volume up to {new_level:.0f}%", "caption": f"Volume: {new_level:.0f}%"}
            
            elif action == "decrease":
                change = level if level else 10
                current = interface.GetMasterVolumeLevelScalar() * 100
                new_level = max(0, current - change)
                interface.SetMasterVolumeLevelScalar(new_level / 100.0, None)
                return {"success": True, "result": f"Volume down to {new_level:.0f}%", "caption": f"Volume: {new_level:.0f}%"}
            
            elif action == "mute":
                interface.SetMute(1, None)
                return {"success": True, "result": "Muted", "caption": "Muted"}
            
            elif action == "unmute":
                interface.SetMute(0, None)
                return {"success": True, "result": "Unmuted", "caption": "Unmuted"}
                
            return {"success": False, "error": f"Unknown action: {action}"}
            
        except Exception as e:
            logger.error(f"Volume execution failed: {e}")
            return {"success": False, "error": str(e)}

    def validate_params(self, action: str = None, **kwargs) -> tuple[bool, str | None]:
        if not action: return False, "Action required"
        return True, None
