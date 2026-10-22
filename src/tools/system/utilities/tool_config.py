from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.infrastructure.tools.config_manager import ToolConfigManager

class ManageToolConfigs(BaseTool):
    """
    Tool to manage 'preferred configurations' or 'context' for other tools.
    Helps the model remember specific details (like which light to use for Hubspace).
    """
    
    name = "manage_tool_configs"
    description = (
        "Save or retrieve 'Preferred Settings' or 'Notes' for specific tools. "
        "Use this to remember which device names, file paths, or parameters to use for other tools. "
        "Actions: 'save' (requires tool_name and context), 'list' (shows all saved configs)."
    )
    category = "system"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save", "list", "delete"],
                        "description": "Action to perform."
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "The exact name of the tool to configure (e.g., 'hubspace_control')."
                    },
                    "context": {
                        "type": "string",
                        "description": "The note or preferred setting to remember (e.g., 'Default light is Main Light')."
                    }
                },
                "required": ["action"]
            }
        }

    async def execute(self, action: str, tool_name: Optional[str] = None, context: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        if action == "save":
            if not tool_name or not context:
                return {"success": False, "error": "tool_name and context are required for 'save'"}
            ToolConfigManager.save_config_for_tool(tool_name, context)
            return {"success": True, "result": f"Saved config for {tool_name}: {context}"}
            
        elif action == "list":
            configs = ToolConfigManager.list_configs()
            if not configs:
                return {"success": True, "result": "No tool configs saved yet."}
            
            formatted = "\n".join([f"- {name}: {note}" for name, note in configs.items()])
            return {"success": True, "result": f"Saved Tool Configs:\n{formatted}"}
            
        elif action == "delete":
            if not tool_name:
                return {"success": False, "error": "tool_name is required for 'delete'"}
            
            configs = ToolConfigManager.list_configs()
            if tool_name in configs:
                del configs[tool_name]
                # Manually save after deletion (ToolConfigManager should handle this better but for now...)
                import json
                import os
                path = ToolConfigManager._config_path
                with open(path, "w") as f:
                    json.dump(configs, f, indent=4)
                return {"success": True, "result": f"Deleted config for {tool_name}"}
            else:
                return {"success": False, "error": f"No config found for {tool_name}"}

        return {"success": False, "error": f"Unknown action: {action}"}
