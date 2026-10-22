"""
Workspace Tools - Efficient Project and File Discovery
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.tools.base_tool import BaseTool
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("WorkspaceTools")

class ProjectDiscovery:
    """Helper class for fast project path discovery."""
    @staticmethod
    def find_project(project_name: str) -> Optional[Path]:
        project_name = project_name.lower().strip()
        coding_roots = ConfigManager.get("workspace", {}).get("coding_roots", [])
        for root_str in coding_roots:
            root = Path(ConfigManager.expand_path(root_str))
            if not root.exists() or not root.is_dir(): continue
            try:
                for entry in root.iterdir():
                    if not entry.is_dir(): continue
                    if entry.name.lower() == project_name: return entry
                    try:
                        for sub_entry in entry.iterdir():
                            if sub_entry.is_dir() and sub_entry.name.lower() == project_name: return sub_entry
                    except PermissionError: continue
            except PermissionError: continue
        return None

    @staticmethod
    def is_project_dir(dir_path: Path) -> bool:
        if not dir_path.is_dir(): return False
        # ".chad-project" marks folders created via the Projects nav (#164) that
        # don't have a real toolchain marker yet (e.g. brand-new, empty projects).
        markers = [".git", "package.json", ".sln", "project.json", "go.mod", "requirements.txt", "Cargo.toml", ".chad-project"]
        for marker in markers:
            if (dir_path / marker).exists(): return True
        return False

class FindProjectTool(BaseTool):
    """Tool to find a project directory on the system."""
    name = "find_project"
    description = "Quickly search for a coding project by name in standard locations. Much faster than a full disk search."
    category = "os"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "The name of the project (folder) to look for."
                    }
                },
                "required": ["project_name"]
            }
        }
    
    async def execute(self, project_name: str, **kwargs) -> Dict[str, Any]:
        logger.info(f"Searching for project: {project_name}")
        project_path = ProjectDiscovery.find_project(project_name)
        if project_path:
            return {
                "success": True,
                "project_name": project_name,
                "path": str(project_path),
                "is_project": ProjectDiscovery.is_project_dir(project_path)
            }
        return {"success": False, "error": f"Project '{project_name}' not found."}
