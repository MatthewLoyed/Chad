"""
Routine Runner Tool - Macro Execution
"""

import asyncio
from typing import Dict, Any, List, Optional

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("RoutineRunner")

class RoutineRunnerTool(BaseTool):
    """
    Execute predefined multi-step routines (macros) defined in the config.
    """
    
    name = "run_routine"
    description = "Execute a predefined multi-step routine (macro) from the configuration (e.g., 'start_work', 'shutdown')."
    category = "os"
    
    def __init__(self, config=None):
        super().__init__(config)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "routine_name": {
                        "type": "string",
                        "description": "The name of the routine to execute."
                    }
                },
                "required": ["routine_name"]
            }
        }

    async def execute(self, routine_name: str, **kwargs) -> Dict[str, Any]:
        if not routine_name:
            return {"success": False, "error": "routine_name is required."}
            
        key = routine_name.lower().strip()
        routines = self.config.get("routines", {})
        steps = routines.get(key)
        
        if not steps:
            return {"success": False, "error": f"Routine '{routine_name}' not found in config."}
            
        logger.info(f"Executing routine '{routine_name}' with {len(steps)} steps...")
        
        # We'll use the other tools internally or re-implement the logic
        # For simplicity and to avoid circular imports, we'll implement a basic version
        # that handles common actions like launch_app.
        
        from src.tools.os.app_launcher import AppLauncherTool
        from src.tools.os.system_settings import SystemSettingsTool
        
        launcher = AppLauncherTool(config=self.config)
        settings = SystemSettingsTool(config=self.config)
        
        tasks = []
        for step in steps:
            action = step.get("action")
            if action == "launch_app":
                tasks.append(launcher.execute(
                    app_name_or_path=step.get("app"), 
                    monitor_index=step.get("monitor"),
                    url=step.get("url")
                ))
            elif action == "set_volume":
                tasks.append(settings.execute(action="set_volume", level=step.get("level")))
        
        if tasks:
            await asyncio.gather(*tasks)
            
        return {"success": True, "result": f"Routine '{routine_name}' completed."}
