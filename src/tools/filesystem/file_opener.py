"""
File Opener Tool - Open local files with fuzzy file matching
"""

import os
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.core.filesystem.discovery_engine import DiscoveryEngine

logger = setup_logger("FileOpener")

class FileOpener(BaseTool):
    """Tool for opening local files (documents, images, etc.) with fuzzy matching."""
    
    name = "open_file"
    description = "Open a local file (document, image, video, etc.) using fuzzy matching. If you say a keyword like 'resume' or 'project document', I'll find the closest matching file and open it with the default application."
    category = "filesystem"
    
    # Fuzzy matching thresholds
    AUTO_OPEN_THRESHOLD = 70.0  # 70%+ similarity = auto-open
    CONFIRM_THRESHOLD = 50.0     # 50-70% = ask for confirmation
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize with Discovery Engine."""
        super().__init__(config)
        self.engine = DiscoveryEngine()
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration from ConfigManager (handles profile overrides)."""
        try:
            from src.shared.config_manager import ConfigManager
            self.SEARCH_PATHS = ConfigManager.get_search_paths()
            
            file_config = ConfigManager.get('file_opener', {})
            self.AUTO_OPEN_THRESHOLD = file_config.get('auto_open_threshold', 70.0)
            self.CONFIRM_THRESHOLD = file_config.get('confirm_threshold', 50.0)
            
            logger.debug(f"Loaded search paths: {self.SEARCH_PATHS}")
        except Exception as e:
            logger.error(f"Failed to load file opener config: {e}")
            self.SEARCH_PATHS = []

    def _find_file(self, user_input: str) -> tuple[Optional[str], Optional[str], float, str]:
        """
        Find the best matching file using DiscoveryEngine's deep fuzzy matching.
        
        Args:
            user_input: What the user said (e.g., "teaching tips")
            
        Returns:
            Tuple of (best_match_filename, full_path, similarity_score, status)
        """
        best_candidate = None
        
        # Search across all roots
        for search_root in self.SEARCH_PATHS:
            matches = self.engine.find(user_input, search_root)
            if matches and "error" not in matches[0]:
                match = matches[0]
                # If we haven't found a match or this is a better score
                if not best_candidate or match['score'] > best_candidate['score']:
                    best_candidate = match
        
        if not best_candidate:
            return None, None, 0.0, 'not_found'
            
        matched_filename = best_candidate['name']
        matched_path = best_candidate['path']
        score = best_candidate['score']
        
        logger.info(f"Matched File: '{matched_filename}' (Score: {score}) at {matched_path}")
        
        # Determine status based on score
        if score >= self.AUTO_OPEN_THRESHOLD:
            status = 'auto_open'
        else:
            status = 'needs_confirmation'
        
        return matched_filename, matched_path, score, status
    
    def _open_file(self, file_path: str) -> Dict[str, Any]:
        """
        Open a file with the default application.
        
        Args:
            file_path: Full path to the file
            
        Returns:
            Result dictionary
        """
        path = Path(file_path)
        
        logger.info(f"Opening: {file_path}")
        
        if not path.exists():
            error_msg = f"File not found: {file_path}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg
            }
        
        try:

            # Use os.startfile on Windows (opens with default application)
            if sys.platform == 'win32':
                os.startfile(file_path)
            else:
                # Unix-like: use xdg-open or similar
                import subprocess
                subprocess.Popen(['xdg-open', file_path])

            return {
                "success": True,
                "result": f"Opened {path.name}",
                "file": path.name,
                "path": file_path,
                "caption": f"Opened Local Sector: {path.name}"
            }
            
        except Exception as e:
            error_msg = f"Failed to open file: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg
            }
    
    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Name or keyword for the file to open (e.g., 'resume', 'project document', 'my photo'). Uses fuzzy matching to find the closest file."
                    }
                },
                "required": ["file_name"]
            }
        }
    
    async def execute(self, file_name: str, **kwargs) -> Dict[str, Any]:
        """
        Open a file using fuzzy matching.
        
        Args:
            file_name: What the user said (will be matched against available files)
            
        Returns:
            Result dictionary with success status and optional confirmation request
        """
        try:

            # Find the best matching file
            matched_filename, matched_path, score, status = self._find_file(file_name)
            
            if status == 'not_found':
                return {
                    "success": False,
                    "error": f"Could not find any file matching '{file_name}' in your Desktop, Documents, or Downloads. Try being more specific or use 'list_directory' to find where it is."
                }
            
            elif status == 'needs_confirmation':
                # Return a special result that indicates confirmation is needed
                return {
                    "success": False,
                    "needs_confirmation": True,
                    "suggested_file": matched_filename,
                    "confidence": score,
                    "message": f"Did you mean '{matched_filename}'? (confidence: {score:.0f}%)"
                }
            
            else:  # auto_open
                # Open the file
                return self._open_file(matched_path)
            
        except Exception as e:
            logger.error(f"Failed to open file: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to open file: {str(e)}"
            }
    
    def validate_params(self, file_name: str = None, **kwargs) -> tuple[bool, str | None]:
        """Validate parameters."""
        if not file_name or not file_name.strip():
            return False, "file_name cannot be empty"
        return True, None













