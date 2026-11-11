"""
Discovery Tools - Agent Interface for Semantic Filesystem
wraps the DiscoveryEngine into standard LLM tools.
"""

from typing import Dict, Any, Optional
import asyncio
from src.core.tools import failure, limits
from src.tools.base_tool import BaseTool
from src.tools.filesystem._file_page import file_page
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
            search_errors = []
            for root in roots:
                matches = await asyncio.to_thread(_engine.find, query, root)
                for match in matches:
                    if "error" in match:
                        search_errors.append(match["error"])
                    else:
                        all_matches.append(match)

            if not all_matches:
                if search_errors:
                    return failure.failure(search_errors[0], tool_name=self.name)
                scope = "the sandbox root" if roots == [""] else ", ".join(roots)
                return failure.failure(
                    f"Fuzzy filename search returned no matches for '{query}' in {scope}.",
                    failure_class=failure.NOT_FOUND,
                    tool_name=self.name,
                    next_actions=[
                        "Report that the filename search found no matches in its scope.",
                        "Search another folder only if the user or earlier results point to it.",
                    ],
                )
            
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
    Agent Tool: Read the text content of a local file.
    Use this after fuzzy_find_local to actually read the file contents.
    """
    
    name = "read_file"
    description = (
        "Read a numbered page of a local text file. Use query to jump to a symbol or literal text. "
        "To continue a page, call again with next_offset and next_column as offset and column, "
        "omitting query. Supports .txt, .md, .py, .json, .yaml, .csv, and similar text files."
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
                        "description": "Absolute or home-relative path from fuzzy_find_local results."
                    },
                    "offset": {"type": "integer", "description": "First line to read, numbered from 1."},
                    "limit": {"type": "integer", "description": "Maximum lines to read on this page."},
                    "query": {"type": "string", "description": "Optional case-insensitive literal to find at or after offset."},
                    "column": {"type": "integer", "description": "First character on the starting line, numbered from 1. Use next_column for continuation."},
                },
                "required": ["file_path"]
            }
        }
    
    async def execute(
        self,
        file_path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        query: Optional[str] = None,
        column: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        import os
        from pathlib import Path
        
        from src.shared.fs_sandbox import sandbox_root

        for field, value in (("offset", offset), ("limit", limit), ("column", column)):
            if value is not None and (type(value) is not int or value < 1):
                return failure.failure(
                    f"{field} must be a positive integer",
                    failure_class=failure.BAD_INPUT,
                    tool_name=self.name,
                )
        if query is not None and not isinstance(query, str):
            return failure.failure(
                "query must be text", failure_class=failure.BAD_INPUT, tool_name=self.name,
            )
        maximum = max(1, int(limits.get("read_file_max_lines")))
        default = max(1, int(limits.get("read_file_default_lines")))

        try:
            # Under an eval sandbox, relative paths resolve against the SANDBOX, not home
            # (#566). This is not cosmetic: `fuzzy_find_local` returns paths relative to
            # whatever root the discovery engine is using, so resolving them against home
            # both breaks sandboxed reads and — when a same-named file exists there — reads
            # the real user's copy instead of the one the bench just found.
            sandbox = sandbox_root()
            base = sandbox or Path.home()
            if not os.path.isabs(file_path):
                full_path = base / file_path
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

            logger.info(f"Reading file: {full_path}")

            # Safety checks
            if not full_path.exists():
                return {
                    "success": False,
                    "error": f"File not found: {full_path}"
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
            
            return file_page(
                content,
                file_path=full_path,
                size_bytes=file_size,
                offset=offset or 1,
                limit=min(limit or default, maximum),
                query=(query or "").strip(),
                column=column or 1,
            )

        except Exception as e:
            logger.error(f"Failed to read file: {e}", exc_info=True)
            return {"success": False, "error": f"Read failed: {str(e)}"}
