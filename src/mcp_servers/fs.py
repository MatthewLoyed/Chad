"""
chad-fs - File System and Sandbox MCP Server for CHAD
Provides safe, robust, and audited filesystem and E2B sandbox capabilities.
"""

import os
import sys
import base64
import difflib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import asyncio

from mcp.server.fastmcp import FastMCP

from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.shared.file_registry import file_registry
from src.core.filesystem.discovery_engine import DiscoveryEngine

logger = setup_logger("chad-fs-server")

# Initialize the FastMCP server
mcp = FastMCP("chad-fs")

# Fuzzy matching and discovery engine singleton
_engine = DiscoveryEngine()

# E2B execution settings
EXECUTION_TIMEOUT = 300
_sandbox_instance: Optional[Any] = None

# Allowed text extensions for read_file
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', 
    '.csv', '.xml', '.html', '.css', '.java', '.c', '.cpp', '.h',
    '.sh', '.bat', '.ps1', '.log', '.ini', '.cfg', '.conf', '.toml',
    '.jsx', '.tsx', '.scss', '.sass', '.less', '.svg', '.vue', '.svelte',
    '.plan', '.spec'
}

# Maximum size for reading files (100KB)
MAX_FILE_SIZE = 100 * 1024

# Filesystem Safety Layer Definitions
ALLOWED_ROOTS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Pictures",
    Path.home() / "Videos",
    Path.home() / "Music",
    Path.cwd()  # Core workspace tree must always be allowed
]

PROTECTED_PATHS = [
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "C:\\$Recycle.Bin",
]

DEFAULT_BLOCKED_EXTENSIONS = [
    '.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.msi', '.dll',
    '.scr', '.com', '.pif', '.jar', '.wsf', '.wsh'
]


# ============================================================================
# HELPER FUNCTIONS & SAFETY CHECKS
# ============================================================================

def get_sandbox() -> Any:
    """Get or create a persistent E2B sandbox instance."""
    global _sandbox_instance
    if _sandbox_instance is None:
        from e2b_code_interpreter import Sandbox
        api_key = ConfigManager.get_secret("E2B_API_KEY")
        if not api_key:
            raise ValueError("Missing E2B_API_KEY in environment or config.")
        
        logger.info("[ E2B ] Initializing new Sandbox...")
        _sandbox_instance = Sandbox.create(api_key=api_key)
    return _sandbox_instance


def register_artifact_bytes(local_path: str, filename: str, content_bytes: bytes) -> Optional[str]:
    """Registers the file in the FileRegistry for UI download button support."""
    try:
        catchable_exts = {
            '.html': 'text/html',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.csv': 'text/csv',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg'
        }
        
        ext = os.path.splitext(filename)[1].lower()
        if ext not in catchable_exts:
            return None

        # Register the file using base64 encoded bytes
        encoded_data = base64.b64encode(content_bytes).decode('utf-8')
        token = file_registry.store_file(filename, encoded_data, catchable_exts[ext])
        
        logger.info(f"[ ARTIFACT ] Registered {filename} with token: {token}")
        return token

    except Exception as e:
        logger.warning(f"Artifact registration failed for {filename}: {e}")
        return None


def _get_files_snapshot(sb: Any) -> Dict[str, str]:
    """Helper to take a snapshot of filenames in common working directories of the sandbox."""
    snapshot = {}
    for path in ["/home/user", "/root", "/"]:
        try:
            for f in sb.files.list(path):
                if not f.is_dir:
                    snapshot[f.name] = f"{path}/{f.name}"
        except Exception:
            pass
    return snapshot


def _resolve_path(path_str: str) -> Path:
    """Resolve a path to an absolute path, expanding home user shortcuts and environment vars."""
    expanded = os.path.expanduser(os.path.expandvars(path_str))
    # Handle home directory mappings manually if needed
    lower_path = path_str.lower().strip()
    shortcuts = {
        'downloads': Path.home() / "Downloads",
        'documents': Path.home() / "Documents",
        'desktop': Path.home() / "Desktop",
        'download': Path.home() / "Downloads",
        'document': Path.home() / "Documents",
    }
    if lower_path in shortcuts:
        return shortcuts[lower_path].resolve()
        
    return Path(expanded).resolve()


