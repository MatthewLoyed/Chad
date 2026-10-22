"""
Config Repository

Data access layer for configuration persistence.
Abstracts YAML file operations for configuration management.
"""

import logging
import yaml
from typing import Dict, Any, Optional, List
from pathlib import Path
from copy import deepcopy

logger = logging.getLogger("ConfigRepository")


class ConfigRepository:
    """
    Repository for configuration persistence operations.

    Provides a clean data access interface that abstracts
    YAML file operations for configuration management.
    """

    def __init__(self, config_dir: str = "config"):
        """
        Initialize ConfigRepository.

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = Path(config_dir)
        self._ensure_config_directory()
        self._config_cache: Dict[str, Dict[str, Any]] = {}
        logger.info(f"ConfigRepository initialized: {config_dir}")

    def _ensure_config_directory(self) -> None:
        """Ensure the config directory exists"""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _get_config_path(self, config_name: str) -> Path:
        """Get full path for a config file"""
        if not config_name.endswith('.yaml'):
            config_name = f"{config_name}.yaml"
        return self.config_dir / config_name

    # ========================================================================
    # Config CRUD Operations
    # ========================================================================

    def load(self, config_name: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        Load configuration from YAML file.

        Args:
            config_name: Name of config file (without .yaml extension)
            use_cache: Use cached config if available

        Returns:
            Configuration dictionary
        """
        # Check cache first
        if use_cache and config_name in self._config_cache:
            logger.debug(f"Loading {config_name} from cache")
            return deepcopy(self._config_cache[config_name])

        config_path = self._get_config_path(config_name)

        try:
            if not config_path.exists():
                logger.warning(f"Config file not found: {config_path}")
                return {}

            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

            # Cache the config
            self._config_cache[config_name] = deepcopy(config)

            logger.debug(f"Loaded config: {config_name}")
            return config

        except Exception as e:
            logger.error(f"Failed to load config {config_name}: {e}", exc_info=True)
            return {}

    def save(
        self,
        config_name: str,
        config: Dict[str, Any],
        update_cache: bool = True
    ) -> bool:
        """
        Save configuration to YAML file.

        Args:
            config_name: Name of config file (without .yaml extension)
            config: Configuration dictionary
            update_cache: Update cache after saving

        Returns:
            True if successful
        """
        config_path = self._get_config_path(config_name)

        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

            if update_cache:
                self._config_cache[config_name] = deepcopy(config)

            logger.info(f"Saved config: {config_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to save config {config_name}: {e}", exc_info=True)
            return False

    def get_value(
        self,
        config_name: str,
        key_path: str,
        default: Any = None
    ) -> Any:
        """
        Get a specific value from config using dot notation.

        Args:
            config_name: Name of config file
            key_path: Dot-separated key path (e.g., 'audio.sample_rate')
            default: Default value if key not found

        Returns:
            Value at key path or default
        """
        config = self.load(config_name)

        keys = key_path.split('.')
        value = config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def set_value(
        self,
        config_name: str,
        key_path: str,
        value: Any
    ) -> bool:
        """
        Set a specific value in config using dot notation.

        Args:
            config_name: Name of config file
            key_path: Dot-separated key path (e.g., 'audio.sample_rate')
            value: Value to set

        Returns:
            True if successful
        """
        config = self.load(config_name)

        keys = key_path.split('.')
        current = config

        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the value
        current[keys[-1]] = value

        return self.save(config_name, config)

    def update(
        self,
        config_name: str,
        updates: Dict[str, Any],
        merge: bool = True
    ) -> bool:
        """
        Update configuration with new values.

        Args:
            config_name: Name of config file
            updates: Dictionary of updates
            merge: If True, merge with existing config; if False, replace

        Returns:
            True if successful
        """
        if merge:
            config = self.load(config_name)
            config.update(updates)
        else:
            config = updates

        return self.save(config_name, config)

    def delete(self, config_name: str) -> bool:
        """
        Delete a configuration file.

        Args:
            config_name: Name of config file

        Returns:
            True if successful
        """
        config_path = self._get_config_path(config_name)

        try:
            if config_path.exists():
                config_path.unlink()

                # Remove from cache
                if config_name in self._config_cache:
                    del self._config_cache[config_name]

                logger.info(f"Deleted config: {config_name}")
                return True

            logger.warning(f"Config file not found: {config_path}")
            return False

        except Exception as e:
            logger.error(f"Failed to delete config {config_name}: {e}", exc_info=True)
            return False

    def exists(self, config_name: str) -> bool:
        """
        Check if a configuration file exists.

        Args:
            config_name: Name of config file

        Returns:
            True if exists
        """
        config_path = self._get_config_path(config_name)
        return config_path.exists()

    def list_configs(self) -> List[str]:
        """
        List all configuration files.

        Returns:
            List of config file names (without .yaml extension)
        """
        try:
            yaml_files = list(self.config_dir.glob('*.yaml'))
            return [f.stem for f in yaml_files]

        except Exception as e:
            logger.error(f"Failed to list configs: {e}")
            return []

    # ========================================================================
    # Cache Management
    # ========================================================================

    def clear_cache(self, config_name: Optional[str] = None) -> None:
        """
        Clear configuration cache.

        Args:
            config_name: Specific config to clear, or None to clear all
        """
        if config_name:
            if config_name in self._config_cache:
                del self._config_cache[config_name]
                logger.debug(f"Cleared cache for: {config_name}")
        else:
            self._config_cache.clear()
            logger.debug("Cleared all config cache")

    def reload(self, config_name: str) -> Dict[str, Any]:
        """
        Reload configuration from disk (bypass cache).

        Args:
            config_name: Name of config file

        Returns:
            Fresh configuration dictionary
        """
        # Clear cache entry
        if config_name in self._config_cache:
            del self._config_cache[config_name]

        # Load fresh from disk
        return self.load(config_name, use_cache=False)

    # ========================================================================
    # Profile Management (Special Config Type)
    # ========================================================================

    @staticmethod
    def _recursive_update(base: Dict[str, Any], update: Dict[str, Any]) -> None:
        for key, value in update.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                ConfigRepository._recursive_update(base[key], value)
            else:
                base[key] = value

    def _resolve_profile(
        self,
        profile_name: str,
        profiles_doc: Optional[Dict[str, Any]] = None,
        preferences: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        profiles_doc = profiles_doc if profiles_doc is not None else self.load("profiles")
        preferences = preferences if preferences is not None else self.load("preferences", use_cache=False)
        base = deepcopy(profiles_doc.get("profiles", {}).get(profile_name, {}))
        active_id = profiles_doc.get("active_profile", "default")
        if profile_name == active_id:
            overlay = preferences.get("profile")
            if isinstance(overlay, dict) and overlay:
                self._recursive_update(base, overlay)
        return base

    def get_profile(self, profile_name: str) -> Dict[str, Any]:
        """
        Get a user profile configuration.

        The active profile merges ``preferences.profile`` (gitignored overlay)
        on top of the tracked ``profiles.yaml`` entry.

        Args:
            profile_name: Profile name

        Returns:
            Profile configuration
        """
        return self._resolve_profile(profile_name)

    def save_profile(
        self,
        profile_name: str,
        profile_config: Dict[str, Any]
    ) -> bool:
        """
        Save a user profile configuration.

        Personal overrides for the *active* profile are persisted to
        ``preferences.profile`` (gitignored). Other profiles still write to
        ``profiles.yaml`` for multi-profile setups.

        Args:
            profile_name: Profile name
            profile_config: Profile configuration

        Returns:
            True if successful
        """
        profiles = self.load("profiles", use_cache=False)
        active_id = profiles.get("active_profile", "default")

        if profile_name == active_id:
            preferences = self.load("preferences", use_cache=False)
            overlay = preferences.get("profile")
            if not isinstance(overlay, dict):
                overlay = {}
            self._recursive_update(overlay, profile_config)
            preferences["profile"] = overlay
            return self.save("preferences", preferences, update_cache=True)

        if "profiles" not in profiles:
            profiles["profiles"] = {}

        profiles["profiles"][profile_name] = profile_config

        return self.save("profiles", profiles)

    def list_profiles(self) -> List[str]:
        """
        List all available profiles.

        Returns:
            List of profile names
        """
        profiles = self.load('profiles')
        return list(profiles.get('profiles', {}).keys())

    def delete_profile(self, profile_name: str) -> bool:
        """
        Delete a user profile.

        Args:
            profile_name: Profile name

        Returns:
            True if successful
        """
        profiles = self.load('profiles')

        if 'profiles' in profiles and profile_name in profiles['profiles']:
            del profiles['profiles'][profile_name]
            return self.save('profiles', profiles)

        return False

    # ========================================================================
    # Preferences Management
    # ========================================================================

    def update_preference(self, key_path: str, value: Any) -> bool:
        """
        Update a user preference in preferences.yaml.

        This is a convenience method that mirrors the old ConfigManager.save_config()
        behavior. It loads preferences, updates the value, and saves it back.

        Args:
            key_path: Dot-separated key path (e.g., 'audio.volume')
            value: Value to set

        Returns:
            True if successful
        """
        preferences = self.load('preferences', use_cache=False)

        keys = key_path.split('.')
        current = preferences

        # Navigate to parent key
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]

        # Set value
        current[keys[-1]] = value

        # Save back to preferences.yaml
        success = self.save('preferences', preferences, update_cache=True)

        if success:
            logger.info(f"Updated preference: {key_path} = {value}")

        return success

    def get_preference(self, key_path: str, default: Any = None) -> Any:
        """
        Get a preference value from preferences.yaml.

        Args:
            key_path: Dot-separated key path (e.g., 'audio.volume')
            default: Default value if not found

        Returns:
            Preference value or default
        """
        return self.get_value('preferences', key_path, default)

    # ========================================================================
    # Secrets Management
    # ========================================================================

    def update_secret(self, key_path: str, value: str) -> bool:
        """
        Update a secret in secrets.yaml.

        Args:
            key_path: Dot-separated key path (e.g., 'api_keys.gemini')
            value: Secret value

        Returns:
            True if successful
        """
        secrets = self.load('secrets', use_cache=False)

        keys = key_path.split('.')
        current = secrets

        # Navigate to parent key
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]

        # Set value
        current[keys[-1]] = value

        # Save back to secrets.yaml
        success = self.save('secrets', secrets, update_cache=True)

        if success:
            logger.info(f"Updated secret: {key_path}")

        return success

    def get_secret(self, key_path: str, default: Any = None) -> Any:
        """
        Get a secret value from secrets.yaml.

        Args:
            key_path: Dot-separated key path (e.g., 'api_keys.gemini')
            default: Default value if not found

        Returns:
            Secret value or default
        """
        return self.get_value('secrets', key_path, default)

    # ========================================================================
    # Validation
    # ========================================================================

    def validate(self, config_name: str, schema: Dict[str, Any]) -> bool:
        """
        Validate configuration against a schema.

        Args:
            config_name: Name of config file
            schema: Validation schema

        Returns:
            True if valid

        Note: This is a placeholder for future validation logic
        """
        # TODO: Implement schema validation
        # Could use jsonschema or similar
        logger.warning("Config validation not yet implemented")
        return True
