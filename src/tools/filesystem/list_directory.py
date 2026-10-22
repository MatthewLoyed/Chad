"""
List Directory - Highly optimized tree-like directory scanner for token efficiency.
"""

import fnmatch
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from rapidfuzz import process, fuzz

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("ListDirectoryTool")


class GitignoreMatcher:
    """Lightweight .gitignore parser using glob patterns."""
    def __init__(self, root_dir: Path):
        self.rules = []
        self.root_dir = root_dir.resolve()
        gitignore_path = self.root_dir / ".gitignore"
        if gitignore_path.exists():
            try:
                for line in gitignore_path.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    negate = False
                    if line.startswith('!'):
                        negate = True
                        line = line[1:]
                    self.rules.append((line, negate))
            except Exception as e:
                logger.warning(f"Failed to parse .gitignore: {e}")

    def is_ignored(self, path: Path) -> bool:
        try:
            resolved_path = path.resolve()
            rel_path = resolved_path.relative_to(self.root_dir).as_posix()
        except ValueError:
            return False

        ignored = False
        for rule, negate in self.rules:
            is_dir_rule = rule.endswith('/')
            clean_rule = rule.rstrip('/')
            
            # Match against full relative path, pattern starting with */, or individual path parts
            match = (
                fnmatch.fnmatch(rel_path, clean_rule) or
                fnmatch.fnmatch(rel_path, f"*/{clean_rule}") or
                fnmatch.fnmatch(rel_path, f"{clean_rule}/*") or
                any(fnmatch.fnmatch(part, clean_rule) for part in Path(rel_path).parts)
            )
            
            if match:
                if is_dir_rule:
                    if resolved_path.is_dir():
                        ignored = not negate
                else:
                    ignored = not negate
        return ignored