def _is_safe_path(path: Path) -> bool:
    """Verify that a path falls within the allowed roots, the workspace, or standard user spaces."""
    try:
        resolved = path.resolve()
        
        # Guard against systemic system directories
        resolved_str = str(resolved).lower()
        for protected in PROTECTED_PATHS:
            if resolved_str.startswith(protected.lower()):
                return False
                
        # Must be in one of the ALLOWED_ROOTS
        for root in ALLOWED_ROOTS:
            try:
                root_resolved = root.resolve()
                if resolved.is_relative_to(root_resolved):
                    return True
            except ValueError:
                continue
                
        # Also check DiscoveryEngine root path just in case
        if resolved.is_relative_to(_engine.root_path):
            return True
            
        return False
    except Exception as e:
        logger.warning(f"Path safety check failed for {path}: {e}")
        return False


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent directory traversal and invalid characters."""
    filename = filename.replace('\x00', '')
    filename = filename.replace('/', '').replace('\\', '')
    invalid_chars = '<>:"|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    filename = filename.strip().strip('.')
    if not filename:
        filename = 'untitled.txt'
    if '.' not in filename:
        filename += '.txt'
    return filename


def _is_extension_blocked(filename: str) -> bool:
    """Check if the file extension is on the blocklist."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in DEFAULT_BLOCKED_EXTENSIONS


def _apply_artifact_catcher(full_path: Path, content: str, action_msg: str) -> tuple[str, Optional[str]]:
    """Automatically registers user-facing files to generate download tokens for the UI."""
    try:
        filename = full_path.name
        home_dirs = [
            (Path.home() / "Downloads").resolve(),
            (Path.home() / "Documents").resolve(),
            (Path.home() / "Desktop").resolve()
        ]
        
        is_user_facing = False
        resolved_path = full_path.resolve()
        for hdir in home_dirs:
            if resolved_path.is_relative_to(hdir):
                is_user_facing = True
                break
                
        if not is_user_facing:
            return action_msg, None

        catchable_exts = {
            '.html': 'text/html',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.csv': 'text/csv'
        }
        
        ext = full_path.suffix.lower()
        if ext not in catchable_exts:
            return action_msg, None

        encoded_data = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        token = file_registry.store_file(filename, encoded_data, catchable_exts[ext])
        
        logger.info(f"[ ARTIFACT CATCHER ] Registered {filename} (Token: {token})")
        return action_msg, token

    except Exception as e:
        logger.warning(f"Artifact Catcher failed: {e}")
        return action_msg, None


# ============================================================================
# TOOL DEFINITIONS (DECORATED WITH MCP DECORATORS)
# ============================================================================

@mcp.tool(meta={"hitl_class": "READ"})
async def read_file(path: str) -> Dict[str, Any]:
    """
    Read the text contents of a local file safely.
    Supports standard text formats (.txt, .md, .py, .json, .yaml, .csv, .html, .css, etc.).
    
    Args:
        path: Path to the target file.
    """
    try:
        resolved = _resolve_path(path)
        logger.info(f"mcp:read_file: {resolved}")
        
        if not resolved.exists():
            return {"success": False, "error": f"File not found: {path}"}
            
        if not resolved.is_file():
            return {"success": False, "error": f"Path is not a file: {path}"}
            
        if not _is_safe_path(resolved):
            return {"success": False, "error": "Access Denied: Path is outside allowed directories."}
            
        ext = resolved.suffix.lower()
        if ext not in TEXT_EXTENSIONS:
            return {
                "success": False, 
                "error": f"Cannot read binary or unsupported file type: {ext}. Supported text formats only."
            }
            
        file_size = resolved.stat().st_size
        if file_size > MAX_FILE_SIZE:
            return {
                "success": False,
                "error": f"File too large ({file_size / 1024:.1f}KB). Max supported size is {MAX_FILE_SIZE / 1024:.0f}KB."
            }
            
        try:
            content = await asyncio.to_thread(resolved.read_text, encoding='utf-8')
        except UnicodeDecodeError:
            content = await asyncio.to_thread(resolved.read_text, encoding='latin-1')
            
        display_content = content
        truncated = False
        if len(content) > 5000:
            display_content = content[:5000]
            truncated = True
            
        msg = f"Read {len(content)} characters from '{resolved.name}'"
        if truncated:
            msg += " (showing first 5000 characters)"
            
        return {
            "success": True,
            "result": msg,
            "filename": resolved.name,
            "path": str(resolved),
            "content": display_content,
            "full_content": content,
            "truncated": truncated,
            "size_bytes": file_size
        }
        
    except Exception as e:
        logger.error(f"Failed to read file {path}: {e}", exc_info=True)
        return {"success": False, "error": f"Read failed: {str(e)}"}


