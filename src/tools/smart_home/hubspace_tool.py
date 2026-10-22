"""
Hubspace Light Control Tool
Enables control of lights and other devices via the Hubspace API.
"""
import logging
import asyncio
import re
from typing import Dict, Any, List, Optional

from src.tools.base_tool import BaseTool
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("HubspaceTool")

class HubspaceTool(BaseTool):
    """
    Tool for controlling Hubspace smart home devices (mainly lights).
    """
    name = "hubspace_control"
    description = "Control Hubspace lights, lamps, and devices (turn on/off, set brightness)."
    category = "custom"
    requirements = ["hubspace.username", "hubspace.password"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
        self._api = None
        self._devices = {}
        self._initialized = False

    def is_available(self) -> bool:
        """Check if the tool is available for use."""
        try:
            import importlib.util
            return importlib.util.find_spec("hubspaceng") is not None
        except Exception:
            return False

    async def _ensure_initialized(self) -> bool:
        """Initialize connection to Hubspace if not already done."""
        if self._initialized and self._api:
            return True

        hubspace_config = ConfigManager.get("hubspace")
        if not hubspace_config:
            logger.warning("Hubspace credentials not found in config.")
            return False

        username = ConfigManager.get_secret("hubspace.username")
        password = ConfigManager.get_secret("hubspace.password")

        if not username or not password:
            logger.warning("Hubspace username or password missing.")
            return False

        try:
            from hubspaceng import login
        except ImportError:
            logger.error("Hubspace library 'hubspace-ng' is not installed.")
            return False

        try:
            # Login and get initial state
            logger.info("[ HUBSPACE ] Authenticating with Hubspace...")
            self._api = await login(username, password)
            self._devices = self._api.devices
            self._initialized = True
            logger.info(f"Hubspace initialized. Found {len(self._devices)} devices.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Hubspace: {e}")
            return False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform. Allowed values: 'turn_on', 'turn_off', 'set_brightness', 'list_devices'"
                    },
                    "device_name": {
                        "type": "string",
                        "description": "Fuzzy name of the device (e.g., 'bedroom light', 'lamp'). Optional for list_devices."
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brightness percentage (0-100). Required for 'set_brightness'."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(self, action: str, device_name: Optional[str] = None, brightness: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        if not await self._ensure_initialized():
            return {"success": False, "error": "Failed to connect to Hubspace. Check credentials."}

        if action == "list_devices":
            device_list = [f"{d.name} ({d.device_class})" for d in self._devices.values()]
            return {"success": True, "result": f"Available devices: {', '.join(device_list)}"}

        if not device_name:
            return {"success": False, "error": "Device name is required for control actions."}

        target_device = None  # Initialize match variable
        
        # Normalize Input
        target_name_lower = device_name.lower().strip()
        
        # SINGLETON RESOLUTION: one light in the home + user said light/lamp/lights
        # (including "living room lights", "the bedroom lamp", etc.). With a single
        # device the room label is noise — just control it.
        lights = [d for d in self._devices.values() if d.device_class == 'light']
        if len(lights) == 1 and re.search(r"\b(lights?|lamps?)\b", target_name_lower):
            logger.info(f"Singleton resolution: '{device_name}' -> '{lights[0].name}'")
            target_device = lights[0]
        elif target_name_lower in ['light', 'lamp', 'the light', 'lights']:
             if len(lights) == 1:
                 logger.info(f"Singleton resolution: '{device_name}' -> '{lights[0].name}'")
                 target_device = lights[0]
             elif len(lights) > 1:
                 return {"success": False, "error": f"Multiple lights found. Please specify which one: {', '.join([d.name for d in lights])}"}
             else:
                 return {"success": False, "error": "No lights found to control."}
        
        # Find device by fuzzy name matching
        if not target_device:
            # 1. Exact Name Match
            for dev in self._devices.values():
                if dev.name and dev.name.lower() == target_name_lower:
                    target_device = dev
                    break
            
            # 2. Smart Fuzzy Match (Ignore Hubspace suffixes like '(light)')
            # Hubspace names are often "Bedroom (light)". User says "Bedroom".
            if not target_device:
                for dev in self._devices.values():
                    if not dev.name: continue
                    
                    # Clean the device name: "Bedroom (light)" -> "bedroom"
                    clean_dev_name = dev.name.lower().split('(')[0].strip()
                    
                    # Check if user input matches the clean name exact
                    if clean_dev_name == target_name_lower:
                        target_device = dev
                        break
                    
                    # Check if user input is contained in clean name
                    if target_name_lower in clean_dev_name:
                         target_device = dev
                         break
                    
                    # Check if clean name is contained in user input (e.g. "Bedroom" in "Bedroom light")
                    if clean_dev_name in target_name_lower:
                        target_device = dev
                        break

        if not target_device:
            return {"success": False, "error": f"Device '{device_name}' not found."}

        try:
            # Execute Action
            if action == "turn_on":
                # Find power function
                power_func = None
                for func in target_device.functions:
                    if getattr(func, 'func_class', None) == 'power':
                        power_func = func
                        break
                
                if power_func:
                     await target_device.set_state(power_func, 'on')
                     return {"success": True, "result": f"Turned on {target_device.name}."}
                else:
                    return {"success": False, "error": f"Power control not found for {target_device.name}"}

            elif action == "turn_off":
                power_func = None
                for func in target_device.functions:
                    if getattr(func, 'func_class', None) == 'power':
                        power_func = func
                        break
                
                if power_func:
                     await target_device.set_state(power_func, 'off')
                     return {"success": True, "result": f"Turned off {target_device.name}."}
                else:
                    return {"success": False, "error": f"Power control not found for {target_device.name}"}

            elif action == "set_brightness":
                if brightness is None:
                    return {"success": False, "error": "Brightness level (0-100) is required."}
                
                # Find brightness function
                bright_func = None
                for func in target_device.functions:
                    if getattr(func, 'func_class', None) == 'brightness':
                        bright_func = func
                        break
                
                if bright_func:
                    # Hubspace often expects 0-100 for brightness
                    await target_device.set_state(bright_func, brightness)
                    return {"success": True, "result": f"Set {target_device.name} brightness to {brightness}%."}
                else:
                     return {"success": False, "error": f"Brightness control not found for {target_device.name}"}

        except Exception as e:
            logger.error(f"Error controlling Hubspace device: {e}", exc_info=True)
            return {"success": False, "error": f"Failed to execute {action} on {device_name}: {str(e)}"}

        return {"success": False, "error": f"Unknown action: {action}"}
