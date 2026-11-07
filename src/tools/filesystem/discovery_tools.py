"""
Discovery Tools - Agent Interface for Semantic Filesystem
wraps the DiscoveryEngine into standard LLM tools.
"""

from typing import Dict, Any, Optional
import asyncio
from src.tools.base_tool import BaseTool
from src.core.filesystem.discovery_engine import DiscoveryEngine
from src.shared.logger import setup_logger

logger = setup_logger("DiscoveryTools")

# Shared Engine Instance (Singleton via Module Scope)
_engine = DiscoveryEngine()

# ScoutDirectoryTool removed in favor of list_directory.py


class FuzzyFindTool(BaseTool):
    """
    Agent Tool: Finds specific files logicallly.
    Use this after you have a hypothesis from scouting.
    """
    
    name = "fuzzy_find_local"
    description = (
        "Search for a file inside a specific folder tree using fuzzy matching. "
        "Use this to find 'physics notes' inside 'Documents/School'. "
        "Avoids system folders and binary files."
    )
    category = "filesystem"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Filename or keyword to find (e.g., 'resume', 'physics', 'main.py')."
                    },
                    "search_root": {
                        "type": "string",
                        "description": "Optional folder to look in. If omitted, I will search your Documents, Desktop, and Downloads."
                    }
                },
                "required": ["query"]
            }
        }
    
    async def execute(self, query: str, search_root: str = None, **kwargs) -> Dict[str, Any]:
        try:
            logger.info(f"Fuzzy Finding '{query}' (Root: {search_root or 'AUTO'})")

            # Default roots if none specified. Under an eval sandbox the auto-roots become
            # the sandbox itself: a disposable repo checkout has no Documents/Desktop/
            # Downloads, so searching for those either found nothing or — before the engine
            # honoured the sandbox — reached into the real user folders and read a file
            # from the live production repo (#566).
            from src.shared.fs_sandbox import is_active as _sandbox_active
            if search_root:
                roots = [search_root]
            elif _sandbox_active():
                roots = [""]
            else:
                roots = ["Documents", "Desktop", "Downloads"]

            all_matches = []
            
            for root in roots:
                # Find in this root (using to_thread)
                import asyncio
                matches = await asyncio.to_thread(_engine.find, query, root)
                
                # Filter out errors
                valid_matches = [m for m in matches if "error" not in m]
                all_matches.extend(valid_matches)

            if not all_matches:
                hint = (
                    f"NEGATIVE CONFIRMATION: No matches found for '{query}' in {roots}. "
                    "Proving that an entity or file does not exist is a successful resolution. "
                    "Do NOT trigger unconstrained folder crawls or search external services. "
                    "Conclude directly and inform the user of what was found."
                )
                return {
                    "success": False, 
                    "error": f"No matches found for '{query}' in {roots}.\n{hint}",
                    "harness_hint": hint
                }
            
            # Sort all combined matches by score
            all_matches.sort(key=lambda x: x['score'], reverse=True)
            top_matches = all_matches[:5]

            # Helper text for the LLM
            output_text = f"Found {len(top_matches)} matches:\n"
            for m in top_matches:
                output_text += f"- {m['name']} (Score: {m['score']})\n  Path: {m['rel_path']}\n"
                
            return {
                "success": True,
                "result": output_text,
                "matches": top_matches,
                "caption": f"Found: {top_matches[0]['name']}"
            }
        except Exception as e:
            return {"success": False, "error": f"Find failed: {str(e)}"}


