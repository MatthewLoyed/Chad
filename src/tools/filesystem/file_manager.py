"""
File Manager Tool - Safe Filesystem Operations

Provides Chad with the ability to move, rename, delete files and create folders.
All operations are logged to an audit file for transparency and potential undo.

SAFETY LAYERS:
1. ALLOWED_ROOTS: Deterministic whitelist of directories that can be modified
2. APPROVAL_TOKEN: Cryptographic one-time token required for destructive actions
3. Protected Paths: System directories are blocklisted
4. Audit Logging: All operations logged to ~/.chad_fs_audit.json
"""

import os
import shutil
import json
import subprocess
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("FileManager")

# ============================================================================
# APPROVAL TOKEN REGISTRY (shared with propose_file_plan.py)
# ============================================================================
# This is an in-memory store. Tokens expire after 5 minutes.
_pending_approvals: Dict[str, Dict[str, Any]] = {}


def generate_approval_token() -> str:
    """Generate a human-readable approval token."""
    return f"CHAD-{secrets.token_hex(2).upper()}"  # e.g., "CHAD-A7X9"


def register_approval(token: str, plan: Dict[str, Any], expires_minutes: int = 5):
    """Register a pending approval with expiration."""
    _pending_approvals[token] = {
        "plan": plan,
        "expires_at": datetime.now() + timedelta(minutes=expires_minutes),
        "created_at": datetime.now().isoformat()
    }
    logger.info(f"[ APPROVAL ] Registered token {token}, expires in {expires_minutes} min")


def consume_approval(token: str) -> Optional[Dict[str, Any]]:
    """Consume a one-time approval token. Returns the plan if valid, None otherwise."""
    if token not in _pending_approvals:
        return None
    
    approval = _pending_approvals.pop(token)
    
    if datetime.now() > approval["expires_at"]:
        logger.warning(f"[ APPROVAL ] Token {token} expired")
        return None
    
    logger.info(f"[ APPROVAL ] Token {token} consumed successfully")
    return approval["plan"]


