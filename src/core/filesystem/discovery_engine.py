"""
Discovery Engine - Semantic Filesystem Intelligence
Builds a cognitive map of the user's filesystem for intelligent scouting and retrieval.
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from rapidfuzz import process, fuzz

from src.shared.logger import setup_logger

logger = setup_logger("DiscoveryEngine")

class DiscoveryEngine:
    """"
    Intelligent filesystem explorer that provides:
    1. Safe scouting (filtered directory trees)
    2. Smart fuzzy-finding (exact & partial query matching)
    3. Noise filtering (ignoring system/build folders)
    """

    # Folders to completely ignore during scans
    IGNORE_PATTERNS = {
        # System / Meta
        '.git', '.svn', '.hg', '.vscode', '.idea', '.DS_Store', 'Thumbs.db',
        'node_modules', 'venv', 'env', '__pycache__', 'site-packages',
        
        # Build Artifacts (Compiled junk)
        'dist', 'build', 'target', 'bin', 'obj', 'lib', 'debug', 'release',
        'cmake-build-debug', 'cmake-build-release',
        
        # Data Dumps
        'AppData', 'Program Files', 'Windows', 'System32'
    }

    # Files to ignore (binaries that look like noise)
    IGNORE_EXTENSIONS = {
        '.exe', '.dll', '.so', '.dylib', '.bin', '.obj', '.o', '.a', 
        '.pdb', '.class', '.pyc', '.pyo', '.pyd'
    }
    
    # Token Safety: Adaptive Limits
    SOFT_ITEM_LIMIT = 50       # Target limit for aesthetic/conciseness
    HARD_SAFE_LIMIT = 300      # Absolute max before truncation is forced
    MAX_ITEMS_PER_LEVEL = 50   # Legacy fallback (referenced elsewhere)

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DiscoveryEngine, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, root_path: str = None):
        """
        Initialize the discovery engine.

        Args:
            root_path: The absolute root boundary. Defaults to %USERPROFILE%.
        """
        if getattr(self, '_initialized', False):
            return

        if root_path:
            self._configured_root = Path(os.path.expandvars(root_path)).resolve()
        else:
            self._configured_root = Path(os.path.expandvars(r"%USERPROFILE%")).resolve()

        logger.info(f"Discovery Engine initialized. Root Safety: {self._configured_root}")
        self._initialized = True

    @property
    def root_path(self) -> Path:
        """The active root boundary — an eval sandbox if one is set, else the configured root.

        Resolved per call rather than stored, because this class is a singleton built at
        module import: a harness that sets its sandbox afterwards (coder_bench only knows
        its disposable worktree path once it has created it) would otherwise be ignored.
        That gap is exactly how a coder_bench turn ended up reading the live production
        repo instead of its own checkout (#566).
        """
        from src.shared.fs_sandbox import sandbox_root
        return sandbox_root() or self._configured_root

    def _is_safe_path(self, target_path: Path) -> bool:
        """
        CRITICAL SAFETY CHECK: Ensure path is within allowed root.
        Using .resolve() to catch '..' traversal attacks.
        """
        try:
            resolved = target_path.resolve()
            return resolved.is_relative_to(self.root_path)
        except Exception as e:
            logger.warning(f"Path safety check failed for {target_path}: {e}")
            return False

    def _is_ignored(self, name: str) -> bool:
        """Check if a folder or file name matches ignore patterns."""
        return name in self.IGNORE_PATTERNS or name.startswith('.')

    def scout(self, rel_path: str = "", depth: int = 1) -> Dict[str, Any]:
        """
        Returns a lightweight JSON tree of the directory.
        
        Args:
            rel_path: Path relative to ROOT. "" means scout the root.
            depth: How many levels deep to scan (recommend 1 or 2).
        
        Returns:
            Dict representing the folder structure.
        """
        # Coerce/fallback depth if None or invalid type
        if depth is None:
            depth = 1
        else:
            try:
                depth = int(depth)
            except (ValueError, TypeError):
                depth = 1

        # Resolve target
        if rel_path.strip() == "" or rel_path == ".":
            target = self.root_path
        else:
            target = (self.root_path / rel_path).resolve()

        # Safety Check
        if not self._is_safe_path(target):
            return {"error": "Access Denied: Path is outside allowed user directory."}
            
        if not target.exists() or not target.is_dir():
            return {"error": f"Path not found or not a directory: {rel_path}"}

        # Build Tree
        return self._scan_dir(target, current_depth=0, max_depth=depth)

    def _scan_dir(self, directory: Path, current_depth: int, max_depth: int) -> Dict[str, Any]:
        """Recursive directory scanner with truncation."""
        structure = {
            "name": directory.name,
            "type": "folder",
            "children": [],
            "status": "partial"  # Default to partial
        }
        
        if current_depth >= max_depth:
            structure["status"] = "depth_limit_reached"
            return structure

        try:
            # List all items
            all_items = sorted(list(directory.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
            
            visible_items = []
            hidden_count = 0
            
            for item in all_items:
                if self._is_ignored(item.name):
                    continue
                
                # Filter binary files by extension
                if item.is_file() and item.suffix.lower() in self.IGNORE_EXTENSIONS:
                    continue
                    
                visible_items.append(item)

            # --- ADAPTIVE TRUNCATION LOGIC (TOKEN SAFETY) ---
            # Smart Logic: If total items < HARD_SAFE_LIMIT, show EVERYTHING.
            # If total items > HARD_SAFE_LIMIT, clamp strictly to SOFT_ITEM_LIMIT to prevent massive context dumps.
            
            total_visible = len(visible_items)
            
            if total_visible <= self.HARD_SAFE_LIMIT:
                # Safe to show all
                display_items = visible_items
                limit_applied = False
            else:
                # Too many items! Force truncation to soft limit.
                display_items = visible_items[:self.SOFT_ITEM_LIMIT]
                limit_applied = True
                
            remaining_count = total_visible - len(display_items)

            children = []
            for item in display_items:
                if item.is_dir():
                    children.append(self._scan_dir(item, current_depth + 1, max_depth))
                else:
                    children.append({
                        "name": item.name,
                        "type": "file"
                    })
            
            structure["children"] = children
            structure["total_items"] = total_visible
            structure["item_count"] = total_visible  # Explicit field for LLM
            
            if remaining_count > 0:
                structure["truncation_warning"] = (
                    f"⚠️ TRUNCATION ACTIVE: Showing first {len(display_items)} of {total_visible} items.\n"
                    f"Reason: This folder exceeds the safety limit of {self.HARD_SAFE_LIMIT} items.\n"
                    "To protect valid context window usage, the full list is hidden. "
                    "Please use specific search queries or navigate to subfolders."
                )
            else:
                structure["truncation_warning"] = None

            structure["status"] = "scanned"
            return structure

        except PermissionError:
            return {"name": directory.name, "type": "folder", "error": "Permission Denied"}
        except Exception as e:
            return {"name": directory.name, "type": "folder", "error": str(e)}

    def find(self, query: str, search_root: str = "Documents") -> List[Dict[str, Any]]:
        """
        Smart fuzzy-finder that drills down into a specific folder.
        
        Args:
            query: The filename or keywords to look for.
            search_root: The relative path to start searching (e.g., "Documents/Projects").
        """
        start_dir = (self.root_path / search_root).resolve()
        
        if not self._is_safe_path(start_dir):
            return [{"error": "Search root is outside user directory."}]
            
        if not start_dir.exists():
            # Graceful failure: return empty matches instead of hard error
            logger.warning(f"Search root does not exist: {start_dir}")
            return []

        logger.info(f"Fuzzy Finding '{query}' in {start_dir}")
        
        candidates = []
        
        # Walk the tree (MAX DEPTH 5 to prevent infinite loops)
        # We manually walk to enforce IGNORE patterns
        for root, dirs, files in os.walk(start_dir):
            # Filtering in-place to prevent walking ignored dirs
            dirs[:] = [d for d in dirs if not self._is_ignored(d)]
            
            # Check Files
            for file in files:
                if self._is_ignored(file): continue
                if Path(file).suffix.lower() in self.IGNORE_EXTENSIONS: continue
                
                full_path = Path(root) / file
                
                # Scoring
                # Normalize 1: treat underscores as spaces to handle STT inputs
                norm_query = query.lower().replace('_', ' ')
                norm_file = file.lower().replace('_', ' ')
                
                # Normalize 2: Remove all punctuation/spaces for "Ultra" matching
                ultra_norm_query = "".join(c for c in norm_query if c.isalnum())
                ultra_norm_file = "".join(c for c in norm_file if c.isalnum())
                
                # 1. Exact or Subset Match (Priority)
                if norm_query == norm_file or ultra_norm_query == ultra_norm_file:
                    score = 110 # Super perfect match
                elif norm_query in norm_file or ultra_norm_query in ultra_norm_file:
                    # Query is actually inside the filename (e.g. "tips" in "teaching_tips.md")
                    score = 95
                else:
                    # 2. Advanced Fuzzy Matching
                    # Using WRatio which is more robust than partial_ratio for this use case
                    score = fuzz.WRatio(norm_query, norm_file)
                    
                    # Penalty for length mismatch if the file is MUCH shorter than the query
                    # prevents "wood.mp4" matching a file simply called "mp"
                    if len(norm_file) < len(norm_query) * 0.5:
                        score *= 0.6 
                
                if score > 70: # Increased threshold for better precision
                    candidates.append({
                        "name": file,
                        "path": str(full_path),
                        "rel_path": str(full_path.relative_to(self.root_path)),
                        "score": score
                    })

        # Sort by best match
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # Return Top 5
        return candidates[:5]
