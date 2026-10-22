import logging
import requests
import os
from pathlib import Path
from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool

class FileDownloaderTool(BaseTool):
    """Tool for downloading files from the web."""
    
    name = "download_file"
    description = "Download a file from a URL to a local path. Use this for downloading external assets or assignments that aren't on Canvas."
    category = "system"  # Migrated from 'web' to 'system' utilities
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The direct download URL."
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Local path where the file should be saved (including filename)."
                    }
                },
                "required": ["url", "destination_path"]
            }
        }

    async def execute(self, url: str, destination_path: str, **kwargs) -> Dict[str, Any]:
        try:
            from src.shared.config_manager import ConfigManager
            dest = Path(ConfigManager.expand_path(destination_path))
            
            # Ensure directory exists
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            
            with open(dest, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return {
                "success": True,
                "result": f"Successfully downloaded file to {dest}",
                "path": str(dest),
                "size_bytes": dest.stat().st_size
            }
            
        except Exception as e:
            logging.error(f"Error in {self.name}: {e}")
            return {"success": False, "error": str(e)}
