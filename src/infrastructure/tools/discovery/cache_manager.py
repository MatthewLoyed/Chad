import json
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from src.shared.logger import setup_logger

logger = setup_logger("ToolCacheManager")

class ToolCacheManager:
    """
    Manages caching of tool schemas and metadata to accelerate startup.
    """
    
    def __init__(self, cache_file: str = "data/tool_cache.json"):
        self.cache_file = Path(cache_file)
        self._cache_data: Dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self):
        """Load cache from disk if it exists."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    self._cache_data = json.load(f)
                logger.debug(f"[ CACHE ] Loaded {len(self._cache_data.get('tools', {}))} tools from cache.")
            except Exception as e:
                logger.warning(f"[ CACHE ] Failed to load cache: {e}")
                self._cache_data = {}

    def save_cache(self, tools_data: Dict[str, Any]):
        """
        Save tool metadata to cache.
        :param tools_data: Dict containing tool metadata organized by name.
        """
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Record timestamps of tool directories to detect staleness
            timestamps = {
                "src/tools": self._get_dir_mtime("src/tools"),
                "src/skills": self._get_dir_mtime("src/skills")
            }
            
            payload = {
                "version": "1.0",
                "timestamp": time.time(),
                "dir_timestamps": timestamps,
                "tools": tools_data
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(payload, f, indent=2)
            
            logger.info(f"[ CACHE ] Saved {len(tools_data)} tools to cache.")
        except Exception as e:
            logger.error(f"[ CACHE ] Failed to save cache: {e}")

    def is_cache_valid(self) -> bool:
        """Check if the cache exists and is not stale."""
        if not self._cache_data or "tools" not in self._cache_data:
            return False
            
        cached_timestamps = self._cache_data.get("dir_timestamps", {})
        
        # Check if directories have changed
        for dir_path, cached_mtime in cached_timestamps.items():
            current_mtime = self._get_dir_mtime(dir_path)
            if current_mtime > cached_mtime:
                logger.info(f"[ CACHE ] Stale cache detected: {dir_path} has changed.")
                return False
                
        return True

    def get_cached_tools(self) -> Dict[str, Any]:
        """Return the cached tool metadata."""
        return self._cache_data.get("tools", {})

    def _get_dir_mtime(self, path: str) -> float:
        """Get the latest modification time of a directory and its files."""
        abs_path = Path(path).resolve()
        if not abs_path.exists():
            return 0.0
            
        latest_mtime = abs_path.stat().st_mtime
        
        # Check files recursively (shallow check for speed, only 1 level deep is usually enough for tools)
        try:
            for p in abs_path.rglob("*.py"):
                if p.name.startswith("_"): continue
                mtime = p.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
        except Exception:
            pass
            
        return latest_mtime