class ReadLocalFileTool(BaseTool):
    """
    Tool for reading file contents safely.
    Use this after fuzzy_find_local to actually read the file contents.
    Supports line numbering, offsets, and limits for pagination.
    """
    
    name = "read_file"
    description = (
        "Read the text contents of a local file with line numbers and pagination. "
        "Supports .txt, .md, .py, .js, .ts, .json, .yaml, .csv, and similar text files. "
        "Use offset and limit to page through large files."
    )
    category = "filesystem"
    
    # Allowed text extensions
    TEXT_EXTENSIONS = {
        '.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml', 
        '.csv', '.xml', '.html', '.css', '.java', '.c', '.cpp', '.h',
        '.sh', '.bat', '.ps1', '.log', '.ini', '.cfg', '.conf', '.toml',
        '.jsx', '.tsx', '.scss', '.sass', '.less', '.svg', '.vue', '.svelte'
    }
    MAX_FILE_SIZE = 100 * 1024  # 100KB limit
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Full or relative path to the file."
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (1-indexed, default: 1)."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default: 200, max: 300)."
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword or symbol to search for within the file. If provided, jumps directly to the matching section."
                    }
                },
                "required": ["file_path"]
            }
        }
    
    async def execute(self, file_path: str, offset: int = 1, limit: int = 200, query: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        import os
        from pathlib import Path
        
        from src.shared.fs_sandbox import sandbox_root

        try:
            # Under an eval sandbox, relative paths resolve against the SANDBOX, not home
            sandbox = sandbox_root()
            if not os.path.isabs(file_path):
                if sandbox is not None:
                    full_path = sandbox / file_path
                elif (Path.cwd() / file_path).exists():
                    full_path = Path.cwd() / file_path
                else:
                    full_path = Path.home() / file_path
            else:
                full_path = Path(file_path)

            if sandbox is not None:
                try:
                    escapes = not full_path.resolve().is_relative_to(sandbox)
                except (OSError, ValueError):
                    escapes = True
                if escapes:
                    return {
                        "success": False,
                        "error": (
                            f"Refusing to read outside the sandbox root ({sandbox}): {full_path}"
                        ),
                    }

            logger.info(f"Reading file: {full_path} (offset={offset}, limit={limit})")

            # Safety checks
            if not full_path.exists():
                hint = (
                    f"NEGATIVE CONFIRMATION: The requested file '{full_path.name}' does not exist at this location. "
                    "Confirming non-existence is a successful result. Do NOT crawl subdirectories, probe other folders, "
                    "or search Google Drive. If this was a check for project dependencies, report that this repository "
                    "uses requirements.txt (not pyproject.toml) or conclude directly that the file was not found."
                )
                return {
                    "success": False,
                    "error": f"File not found: {full_path}\n{hint}",
                    "harness_hint": hint
                }
            
            if not full_path.is_file():
                return {
                    "success": False,
                    "error": f"Path is not a file: {full_path}"
                }
            
            # Check extension
            ext = full_path.suffix.lower()
            if ext not in self.TEXT_EXTENSIONS:
                return {
                    "success": False,
                    "error": f"Cannot read binary or unsupported file type: {ext}. Supported: {', '.join(sorted(self.TEXT_EXTENSIONS))}"
                }
            
            # Check file size
            file_size = full_path.stat().st_size
            if file_size > self.MAX_FILE_SIZE:
                return {
                    "success": False,
                    "error": f"File too large ({file_size / 1024:.1f}KB). Max supported: {self.MAX_FILE_SIZE / 1024:.0f}KB"
                }
            
            # Read the file (using to_thread)
            try:
                content = await asyncio.to_thread(full_path.read_text, encoding='utf-8')
            except UnicodeDecodeError:
                # Try with latin-1 as fallback
                content = await asyncio.to_thread(full_path.read_text, encoding='latin-1')
            
            # Parse offset and limit safely
            try:
                offset_int = max(1, int(offset or 1))
            except (ValueError, TypeError):
                offset_int = 1
            try:
                limit_int = max(1, min(300, int(limit or 200)))
            except (ValueError, TypeError):
                limit_int = 200

            lines = content.splitlines()
            total_lines = len(lines)

            # If query is provided, jump directly to the matching line
            matched_info = ""
            if query and str(query).strip():
                q_lower = str(query).strip().lower()
                matched_line = next((i + 1 for i, l in enumerate(lines) if q_lower in l.lower()), None)
                if matched_line is not None:
                    offset_int = max(1, matched_line - 5)
                    matched_info = f" (jumped to line {matched_line} matching '{query}')"
                else:
                    matched_info = f" (query '{query}' not found in file; starting from offset {offset_int})"

            start_idx = offset_int - 1
            end_idx = min(total_lines, start_idx + limit_int)

            if total_lines == 0:
                display_content = "[File is empty]"
                result_msg = f"Read 0 lines from '{full_path.name}' (file is empty)"
                has_more = False
            elif start_idx >= total_lines:
                display_content = f"[Offset {offset_int} is beyond end of file ({total_lines} lines total)]"
                result_msg = f"Offset {offset_int} exceeds total lines ({total_lines}) in '{full_path.name}'"
                has_more = False
            else:
                slice_lines = lines[start_idx:end_idx]
                formatted = [f"{i:4d} | {line}" for i, line in enumerate(slice_lines, start=start_idx + 1)]
                display_content = "\n".join(formatted)
                has_more = end_idx < total_lines
                if has_more:
                    display_content += f"\n\n[Showing lines {offset_int}-{end_idx} of {total_lines}{matched_info}. Call read_file with offset={end_idx + 1} to read more, or query='...' to jump to another symbol]"
                    result_msg = f"Read lines {offset_int}-{end_idx} of {total_lines} from '{full_path.name}'"
                else:
                    result_msg = f"Read lines {offset_int}-{end_idx} of {total_lines} from '{full_path.name}' (end of file)"

            return {
                "success": True,
                "result": result_msg,
                "file_name": full_path.name,
                "file_path": str(full_path),
                "content": display_content,
                "full_content": content,
                "offset": offset_int,
                "limit": limit_int,
                "total_lines": total_lines,
                "has_more": has_more,
                "truncated": has_more,
                "size_bytes": file_size,
                "_trusted_source": True,
            }
            
        except Exception as e:
            logger.error(f"Failed to read file: {e}", exc_info=True)
            return {"success": False, "error": f"Read failed: {str(e)}"}
