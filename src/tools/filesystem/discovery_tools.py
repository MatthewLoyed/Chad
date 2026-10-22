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
                return {
                    "success": False, 
                    "error": f"No matches found for '{query}' in {roots}. Try scouting to find the right folder."
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
    Agent Tool: Read the text content of a local file.
    Use this after fuzzy_find_local to actually read the file contents.
    """
    
    name = "read_file"
    description = (
        "Read the text contents of a local file. Use fuzzy_find_local first to find the file path, "
        "then use this tool to read its contents. Supports .txt, .md, .py, .json, .yaml, .csv, and similar text files."
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
                        "description": "Full or relative path to the file (from fuzzy_find_local results)"
                    }
                },
                "required": ["file_path"]
            }
        }
    
    async def execute(self, file_path: str, **kwargs) -> Dict[str, Any]:
        import os
        from pathlib import Path
        
        from src.shared.fs_sandbox import sandbox_root

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
            
            # Truncate if very long for display
            display_content = content
            truncated = False
            if len(content) > 5000:
                display_content = content[:5000]
                truncated = True
            
            result_msg = f"Read {len(content)} characters from '{full_path.name}'"
            if truncated:
                result_msg += f" (showing first 5000 chars)"
            
            return {
                "success": True,
                "result": result_msg,
                "file_name": full_path.name,
                "file_path": str(full_path),
                "content": display_content,
                "full_content": content,
                "truncated": truncated,
                "size_bytes": file_size,
                # Provenance marker (#642): a local file the user/agent asked for by
                # path is a trusted source — sanitize_tool_output must not mangle
                # legitimate discussion of "system prompt"/"instructions" in, e.g.,
                # this repo's own persona prompts or eval transcripts.
                "_trusted_source": True,
            }
            
        except Exception as e:
            logger.error(f"Failed to read file: {e}", exc_info=True)
            return {"success": False, "error": f"Read failed: {str(e)}"}