@mcp.tool(meta={"hitl_class": "WRITE"})
async def write_file(path: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
    """
    Write text content to a local file safely.
    Creates parent directories automatically if they do not exist.
    
    Args:
        path: Target file path.
        content: Text content to write.
        overwrite: Set to True to allow replacing an existing file.
    """
    try:
        resolved = _resolve_path(path)
        logger.info(f"mcp:write_file: {resolved}")
        
        # Verify safety rules
        if not _is_safe_path(resolved):
            return {"success": False, "error": "Access Denied: Path is outside allowed directories."}
            
        if _is_extension_blocked(resolved.name):
            return {"success": False, "error": f"File extension '{resolved.suffix}' is blocked for safety."}
            
        if resolved.exists() and not overwrite:
            return {
                "success": False,
                "error": f"File already exists: {resolved.name}. Set overwrite=True to replace it.",
                "path": str(resolved)
            }
            
        # Ensure parent directories exist
        resolved.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file content
        await asyncio.to_thread(resolved.write_text, content, encoding='utf-8')
        
        action_msg = f"Created '{resolved.name}' at {resolved.parent}"
        result_msg, token = _apply_artifact_catcher(resolved, content, action_msg)
        
        return {
            "success": True,
            "result": result_msg,
            "download_token": token,
            "filename": resolved.name,
            "path": str(resolved),
            "directory": str(resolved.parent)
        }
        
    except PermissionError:
        return {"success": False, "error": f"Permission denied: Cannot write to {path}."}
    except Exception as e:
        logger.error(f"Failed to write file {path}: {e}", exc_info=True)
        return {"success": False, "error": f"Write failed: {str(e)}"}


@mcp.tool(meta={"hitl_class": "WRITE"})
async def edit_file(
    path: str,
    target_content: str,
    replacement_content: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None
) -> Dict[str, Any]:
    """
    Edit a text file by replacing a unique occurrence of target_content with replacement_content.
    Can be scoped to a specific line range for precise edits.
    
    Args:
        path: Path to the target file.
        target_content: The exact text block inside the file to be replaced.
        replacement_content: The new text block to insert instead.
        start_line: Optional starting line number (1-indexed, inclusive) to search within.
        end_line: Optional ending line number (1-indexed, inclusive) to search within.
    """
    try:
        resolved = _resolve_path(path)
        logger.info(f"mcp:edit_file: {resolved}")
        
        if not resolved.exists():
            return {"success": False, "error": f"File not found: {path}"}
            
        if not _is_safe_path(resolved):
            return {"success": False, "error": "Access Denied: Path is outside allowed directories."}
            
        if _is_extension_blocked(resolved.name):
            return {"success": False, "error": f"File extension '{resolved.suffix}' is blocked for safety."}
            
        # Read current content
        try:
            content = await asyncio.to_thread(resolved.read_text, encoding='utf-8')
        except UnicodeDecodeError:
            content = await asyncio.to_thread(resolved.read_text, encoding='latin-1')
            
        lines = content.splitlines(keepends=True)
        
        # Scoping logic
        if start_line is not None or end_line is not None:
            start_idx = (start_line - 1) if start_line is not None else 0
            end_idx = end_line if end_line is not None else len(lines)
            
            # Bound idx checks
            start_idx = max(0, min(start_idx, len(lines)))
            end_idx = max(start_idx, min(end_idx, len(lines)))
            
            scoped_section = "".join(lines[start_idx:end_idx])
            
            occurrences = scoped_section.count(target_content)
            if occurrences == 0:
                # Provide a helpful diff of why it failed to match
                return {
                    "success": False,
                    "error": (
                        f"Target content not found in line range [{start_idx+1}, {end_idx}].\n"
                        "Please verify line numbers or target_content matching exactly."
                    )
                }
            elif occurrences > 1:
                return {
                    "success": False,
                    "error": f"Multiple occurrences ({occurrences}) of target content found in line range [{start_idx+1}, {end_idx}]. Narrow the range or target content."
                }
                
            # Perform targeted replacement
            replaced_section = scoped_section.replace(target_content, replacement_content, 1)
            new_lines = lines[:start_idx] + [replaced_section] + lines[end_idx:]
            new_content = "".join(new_lines)
        else:
            # Global replacement
            occurrences = content.count(target_content)
            if occurrences == 0:
                return {
                    "success": False,
                    "error": "Target content not found anywhere in the file. Ensure whitespace matches exactly."
                }
            elif occurrences > 1:
                return {
                    "success": False,
                    "error": f"Multiple occurrences ({occurrences}) of target content found globally. Please provide a start_line/end_line scope."
                }
                
            new_content = content.replace(target_content, replacement_content, 1)
            
        # Write modified content
        await asyncio.to_thread(resolved.write_text, new_content, encoding='utf-8')
        
        action_msg = f"Successfully edited '{resolved.name}'"
        result_msg, token = _apply_artifact_catcher(resolved, new_content, action_msg)
        
        return {
            "success": True,
            "result": result_msg,
            "download_token": token,
            "filename": resolved.name,
            "path": str(resolved)
        }
        
    except Exception as e:
        logger.error(f"Failed to edit file {path}: {e}", exc_info=True)
        return {"success": False, "error": f"Edit failed: {str(e)}"}


@mcp.tool(meta={"hitl_class": "READ"})
async def list_directory(path: Optional[str] = None, depth: int = 1) -> Dict[str, Any]:
    """
    List the contents of a directory. Returns a lightweight, clean representation 
    of files and subfolders, avoiding binary files and common system noise.
    
    Args:
        path: Path to the target directory. If None or empty, list the current workspace.
        depth: Recursive scanning depth limit. Default is 1. Maximum is 2.
    """
    try:
        # Coerce/fallback depth if None or invalid type
        if depth is None:
            depth = 1
        else:
            try:
                depth = int(depth)
            except (ValueError, TypeError):
                depth = 1

        resolved_path = _resolve_path(path) if path else Path.cwd().resolve()
        logger.info(f"mcp:list_directory: {resolved_path}")
        
        if not resolved_path.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
            
        if not resolved_path.is_dir():
            return {"success": False, "error": f"Path is not a directory: {path}"}
            
        if not _is_safe_path(resolved_path):
            return {"success": False, "error": "Access Denied: Path is outside allowed directories."}
            
        # Use DiscoveryEngine's recursive scan if it's relative to %USERPROFILE% boundary,
        # otherwise run our own safe recursive scanner to work properly inside the workspace.
        try:
            rel = resolved_path.relative_to(_engine.root_path)
            # Within DiscoveryEngine boundary
            result = await asyncio.to_thread(_engine.scout, str(rel), depth)
            if "error" in result:
                return {"success": False, "error": result["error"]}
            return {
                "success": True,
                "result": result,
                "caption": f"List: {path if path else 'Workspace'}"
            }
        except ValueError:
            # Outside User Profile (e.g. Workspace root is in different drive/folder)
            # Run local implementation of _scan_dir to mirror identical safety & noise-filtering behavior
            def scan_local_dir(dir_path: Path, current_depth: int) -> Dict[str, Any]:
                structure = {
                    "name": dir_path.name or str(dir_path),
                    "type": "folder",
                    "children": [],
                    "status": "partial"
                }
                if current_depth >= depth:
                    structure["status"] = "depth_limit_reached"
                    return structure
                    
                try:
                    all_items = sorted(list(dir_path.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
                    visible = []
                    for item in all_items:
                        if _engine._is_ignored(item.name):
                            continue
                        if item.is_file() and item.suffix.lower() in _engine.IGNORE_EXTENSIONS:
                            continue
                        visible.append(item)
                        
                    total_visible = len(visible)
                    if total_visible <= _engine.HARD_SAFE_LIMIT:
                        display = visible
                        truncated = False
                    else:
                        display = visible[:_engine.SOFT_ITEM_LIMIT]
                        truncated = True
                        
                    children = []
                    for item in display:
                        if item.is_dir():
                            children.append(scan_local_dir(item, current_depth + 1))
                        else:
                            children.append({"name": item.name, "type": "file"})
                            
                    structure["children"] = children
                    structure["total_items"] = total_visible
                    structure["item_count"] = total_visible
                    if truncated:
                        structure["truncation_warning"] = f"⚠️ TRUNCATION ACTIVE: Showing {len(display)} of {total_visible} items."
                    structure["status"] = "scanned"
                    return structure
                except PermissionError:
                    return {"name": dir_path.name, "type": "folder", "error": "Permission Denied"}
                except Exception as e:
                    return {"name": dir_path.name, "type": "folder", "error": str(e)}
                    
            result = await asyncio.to_thread(scan_local_dir, resolved_path, 0)
            return {
                "success": True,
                "result": result,
                "caption": f"List: {path if path else 'Workspace'}"
            }
            
    except Exception as e:
        logger.error(f"Failed to list directory {path}: {e}", exc_info=True)
        return {"success": False, "error": f"List failed: {str(e)}"}


@mcp.tool(meta={"hitl_class": "WRITE"})
async def execute_python(code: str) -> Dict[str, Any]:
    """
    Execute Python code in a secure, isolated E2B sandbox.
    Use this for data analysis, document generation, and complex calculations.
    Returns stdout/stderr, and automatically retrieves any generated document/media outputs.
    
    Args:
        code: The Python code to run in the sandbox.
    """
    try:
        sb = get_sandbox()
        logger.info("[ E2B ] Executing Python code...")
        
        # Take snapshot of files before running
        before_files = _get_files_snapshot(sb)
        
        # Run code synchronously using to_thread
        execution = await asyncio.to_thread(sb.run_code, code, timeout=EXECUTION_TIMEOUT)
        
        # Take snapshot after running to find newly created files
        after_files = _get_files_snapshot(sb)
        new_files = {name: path for name, path in after_files.items() if name not in before_files}
        
        auto_downloaded = []
        auto_downloaded_msgs = []
        allowed_extensions = {'.docx', '.xlsx', '.pdf', '.png', '.csv', '.html', '.jpg', '.jpeg'}
        
        for name, path in new_files.items():
            ext = os.path.splitext(name)[1].lower()
            if ext in allowed_extensions:
                try:
                    # Read the binary data from the sandbox
                    content = await asyncio.to_thread(sb.files.read, path, format="bytes")
                    
                    artifacts_dir = Path("artifacts").resolve()
                    artifacts_dir.mkdir(exist_ok=True)
                    local_path = artifacts_dir / name
                    
                    # Write locally
                    await asyncio.to_thread(local_path.write_bytes, content)
                    
                    # Register file registry
                    token = register_artifact_bytes(str(local_path), name, content)
                    auto_downloaded.append({
                        "filename": name,
                        "local_path": str(local_path),
                        "token": token
                    })
                    auto_downloaded_msgs.append(f"Auto-delivered generated artifact: {name}")
                except Exception as download_err:
                    logger.warning(f"Auto-download failed for {name}: {download_err}")
                    
        stdout = execution.logs.stdout or ""
        if auto_downloaded_msgs:
            notice = "\n\n" + "\n".join([f"[ SYSTEM NOTICE ] {msg}" for msg in auto_downloaded_msgs])
            stdout += notice
            
        return {
            "success": True,
            "stdout": stdout,
            "stderr": execution.logs.stderr,
            "results": [str(r) for r in execution.results],
            "error": execution.error if execution.error else None,
            "auto_delivered_artifacts": auto_downloaded if auto_downloaded else None
        }
        
    except Exception as e:
        logger.error(f"E2B Python Execution failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run()