class FileManagerTool(BaseTool):
    """
    Tool for safe filesystem operations: move, rename, delete, create folder.
    """
    
    name = "file_manager"
    description = (
        "Perform filesystem operations: move, rename, delete files, or create folders. "
        "Use 'open_folder' action to show the folder in File Explorer. "
        "SAFETY: Requires a valid approval_token from user confirmation. "
        "For batch operations, use propose_file_plan first to get user approval."
    )
    category = "filesystem"
    
    # ========================================================================
    # LAYER 1: ALLOWED_ROOTS - Directories that CAN be modified
    # ========================================================================
    ALLOWED_ROOTS = [
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / "Pictures",
        Path.home() / "Videos",
        Path.home() / "Music",
    ]
    
    # System paths that should NEVER be modified (defense in depth)
    PROTECTED_PATHS = [
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\$Recycle.Bin",
    ]
    
    # Audit log location
    AUDIT_LOG_PATH = Path.home() / ".chad_fs_audit.json"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # Load any additional protected paths from config
        if config and "protected_paths" in config:
            self.PROTECTED_PATHS.extend(config["protected_paths"])
        # Load any additional allowed roots from config
        if config and "allowed_roots" in config:
            for root in config["allowed_roots"]:
                self.ALLOWED_ROOTS.append(Path(root).expanduser().resolve())
                
        # Dynamically whitelist the process current working directory (project workspace)
        cwd_path = Path(os.getcwd()).resolve()
        if cwd_path not in self.ALLOWED_ROOTS:
            self.ALLOWED_ROOTS.append(cwd_path)
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["move", "rename", "create_folder", "open_folder"],
                        "description": "The filesystem operation to perform."
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Source file/folder path (for move, rename, delete)."
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Destination path (for move, rename, create_folder)."
                    },
                    "folder_path": {
                        "type": "string",
                        "description": "Folder path to open in File Explorer (for open_folder action)."
                    },
                    "approval_token": {
                        "type": "string",
                        "description": "Approval token from user (required for modify operations). Format: 'CHAD-XXXX'."
                    }
                },
                "required": ["action"]
            }
        }
    
    def _is_in_allowed_roots(self, path: Path) -> bool:
        """Check if a path is inside one of the allowed root directories."""
        resolved = path.resolve()
        for root in self.ALLOWED_ROOTS:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    
    def _is_protected(self, path: str) -> bool:
        """Check if a path is in the protected list."""
        path_obj = Path(path).resolve()
        path_str = str(path_obj).lower()
        
        for protected in self.PROTECTED_PATHS:
            if path_str.startswith(protected.lower()):
                return True
        return False
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve a path, expanding ~ and environment variables."""
        expanded = os.path.expanduser(os.path.expandvars(path))
        return Path(expanded).resolve()
    
    def _log_operation(self, action: str, details: Dict[str, Any], success: bool):
        """Append operation to audit log for transparency and undo capability."""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "action": action,
                "details": details,
                "success": success
            }
            
            # Load existing log or create new
            if self.AUDIT_LOG_PATH.exists():
                with open(self.AUDIT_LOG_PATH, 'r') as f:
                    audit_log = json.load(f)
            else:
                audit_log = {"operations": []}
            
            audit_log["operations"].append(log_entry)
            
            # Keep only last 1000 operations to prevent unbounded growth
            if len(audit_log["operations"]) > 1000:
                audit_log["operations"] = audit_log["operations"][-1000:]
            
            with open(self.AUDIT_LOG_PATH, 'w') as f:
                json.dump(audit_log, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    async def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute the requested filesystem operation."""
        approval_token = kwargs.get("approval_token")
        
        # ====================================================================
        # LAYER 2: TOKEN-BASED APPROVAL FOR DESTRUCTIVE ACTIONS
        # ====================================================================
        if action in ["move", "rename", "delete", "create_folder"]:
            if not approval_token:
                return {
                    "success": False,
                    "error": "SAFETY LOCK: Missing approval_token. "
                             "You must first call 'propose_file_plan' and get user approval. "
                             "The user must provide the approval code (e.g., 'CHAD-A7X9')."
                }
            
            # Validate the token (but don't consume it yet - let the action do that)
            if approval_token not in _pending_approvals:
                return {
                    "success": False,
                    "error": f"SAFETY LOCK: Invalid or expired approval token '{approval_token}'. "
                             "Please request a new plan from the user."
                }
            
            # Check expiration without consuming
            if datetime.now() > _pending_approvals[approval_token]["expires_at"]:
                _pending_approvals.pop(approval_token, None)
                return {
                    "success": False,
                    "error": f"SAFETY LOCK: Approval token '{approval_token}' has expired. "
                             "Please request a new plan from the user."
                }
        
        if action == "move":
            return await self._move_file(kwargs.get("source_path"), kwargs.get("destination_path"), approval_token)
        elif action == "rename":
            return await self._rename_file(kwargs.get("source_path"), kwargs.get("destination_path"), approval_token)
        elif action == "delete":
            return {"success": False, "error": "CRITICAL: The 'delete' action has been disabled by the user for safety."}
        elif action == "create_folder":
            return await self._create_folder(kwargs.get("destination_path"), approval_token)
        elif action == "open_folder":
            return await self._open_folder(kwargs.get("folder_path"))
        else:
            return {"success": False, "error": f"Unknown action: {action}"}
    
    async def _move_file(self, source: str, destination: str, token: str = None) -> Dict[str, Any]:
        """Move a file or folder to a new location."""
        if not source or not destination:
            return {"success": False, "error": "Both source_path and destination_path are required."}
        
        source_path = self._resolve_path(source)
        dest_path = self._resolve_path(destination)
        
        # LAYER 1: Check ALLOWED_ROOTS
        if not self._is_in_allowed_roots(source_path):
            return {
                "success": False, 
                "error": f"SAFETY: Source path '{source_path}' is not in allowed directories. "
                         f"Allowed: {[str(r) for r in self.ALLOWED_ROOTS]}"
            }
        if not self._is_in_allowed_roots(dest_path):
            return {
                "success": False, 
                "error": f"SAFETY: Destination path '{dest_path}' is not in allowed directories. "
                         f"Allowed: {[str(r) for r in self.ALLOWED_ROOTS]}"
            }
        
        # Protected path check (defense in depth)
        if self._is_protected(str(source_path)):
            return {"success": False, "error": f"Cannot move from protected path: {source_path}"}
        if self._is_protected(str(dest_path)):
            return {"success": False, "error": f"Cannot move to protected path: {dest_path}"}
        if not source_path.exists():
            return {"success": False, "error": f"Source does not exist: {source_path}"}
        
        try:
            # If destination is a directory, move into it
            if dest_path.is_dir():
                final_dest = dest_path / source_path.name
            else:
                # Ensure parent directory exists
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                final_dest = dest_path
            
            shutil.move(str(source_path), str(final_dest))
            
            self._log_operation("move", {
                "source": str(source_path),
                "destination": str(final_dest),
                "token_used": token
            }, success=True)
            
            logger.info(f"[ FILE_MANAGER ] Moved: {source_path} → {final_dest}")
            return {
                "success": True,
                "result": f"Moved '{source_path.name}' to '{final_dest}'",
                "caption": f"Moved: {source_path.name}"
            }
            
        except Exception as e:
            self._log_operation("move", {"source": source, "destination": destination}, success=False)
            return {"success": False, "error": f"Move failed: {str(e)}"}
    
    async def _rename_file(self, source: str, new_name: str, token: str = None) -> Dict[str, Any]:
        """Rename a file or folder."""
        if not source or not new_name:
            return {"success": False, "error": "Both source_path and destination_path (new name) are required."}
        
        source_path = self._resolve_path(source)
        
        # LAYER 1: Check ALLOWED_ROOTS
        if not self._is_in_allowed_roots(source_path):
            return {
                "success": False, 
                "error": f"SAFETY: Path '{source_path}' is not in allowed directories."
            }
        
        if self._is_protected(str(source_path)):
            return {"success": False, "error": f"Cannot rename protected path: {source_path}"}
        if not source_path.exists():
            return {"success": False, "error": f"Source does not exist: {source_path}"}
        
        try:
            # new_name could be just a filename or a full path
            if os.path.isabs(new_name):
                new_path = Path(new_name)
            else:
                new_path = source_path.parent / new_name
            
            source_path.rename(new_path)
            
            self._log_operation("rename", {
                "source": str(source_path),
                "new_name": str(new_path),
                "token_used": token
            }, success=True)
            
            logger.info(f"[ FILE_MANAGER ] Renamed: {source_path} → {new_path}")
            return {
                "success": True,
                "result": f"Renamed '{source_path.name}' to '{new_path.name}'",
                "caption": f"Renamed: {new_path.name}"
            }
            
        except Exception as e:
            self._log_operation("rename", {"source": source, "new_name": new_name}, success=False)
            return {"success": False, "error": f"Rename failed: {str(e)}"}
    
    async def _delete_file(self, source: str, token: str = None) -> Dict[str, Any]:
        """Delete a file or folder (permanently)."""
        if not source:
            return {"success": False, "error": "source_path is required."}
        
        source_path = self._resolve_path(source)
        
        # LAYER 1: Check ALLOWED_ROOTS
        if not self._is_in_allowed_roots(source_path):
            return {
                "success": False, 
                "error": f"SAFETY: Path '{source_path}' is not in allowed directories."
            }
        
        if self._is_protected(str(source_path)):
            return {"success": False, "error": f"Cannot delete protected path: {source_path}"}
        if not source_path.exists():
            return {"success": False, "error": f"Source does not exist: {source_path}"}
        
        try:
            was_directory = source_path.is_dir()
            if was_directory:
                shutil.rmtree(str(source_path))
            else:
                source_path.unlink()
            
            self._log_operation("delete", {
                "path": str(source_path),
                "was_directory": was_directory,
                "token_used": token
            }, success=True)
            
            logger.info(f"[ FILE_MANAGER ] Deleted: {source_path}")
            return {
                "success": True,
                "result": f"Deleted '{source_path.name}'",
                "caption": f"Deleted: {source_path.name}"
            }
            
        except Exception as e:
            self._log_operation("delete", {"path": source}, success=False)
            return {"success": False, "error": f"Delete failed: {str(e)}"}
    
    async def _create_folder(self, folder_path: str, token: str = None) -> Dict[str, Any]:
        """Create a new folder (and any necessary parent folders)."""
        if not folder_path:
            return {"success": False, "error": "destination_path is required."}
        
        path = self._resolve_path(folder_path)
        
        # LAYER 1: Check ALLOWED_ROOTS
        if not self._is_in_allowed_roots(path):
            return {
                "success": False, 
                "error": f"SAFETY: Path '{path}' is not in allowed directories. "
                         f"Allowed: {[str(r) for r in self.ALLOWED_ROOTS]}"
            }
        
        if self._is_protected(str(path)):
            return {"success": False, "error": f"Cannot create folder in protected path: {path}"}
        
        try:
            path.mkdir(parents=True, exist_ok=True)
            
            self._log_operation("create_folder", {
                "path": str(path),
                "token_used": token
            }, success=True)
            
            logger.info(f"[ FILE_MANAGER ] Created folder: {path}")
            return {
                "success": True,
                "result": f"Created folder '{path}'",
                "caption": f"Created: {path.name}"
            }
            
        except Exception as e:
            self._log_operation("create_folder", {"path": folder_path}, success=False)
            return {"success": False, "error": f"Create folder failed: {str(e)}"}
    
    async def _open_folder(self, folder_path: str) -> Dict[str, Any]:
        """Open a folder in File Explorer for user visibility."""
        if not folder_path:
            return {"success": False, "error": "folder_path is required."}
        
        path = self._resolve_path(folder_path)
        
        if not path.exists():
            return {"success": False, "error": f"Folder does not exist: {path}"}
        if not path.is_dir():
            # If it's a file, open the parent folder
            path = path.parent
        
        try:
            # Windows: Use explorer.exe
            subprocess.Popen(["explorer", str(path)])
            
            logger.info(f"[ FILE_MANAGER ] Opened in Explorer: {path}")
            return {
                "success": True,
                "result": f"Opened '{path}' in File Explorer",
                "caption": f"Opened: {path.name}"
            }
            
        except Exception as e:
            return {"success": False, "error": f"Failed to open folder: {str(e)}"}
    
    def validate_params(self, action: str = None, **kwargs) -> tuple[bool, Optional[str]]:
        if not action:
            return False, "action is required"
        if action not in ["move", "rename", "delete", "create_folder", "open_folder"]:
            return False, f"Invalid action: {action}"
        return True, None
