"""
Path Resolver Tool - Resolves user-friendly path aliases.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.tools.os.workspace_tools import ProjectDiscovery
from src.shared.logger import setup_logger

logger = setup_logger("PathResolver")

class PathResolverTool(BaseTool):
    """
    Agent Tool: Resolves user-friendly paths to absolute system paths.
    """
    
    name = "resolve_path"
    description = (
        "Resolve a user-friendly path alias (e.g., 'Downloads', 'Desktop', '~') to an absolute system path."
    )
    category = "os"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path_alias": {
                        "type": "string",
                        "description": "The path to resolve (e.g., 'Downloads', '~/Documents', 'current folder')."
                    }
                },
                "required": ["path_alias"]
            }
        }
    
    async def execute(self, path_alias: str, **kwargs) -> Dict[str, Any]:
        try:
            logger.info(f"Resolving path alias: '{path_alias}'")
            user_home = Path.home()
            resolved_path = None
            alias_lower = path_alias.lower().strip()
            
            common_folders = {
                "downloads": user_home / "Downloads",
                "documents": user_home / "Documents",
                "desktop": user_home / "Desktop",
                "music": user_home / "Music",
                "pictures": user_home / "Pictures",
                "videos": user_home / "Videos",
                "home": user_home,
                "~": user_home,
                "user root": user_home
            }
            
            if alias_lower in common_folders:
                resolved_path = common_folders[alias_lower]
            elif path_alias.startswith("~"):
                resolved_path = user_home / path_alias[2:]
            elif alias_lower.startswith("my "):
                clean_name = alias_lower.replace("my ", "")
                if clean_name in common_folders:
                    resolved_path = common_folders[clean_name]
            elif alias_lower in [".", "current", "here", "this folder"]:
                resolved_path = Path.cwd()
            elif os.path.isabs(path_alias):
                resolved_path = Path(path_alias)
            else:
                project_path = ProjectDiscovery.find_project(path_alias)
                if project_path:
                    resolved_path = project_path
                else:
                    resolved_path = user_home / path_alias
            
            final_path = resolved_path.resolve()
            if not final_path.is_absolute():
                 return {"success": False, "error": f"Resolution failed: Path is not absolute: {final_path}"}
            
            exists = final_path.exists()
            is_dir = final_path.is_dir() if exists else False
            
            return {
                "success": True,
                "original_alias": path_alias,
                "resolved_path": str(final_path),
                "exists": exists,
                "is_directory": is_dir
            }
        except Exception as e:
            logger.error(f"Path resolution error: {e}", exc_info=True)
            return {"success": False, "error": f"Failed to resolve path: {str(e)}"}
