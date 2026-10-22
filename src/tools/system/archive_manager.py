import logging
import zipfile
import tarfile
import os
from pathlib import Path
from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool

class ArchiveManagerTool(BaseTool):
    """Tool for compressing and extracting ZIP or TAR archives."""
    
    name = "archive_manager"
    description = "Compress folders into ZIP files or extract archives (.zip, .tar, .tar.gz). Use this for zipping project folders for submission or extracting downloaded assignments."
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
                        "enum": ["zip", "unzip"],
                        "description": "Whether to compress or extract."
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Path to the folder to zip or the archive to extract."
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Target path for the resulting zip file or extraction directory."
                    }
                },
                "required": ["action", "source_path"]
            }
        }

    async def execute(self, action: str, source_path: str, destination_path: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            from src.shared.config_manager import ConfigManager
            source = Path(ConfigManager.expand_path(source_path))
            
            if not source.exists():
                return {"success": False, "error": f"Source path not found: {source}"}

            if action == "zip":
                if not destination_path:
                    destination_path = str(source.with_suffix(".zip"))
                
                dest = Path(ConfigManager.expand_path(destination_path))
                
                with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(source):
                        for file in files:
                            file_path = Path(root) / file
                            # Use relative path for zip internal structure
                            arcname = file_path.relative_to(source.parent)
                            zipf.write(file_path, arcname)
                
                return {
                    "success": True,
                    "result": f"Successfully created archive at {dest}",
                    "zip_path": str(dest)
                }

            elif action == "unzip":
                if not destination_path:
                    # Default: extract to a folder with same name as archive
                    destination_path = str(source.parent / source.stem)
                
                dest = Path(ConfigManager.expand_path(destination_path))
                dest.mkdir(parents=True, exist_ok=True)
                
                # Standard extraction based on suffix
                success = False
                if source.suffix.lower() == ".zip":
                    with zipfile.ZipFile(source, 'r') as zip_ref:
                        zip_ref.extractall(dest)
                    success = True
                elif source.suffix.lower() in [".tar", ".gz", ".tgz"]:
                    with tarfile.open(source, 'r:*') as tar_ref:
                        tar_ref.extractall(dest)
                    success = True
                else:
                    # Fallback: Try as ZIP anyway (helps with instructor-renamed files)
                    try:
                        with zipfile.ZipFile(source, 'r') as zip_ref:
                            zip_ref.extractall(dest)
                        success = True
                        logging.warning(f"[ArchiveManager] Extracted {source.name} as ZIP despite extension {source.suffix}")
                    except Exception:
                        return {"success": False, "error": f"Unsupported or corrupt archive format: {source.suffix}"}
                
                if success:
                    return {
                        "success": True,
                        "result": f"Successfully extracted {source.name} to {dest}",
                        "extraction_path": str(dest)
                    }

            return {"success": False, "error": f"Unknown action: {action}"}
            
        except Exception as e:
            logging.error(f"Error in {self.name}: {e}")
            return {"success": False, "error": str(e)}
