"""
Organize Folder Skill - Intelligent & Safe Filesystem Organization.

This skill implements the "Shadow Execution" pattern:
1. Analyzes the target folder.
2. Creates a "Shadow Plan" (Simulation) of where files SHOULD go.
3. Presents this plan to the user via propose_file_plan.
4. Only executes on separate approval.
"""

from typing import Dict, Any, Optional, List
from collections import defaultdict
import logging
import os
from pathlib import Path

from src.core.skill_system.base_skill import BaseSkill
from src.tools.os.path_resolver_tool import PathResolverTool
from src.tools.filesystem.list_directory import ListDirectoryTool
from src.tools.filesystem.propose_file_plan import ProposeFilePlanTool

logger = logging.getLogger("OrganizeSkill")


class OrganizeFolderSkill(BaseSkill):
    """
    Skill: Intelligent Folder Organization.
    Uses semantic categorization and safe 'Shadow Execution' to organize files.
    """
    
    name = "organize_folder"
    description = (
        "Organize a folder into semantic categories (Images, Docs, etc.). "
        "Pass 'Downloads', 'Desktop', or a full path."
    )
    version = "1.0.0"
    category = "filesystem"
    
    # Default Semantic Categories (can be overridden via custom_categories parameter)
    CATEGORIES = {
        "_Images": {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.tiff', '.webp', '.ico', '.heic'},
        "_Documents": {'.pdf', '.doc', '.docx', '.txt', '.md', '.rtf', '.odt', '.xls', '.xlsx', '.csv', '.ppt', '.pptx'},
        "_Media": {'.mp4', '.mp3', '.wav', '.mov', '.avi', '.mkv', '.flac', '.webm', '.m4a'},
        "_Archives": {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.dmg', '.pkg'},
        "_Code": {'.py', '.js', '.html', '.css', '.json', '.xml', '.java', '.cpp', '.c', '.h', '.rs', '.go', '.ts', '.tsx', '.sh', '.bat'},
        "_Installers": {'.exe', '.msi'}
    }
    
    @staticmethod
    def analyze_files_for_categories(file_paths: List[str]) -> Dict[str, List[str]]:
        """Analyze a list of file paths and suggest custom categories."""
        ext_to_files = defaultdict(list)
        for path in file_paths:
            ext = Path(path).suffix.lower()
            if ext:
                ext_to_files[ext].append(Path(path).name)
        
        suggested = {}
        
        # Image extensions
        image_exts = [ext for ext in ext_to_files.keys() 
                      if ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.heic'}]
        if image_exts:
            suggested["Images"] = image_exts
        
        # Document extensions
        doc_exts = [ext for ext in ext_to_files.keys() 
                   if ext in {'.pdf', '.doc', '.docx', '.txt', '.md', '.rtf', '.odt'}]
        if doc_exts:
            suggested["Documents"] = doc_exts
        
        # Spreadsheet extensions
        sheet_exts = [ext for ext in ext_to_files.keys() 
                     if ext in {'.xls', '.xlsx', '.csv'}]
        if sheet_exts:
            suggested["Spreadsheets"] = sheet_exts
        
        # Media extensions
        media_exts = [ext for ext in ext_to_files.keys() 
                     if ext in {'.mp4', '.mp3', '.wav', '.mov', '.avi', '.mkv', '.flac', '.webm', '.m4a'}]
        if media_exts:
            suggested["Media"] = media_exts
        
        # Archive extensions
        archive_exts = [ext for ext in ext_to_files.keys() 
                       if ext in {'.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.dmg', '.pkg'}]
        if archive_exts:
            suggested["Archives"] = archive_exts
        
        # Code extensions
        code_exts = [ext for ext in ext_to_files.keys() 
                    if ext in {'.py', '.js', '.html', '.css', '.json', '.xml', '.java', '.cpp', '.c', '.h', '.rs', '.go', '.ts', '.tsx', '.sh', '.bat'}]
        if code_exts:
            suggested["Code"] = code_exts
        
        # Everything else
        other_exts = [ext for ext in ext_to_files.keys() 
                     if ext not in image_exts + doc_exts + sheet_exts + media_exts + archive_exts + code_exts]
        if other_exts:
            suggested["Other"] = other_exts
        
        return suggested
    
    def get_system_prompt(self) -> str:
        """System prompt for ReAct mode (not used in hardcoded chain)."""
        return """
        You are the 'Organize Folder' Sub-Agent.
        ONE goal: organize a folder into semantic category subfolders.
        1. RESOLVE path. 2. SCOUT files. 3. PROPOSE plan.
        """

    def _setup_tools(self) -> None:
        """Register tools."""
        self.skill_registry.register_tool(PathResolverTool())
        self.skill_registry.register_tool(ListDirectoryTool())
        self.skill_registry.register_tool(ProposeFilePlanTool())
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_folder": {
                        "type": "string",
                        "description": "Folder to organize (e.g., 'Downloads', 'Desktop', or full path)."
                    },
                    "custom_categories": {
                        "type": "object",
                        "description": "Optional: Custom mapping: {'Name': ['.ext1', ...]}.",
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "organization_strategy": {
                        "type": "string",
                        "enum": ["default", "semantic", "custom"],
                        "description": "Strategy: 'default' (standard), 'semantic' (smart), 'custom' (user-defined).",
                        "default": "default"
                    }
                },
                "required": ["target_folder"]
            }
        }

    async def execute(
        self, 
        target_folder: str = None, 
        input_text: str = None,
        custom_categories: Optional[Dict[str, list]] = None,
        organization_strategy: str = "default",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute the organize logic using "Shadow Execution" (Hardcoded Chain).
        
        This overrides the BaseSkill.execute ReAct loop for maximum reliability and speed.
        
        Args:
            target_folder: The folder to organize (e.g., "Downloads", "Desktop", or full path)
            input_text: Alternative parameter name (for compatibility with BaseSkill schema)
            custom_categories: Optional dict mapping category names to file extensions
                               e.g., {'Photos': ['.jpg', '.png'], 'Videos': ['.mp4']}
            organization_strategy: 'default' (use defaults), 'semantic' (analyze files), 
                                  or 'custom' (use only custom_categories)
            **kwargs: Additional context (e.g., _app_reference for UI integration)
        """
        # Handle both parameter names for compatibility
        folder = target_folder or input_text or "Downloads"
        
        # Determine which categories to use
        if organization_strategy == "custom" and custom_categories:
            categories = {name: set(exts) for name, exts in custom_categories.items()}
            logger.info(f"[organize_folder] Using custom categories: {list(categories.keys())}")
        elif custom_categories:
            # Merge custom with defaults (custom takes precedence)
            categories = self.CATEGORIES.copy()
            for name, exts in custom_categories.items():
                categories[name] = set(exts)
            logger.info(f"[organize_folder] Using merged categories (defaults + custom)")
        else:
            categories = self.CATEGORIES
            logger.info(f"[organize_folder] Using default categories")
        
        logger.info(f"[organize_folder] Starting organization of: {folder} (strategy: {organization_strategy})")
        
        try:
            # 1. RESOLVE PATH
            resolver = PathResolverTool()
            resolve_result = await resolver.execute(folder)
            
            if not resolve_result.get("success"):
                return {"success": False, "error": resolve_result.get("error")}
                
            abs_path = Path(resolve_result.get("resolved_path"))
            
            if not abs_path.exists() or not abs_path.is_dir():
                return {"success": False, "error": f"Target path does not exist or is not a folder: {abs_path}"}
            
            logger.info(f"[organize_folder] Resolved path: {abs_path}")
                
            # 2. SCOUT (Get file list) - Direct Python for speed
            files_to_move = []
            
            for item in os.scandir(abs_path):
                if item.is_file():
                    files_to_move.append(item)
            
            if not files_to_move:
                return {
                    "success": True, 
                    "result": f"📂 **{abs_path.name}** is already empty or has no loose files to organize."
                }
            
            logger.info(f"[organize_folder] Found {len(files_to_move)} files to categorize")
                
            # 3. BUILD SHADOW PLAN
            actions = []
            folders_needed = set()
            
            # If semantic strategy, analyze file names for better categorization
            if organization_strategy == "semantic":
                # For now, we'll use defaults but this could be enhanced with LLM analysis
                # For semantic mode, we could call a lightweight LLM to suggest categories
                # For MVP, we'll use defaults but log that semantic mode was requested
                logger.info(f"[organize_folder] Semantic mode requested - using enhanced defaults")
                # TODO: Could add LLM call here to analyze file names and suggest categories
                # For now, fall through to default categorization
            
            for item in files_to_move:
                ext = Path(item.path).suffix.lower()
                category = "_Other"  # Default for unknown extensions
                
                # Find matching category (using the determined categories dict)
                for cat_name, extensions in categories.items():
                    if ext in extensions:
                        category = cat_name
                        break
                
                # Build move operation
                dest_folder = abs_path / category
                dest_path = dest_folder / item.name
                
                # Track folders we need to create
                folders_needed.add(str(dest_folder))
                
                # Add move action if source != dest
                if str(Path(item.path)) != str(dest_path):
                    actions.append({
                        "action": "move",
                        "source": str(Path(item.path)),
                        "destination": str(dest_path)
                    })
            
            if not actions:
                return {
                    "success": True, 
                    "result": f"📂 **{abs_path.name}** is already organized based on current rules."
                }
            
            # Add folder creation actions at the beginning
            folder_actions = [
                {"action": "create_folder", "source": folder_path, "destination": folder_path}
                for folder_path in sorted(folders_needed)
            ]
            all_actions = folder_actions + actions
            
            logger.info(f"[organize_folder] Built plan: {len(folder_actions)} folders, {len(actions)} moves")
                
            # 4. PROPOSE PLAN via ProposeFilePlanTool (registers approval token)
            plan_tool = ProposeFilePlanTool(self.config)
            
            # Build a summary for the plan
            plan_summary = f"Organize {len(actions)} files in {abs_path.name} into {len(folders_needed)} category folders"
            
            # Execute the propose tool - this registers the token and shows UI
            plan_result = await plan_tool.execute(
                target_folder=str(abs_path),
                plan_summary=plan_summary,
                actions=all_actions,
                **kwargs  # Pass through _app_reference for UI integration
            )
            
            # Return the plan result (includes approval token, UI buttons, etc.)
            return plan_result

        except Exception as e:
            logger.error(f"[organize_folder] Error: {e}", exc_info=True)
            return {"success": False, "error": f"Organization failed: {str(e)}"}