class ListDirectoryTool(BaseTool):
    """
    Explore directory contents. Returns a lightweight visual tree.
    Optimized for token efficiency, size awareness, and typo tolerance.
    """

    name = "list_directory"
    description = (
        "List directory contents in a tree-like visual format. "
        "Outputs file sizes and immediate folder item counts to let you plan "
        "which files to read without wasting context window space."
    )
    category = "filesystem"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to list (relative to workspace or absolute). Use '.' for current directory."
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How deep to traverse directory tree. Default is 1 (shallow list). Maximum recommended is 2."
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": "Whether to list hidden files/folders (starting with '.'). Default is false."
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g. '*.py' or '*config*')."
                    },
                    "respect_gitignore": {
                        "type": "boolean",
                        "description": "Whether to respect .gitignore rules. Default is true."
                    }
                },
                "required": ["path"]
            }
        }

    def _is_safe_path(self, target_path: Path) -> bool:
        try:
            resolved = target_path.resolve()
            workspace = Path.cwd().resolve()
            user_profile = Path.home().resolve()
            return resolved.is_relative_to(workspace) or resolved.is_relative_to(user_profile)
        except Exception:
            return False

    def _find_fuzzy_path_suggestion(self, path: Path) -> str:
        parts = path.parts
        if not parts:
            return ""

        existing_parent = None
        missing_part_idx = -1
        for i in range(len(parts), 0, -1):
            check_path = Path(*parts[:i])
            if check_path.exists() and check_path.is_dir():
                existing_parent = check_path
                missing_part_idx = i
                break

        if existing_parent is None:
            existing_parent = Path.cwd()
            missing_part_idx = 0

        if missing_part_idx >= len(parts) or missing_part_idx < 0:
            return ""

        target_part = parts[missing_part_idx]

        try:
            options = [item.name for item in existing_parent.iterdir()]
        except Exception:
            return ""

        if not options:
            return ""

        best_match = process.extractOne(target_part, options, scorer=fuzz.WRatio)
        if best_match and best_match[1] > 70:
            suggested_part = best_match[0]
            suggested_parts = list(parts[:missing_part_idx]) + [suggested_part] + list(parts[missing_part_idx + 1:])
            suggested_path = Path(*suggested_parts)
            return suggested_path.as_posix()

        return ""

    async def execute(
        self,
        path: str,
        depth: int = 1,
        include_hidden: bool = False,
        pattern: Optional[str] = None,
        respect_gitignore: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        # Coerce/fallback depth if None or invalid type
        if depth is None:
            depth = 1
        else:
            try:
                depth = int(depth)
            except (ValueError, TypeError):
                depth = 1

        # Resolve target path
        target_path = Path(path).resolve()

        if not self._is_safe_path(target_path):
            return {
                "success": False,
                "error": f"Access Denied: Path '{path}' is outside allowed user directory."
            }

        if not target_path.exists():
            suggestion = self._find_fuzzy_path_suggestion(target_path)
            error_msg = f"Error: path '{path}' does not exist."
            if suggestion:
                error_msg += f"\nDid you mean '{suggestion}'? (found via fuzzy match)"
            return {"success": False, "error": error_msg}

        if not target_path.is_dir():
            return {
                "success": False,
                "error": f"Error: '{path}' is a file, not a directory. Use read_file instead."
            }

        # Initialize Gitignore Matcher
        workspace_root = Path.cwd().resolve()
        gitignore_matcher = GitignoreMatcher(workspace_root) if respect_gitignore else None

        lines = [f"📁 {target_path.name}/"]
        item_count_ref = [1]  # Start with root folder line

        self._walk(
            current_dir=target_path,
            root_dir=target_path,
            current_depth=1,
            max_depth=depth,
            lines=lines,
            include_hidden=include_hidden,
            pattern=pattern,
            respect_gitignore=respect_gitignore,
            gitignore_matcher=gitignore_matcher,
            prefix="",
            item_count_ref=item_count_ref
        )

        return {
            "success": True,
            "result": "\n".join(lines),
            "caption": f"Listed directory: {target_path.name}"
        }

    def _walk(
        self,
        current_dir: Path,
        root_dir: Path,
        current_depth: int,
        max_depth: int,
        lines: List[str],
        include_hidden: bool,
        pattern: Optional[str],
        respect_gitignore: bool,
        gitignore_matcher: Optional[GitignoreMatcher],
        prefix: str = "",
        item_count_ref: List[int] = None
    ):
        if current_depth > max_depth:
            return

        if item_count_ref is None:
            item_count_ref = [0]

        if item_count_ref[0] >= 200:
            return

        try:
            all_items = sorted(list(current_dir.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}└── [Permission Denied]")
            item_count_ref[0] += 1
            return
        except Exception as e:
            lines.append(f"{prefix}└── [Error: {str(e)}]")
            item_count_ref[0] += 1
            return

        # Hidden file filtering
        if not include_hidden:
            all_items = [item for item in all_items if not item.name.startswith('.')]

        # Gitignore filtering
        if respect_gitignore and gitignore_matcher:
            all_items = [item for item in all_items if not gitignore_matcher.is_ignored(item)]

        folders = [item for item in all_items if item.is_dir()]
        files = [item for item in all_items if item.is_file()]

        # Pattern matching (applies to files)
        if pattern:
            files = [file for file in files if fnmatch.fnmatch(file.name, pattern)]

        visible_items = folders + files
        total_visible = len(visible_items)

        # Truncation per level
        max_level_items = 50
        display_items = visible_items[:max_level_items]
        truncated_count = total_visible - len(display_items)

        for i, item in enumerate(display_items):
            if item_count_ref[0] >= 200:
                lines.append(f"{prefix}└── ... [Truncated: Total output cap of 200 items reached]")
                item_count_ref[0] += 1
                return

            is_last = (i == len(display_items) - 1) and (truncated_count == 0)
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if item.is_dir():
                # Count immediate child items for metadata display
                try:
                    child_list = list(item.iterdir())
                    if not include_hidden:
                        child_list = [c for c in child_list if not c.name.startswith('.')]
                    if respect_gitignore and gitignore_matcher:
                        child_list = [c for c in child_list if not gitignore_matcher.is_ignored(c)]
                    child_count = len(child_list)
                except Exception:
                    child_count = 0

                lines.append(f"{prefix}{connector}📁 {item.name}/  ({child_count} items)")
                item_count_ref[0] += 1

                self._walk(
                    current_dir=item,
                    root_dir=root_dir,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    lines=lines,
                    include_hidden=include_hidden,
                    pattern=pattern,
                    respect_gitignore=respect_gitignore,
                    gitignore_matcher=gitignore_matcher,
                    prefix=child_prefix,
                    item_count_ref=item_count_ref
                )
            else:
                try:
                    size = item.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                except Exception:
                    size_str = "unknown size"

                lines.append(f"{prefix}{connector}📄 {item.name}  ({size_str})")
                item_count_ref[0] += 1

        if truncated_count > 0:
            connector = "└── "
            lines.append(f"{prefix}{connector}[Truncated: {truncated_count} more items. Use depth=2 or pattern= to filter]")
            item_count_ref[0] += 1
