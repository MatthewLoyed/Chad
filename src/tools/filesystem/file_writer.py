"""
File Writer Tool - Create and write text files with safety validations
"""

import os
import sys
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.tools.base_tool import BaseTool
from src.tools.coding.code_verify import verify_source
from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.shared.file_registry import file_registry
import base64

logger = setup_logger("FileWriter")


def _artifacts_dir() -> str:
    """Project-local artifacts folder (cwd-relative), same convention as sandbox auto-delivery."""
    return os.path.abspath("artifacts")


class FileWriter(BaseTool):
    """Tool for creating text files with content in user-specified directories."""
    
    name = "write_file"
    description = (
        "Create a text file with specified content in a directory. "
        "Use this when the user asks to create, write, or make a file with text content. "
        "Default location is the project artifacts/ folder. "
        "You can specify a different directory like Desktop or Documents when the user asks."
    )
    category = "filesystem"
    
    # Default safety settings
    DEFAULT_BLOCKED_EXTENSIONS = [
        '.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.msi', '.dll',
        '.scr', '.com', '.pif', '.jar', '.wsf', '.wsh'
    ]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize with optional configuration."""
        super().__init__(config)
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration from ConfigManager (handles profile overrides)."""
        artifacts = _artifacts_dir()
        try:
            writer_cfg = ConfigManager.get_writer_config()
            
            raw_default = writer_cfg.get('default_directory', artifacts)
            self.default_directory = self._normalize_configured_dir(raw_default, artifacts)
            raw_allowed = writer_cfg.get('allowed_directories', [
                artifacts,
                os.path.expandvars(r'%USERPROFILE%\Documents'),
                os.path.expandvars(r'%USERPROFILE%\Desktop'),
                os.path.expandvars(r'%USERPROFILE%\Downloads'),
            ])
            self.allowed_directories = [
                self._normalize_configured_dir(d, artifacts) for d in raw_allowed
            ]
            self.open_after_write = writer_cfg.get('open_after_write', True)
            
            blocked_ext = writer_cfg.get('blocked_extensions', self.DEFAULT_BLOCKED_EXTENSIONS)
            self.blocked_extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in blocked_ext]
                
        except Exception as e:
            logger.error(f"Failed to load file writer config: {e}")
            self.default_directory = artifacts
            self.allowed_directories = [
                artifacts,
                os.path.expandvars(r'%USERPROFILE%\Documents'),
                os.path.expandvars(r'%USERPROFILE%\Desktop'),
                os.path.expandvars(r'%USERPROFILE%\Downloads'),
            ]
            self.open_after_write = True
            self.blocked_extensions = self.DEFAULT_BLOCKED_EXTENSIONS
            
        # Always allow project artifacts/ + cwd (workspace)
        if artifacts not in self.allowed_directories:
            self.allowed_directories.append(artifacts)
        cwd = os.getcwd()
        if cwd not in self.allowed_directories:
            self.allowed_directories.append(cwd)

    @staticmethod
    def _normalize_configured_dir(path: str, artifacts_fallback: str) -> str:
        """Expand env vars; treat bare 'artifacts' as cwd artifacts/."""
        if not path or str(path).strip().lower() in ("artifacts", "artifact"):
            return artifacts_fallback
        expanded = os.path.expandvars(str(path).strip())
        # Relative paths (e.g. "artifacts") resolve against cwd.
        if not os.path.isabs(expanded):
            return os.path.abspath(expanded)
        return os.path.normpath(expanded)
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize a filename to prevent directory traversal and invalid characters.
        
        Args:
            filename: The raw filename
            
        Returns:
            Sanitized filename
        """
        # Remove null bytes
        filename = filename.replace('\x00', '')
        
        # Remove path separators to prevent directory traversal
        filename = filename.replace('/', '').replace('\\', '')
        
        # Remove other problematic characters for Windows
        invalid_chars = '<>:"|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '')
        
        # Remove leading/trailing whitespace and dots
        filename = filename.strip().strip('.')
        
        # If filename becomes empty, use a default
        if not filename:
            filename = 'untitled.txt'
        
        # Ensure it has an extension (default to .txt)
        if '.' not in filename:
            filename += '.txt'
        
        return filename
    
    def _resolve_directory(self, directory: Optional[str]) -> tuple[str, bool, str]:
        """
        Resolve and validate the target directory.
        
        Args:
            directory: User-specified directory or None for default
            
        Returns:
            Tuple of (resolved_path, is_valid, error_message)
        """
        if directory is None:
            os.makedirs(self.default_directory, exist_ok=True)
            return self.default_directory, True, ""
        
        # Expand environment variables
        resolved = os.path.expandvars(directory)
        
        # Handle common shortcuts
        shortcuts = {
            'artifacts': _artifacts_dir(),
            'artifact': _artifacts_dir(),
            'downloads': os.path.expandvars(r'%USERPROFILE%\Downloads'),
            'documents': os.path.expandvars(r'%USERPROFILE%\Documents'),
            'desktop': os.path.expandvars(r'%USERPROFILE%\Desktop'),
            'download': os.path.expandvars(r'%USERPROFILE%\Downloads'),
            'document': os.path.expandvars(r'%USERPROFILE%\Documents'),
        }
        
        lower_dir = directory.lower().strip()
        if lower_dir in shortcuts:
            resolved = shortcuts[lower_dir]
        elif not os.path.isabs(resolved):
            resolved = os.path.abspath(resolved)
        
        # Normalize the path
        resolved = os.path.normpath(resolved)
        
        # Check if directory is in allowed list
        is_allowed = False
        for allowed in self.allowed_directories:
            allowed_norm = os.path.normpath(allowed)
            if resolved.lower() == allowed_norm.lower() or resolved.lower().startswith(allowed_norm.lower() + os.sep):
                is_allowed = True
                break
        
        if not is_allowed:
            return (
                resolved,
                False,
                f"Directory '{directory}' is not in the allowed list. "
                f"Allowed: artifacts, Documents, Desktop, Downloads",
            )
        
        # Create the directory if missing if it is inside artifacts or cwd; other
        # targets must already exist so we do not mkdir arbitrary user paths.
        if not os.path.isdir(resolved):
            artifacts_lower = _artifacts_dir().lower()
            cwd_lower = os.path.abspath(os.getcwd()).lower()
            res_lower = resolved.lower()
            if (
                res_lower == artifacts_lower
                or res_lower.startswith(artifacts_lower + os.sep)
                or res_lower == cwd_lower
                or res_lower.startswith(cwd_lower + os.sep)
            ):
                os.makedirs(resolved, exist_ok=True)
            else:
                return resolved, False, f"Directory does not exist: {resolved}"
        
        return resolved, True, ""
    
    def _is_extension_blocked(self, filename: str) -> bool:
        """Check if the file extension is blocked."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.blocked_extensions
    
    def _open_file(self, file_path: str) -> None:
        """
        Open the file. Prefers an explicit editor for text files to avoid 
        Windows "Select an app" prompts.
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            is_text = ext in ['.txt', '.md', '.log', '.yaml', '.json', '.py', '.js', '.css', '.html']
            
            # Get preferred editor from config (default to notepad.exe on Windows for text)
            default_editor = 'notepad.exe' if sys.platform == 'win32' else None
            editor = ConfigManager.get('file_writer.editor', default_editor)

            # Force notepad for specific extensions if on Windows and no editor set (or set to default)
            if sys.platform == 'win32' and is_text:
                if not editor:
                     editor = 'notepad.exe'

            if is_text and editor:
                import subprocess
                try:
                    subprocess.Popen([editor, file_path])
                    return
                except Exception as e:
                    logger.warning(f"Failed to open with explicit editor '{editor}': {e}. Falling back to startfile.")

            # Fallback to system default association
            if sys.platform == 'win32':
                os.startfile(file_path)
            else:
                import subprocess
                subprocess.Popen(['xdg-open', file_path])
        except Exception as e:
            logger.warning(f"Failed to open file: {e}")
    
    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Name of the file to create (e.g., 'dog_story.txt', 'notes.txt'). If no extension is provided, .txt will be added."
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write to the file."
                    },
                    "directory": {
                        "type": "string",
                        "description": (
                            "Target directory. Options: 'artifacts' (default), 'Documents', "
                            "'Desktop', 'Downloads'. Defaults to the project artifacts/ folder."
                        ),
                    },
                    "open_after": {
                        "type": "boolean",
                        "description": "Whether to open the file after creation. Defaults to true."
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Whether to overwrite if the file already exists. Defaults to false for safety."
                    }
                },
                "required": ["filename", "content"]
            }
        }
    
    async def execute(
        self,
        filename: str,
        content: str,
        directory: Optional[str] = None,
        open_after: Optional[bool] = None,
        overwrite: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a text file with the specified content.
        
        Args:
            filename: Name of the file to create
            content: Text content to write
            directory: Target directory (defaults to project artifacts/)
            open_after: Open file after creation (defaults to config setting)
            overwrite: Allow overwriting existing files
            
        Returns:
            Result dictionary with success status and file path
        """
        try:
            # If filename contains path separators, extract directory part
            if '/' in filename or '\\' in filename:
                file_dir, file_base = os.path.split(filename)
                if file_dir:
                    if not directory:
                        directory = file_dir
                    else:
                        directory = os.path.join(directory, file_dir)
                filename = file_base

            # Sanitize filename
            safe_filename = self._sanitize_filename(filename)
            logger.info(f"Creating file: {safe_filename}")
            
            # Check for blocked extensions
            if self._is_extension_blocked(safe_filename):
                ext = os.path.splitext(safe_filename)[1]
                return {
                    "success": False,
                    "error": f"File extension '{ext}' is not allowed for safety reasons. Please use a safe extension like .txt"
                }
            
            # Resolve and validate directory
            resolved_dir, is_valid, error_msg = self._resolve_directory(directory)
            if not is_valid:
                return {
                    "success": False,
                    "error": error_msg
                }
            
            # Build full path
            full_path = os.path.join(resolved_dir, safe_filename)
            
            # Check if file exists
            if os.path.exists(full_path) and not overwrite:
                return {
                    "success": False,
                    "error": f"File already exists: {safe_filename}. Set overwrite=true to replace it.",
                    "path": full_path
                }

            if ConfigManager.get("tools.code_verify_enabled", True):
                gate = verify_source(safe_filename, content)
                if not gate.ok:
                    return {
                        "success": False,
                        "error": gate.as_tool_error(),
                        "gate": gate.gate,
                    }
            
            # Write the file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"File created successfully: {full_path}")
            
            # Determine if we should open the file
            should_open = open_after if open_after is not None else self.open_after_write
            
            if should_open:
                self._open_file(full_path)
                action_msg = f"Created and opened '{safe_filename}'"
            else:
                action_msg = f"Created '{safe_filename}' at {resolved_dir}"
            
            # Apply artifact catcher (Automatic download support)
            result_msg, token = self._apply_artifact_catcher(full_path, safe_filename, content, action_msg)
            
            return {
                "success": True,
                "result": result_msg,
                "download_token": token,
                "filename": safe_filename,
                "path": full_path,
                "directory": resolved_dir,
                "opened": should_open,
                "caption": f"Created file: {safe_filename}",
                **({"auto_preview": True} if token and safe_filename.lower().endswith((".html", ".htm")) else {}),
            }
            
        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied: Cannot write to {resolved_dir}"
            }
        except Exception as e:
            logger.error(f"Failed to create file: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to create file: {str(e)}"
            }

    def _apply_artifact_catcher(self, full_path: str, filename: str, content: str, action_msg: str) -> tuple[str, Optional[str]]:
        """
        Automatically registers user-facing files in the FileRegistry to provide 
        a download button in the chat UI.
        
        Returns:
            Tuple of (display_message, download_token)
        """
        try:
            # 1. Scoping Check: user-facing dirs + project artifacts/
            facing_roots = [
                _artifacts_dir().lower(),
                os.path.expandvars(r'%USERPROFILE%\Downloads').lower(),
                os.path.expandvars(r'%USERPROFILE%\Documents').lower(),
                os.path.expandvars(r'%USERPROFILE%\Desktop').lower(),
            ]
            
            is_user_facing = False
            full_path_lower = full_path.lower()
            for root in facing_roots:
                if full_path_lower.startswith(root):
                    is_user_facing = True
                    break
            
            if not is_user_facing:
                return action_msg, None

            # 2. Extension Check
            catchable_exts = {
                '.html': 'text/html',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.pdf': 'application/pdf',
                '.txt': 'text/plain',
                '.md': 'text/markdown',
                '.json': 'application/json',
                '.csv': 'text/csv'
            }
            
            ext = os.path.splitext(filename)[1].lower()
            if ext not in catchable_exts:
                return action_msg, None

            # 3. Register the file
            encoded_data = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            token = file_registry.store_file(filename, encoded_data, catchable_exts[ext])
            
            # 4. Clean message (No more legacy download tags)
            logger.info(f"[ ARTIFACT CATCHER ] : Auto-generated download token for {filename} (Token: {token})")
            return action_msg, token

        except Exception as e:
            logger.warning(f"Artifact Catcher failed for {filename}: {e}")
            return action_msg, None
    
    def validate_params(
        self,
        filename: str = None,
        content: str = None,
        **kwargs
    ) -> tuple[bool, Optional[str]]:
        """Validate parameters."""
        if not filename or not filename.strip():
            return False, "filename cannot be empty"
        if content is None:
            return False, "content cannot be empty"
        return True, None
