import logging
import json
import os
from typing import Dict, Any, Optional
from src.shared.logger import setup_logger

logger = setup_logger("ToolConfigManager")

class ToolConfigManager:
    """
    Manages tool-specific configurations, preferences, and 'remembered' parameters.
    Stored locally for now, could be synced to Supabase.
    """
    
    _config_path = os.path.join(os.getcwd(), "data", "tool_configs.json")
    _configs: Dict[str, str] = {}
    _loaded = False

    @classmethod
    def _ensure_loaded(cls):
        if cls._loaded:
            return
        
        if os.path.exists(cls._config_path):
            try:
                with open(cls._config_path, "r") as f:
                    cls._configs = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load tool configs: {e}")
        
        cls._loaded = True

    @classmethod
    def get_config_for_tool(cls, tool_name: str) -> Optional[str]:
        """Get the saved context/config for a tool."""
        cls._ensure_loaded()
        return cls._configs.get(tool_name)

    @classmethod
    def save_config_for_tool(cls, tool_name: str, context: str):
        """Save a new preference or context for a tool."""
        cls._ensure_loaded()
        cls._configs[tool_name] = context
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(cls._config_path), exist_ok=True)
        
        try:
            with open(cls._config_path, "w") as f:
                json.dump(cls._configs, f, indent=4)
            logger.info(f"[ SUCCESS ] Saved config for tool '{tool_name}'")
        except Exception as e:
            logger.error(f"Failed to save tool config: {e}")

    @classmethod
    def list_configs(cls) -> Dict[str, str]:
        cls._ensure_loaded()
        return cls._configs
