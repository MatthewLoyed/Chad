import copy
import threading
import yaml
import os
from pathlib import Path
import logging
from typing import Any, Dict, Optional, List

# Load .env file into environment variables on module import
try:
    from dotenv import load_dotenv
    # Save launcher-explicit environment variables to prevent override=True from clobbering them
    _preserved = {
        k: os.environ[k]
        for k in [
            "CHAD_SERVER_URL",
            "CHAD_UI_DEV",
            "CHAD_DEV_WORKTREE",
            "CHAD_MAIN_ROOT",
            "CHAD_LOCAL_DB_PATH",
            "CHAD_MODE",
            "CHAD_DAEMONS_ENABLED",
            "PORT",
            "CHAD_LOG_LEVEL",
            "CHAD_HOME"
        ]
        if k in os.environ
    }
    # CHAD_HOME wins when set (frozen Chad.exe points back at the real repo);
    # otherwise derive the repo root from this file's location.
    _home = os.environ.get("CHAD_HOME")
    _env_path = (Path(_home) if _home else Path(__file__).resolve().parents[2]) / ".env"
    if _env_path.exists():
        load_dotenv(dotenv_path=str(_env_path), override=True)
    # A missing project .env is a valid clean install. Do not search parent
    # directories or the shared interpreter's checkout for unrelated credentials.
    # Restore preserved environment variables
    for k, v in _preserved.items():
        os.environ[k] = v
except ImportError:
    pass  # dotenv not installed, rely on system env vars

logger = logging.getLogger("ConfigManager")

class ConfigManager:
    """
    Centralized configuration management.
    Handles loading, access, and persistence of config.yaml.
    """

    # Profile sections merged from profiles.yaml (and preferences.profile overlay)
    # into the runtime config dict.
    _PROFILE_SECTIONS = (
        "apps", "routines", "persona", "file_opener", "file_writer", "workspace",
    )
    
    _config: Dict[str, Any] = {}
    _prefs: Dict[str, Any] = {}
    _config_path: Path = Path("config/config.yaml")
    _prefs_path: Path = Path("config/preferences.yaml")

    # Tracks whether load_config() has run at least once. Distinct from
    # truthiness of _config/_profiles because both are legitimately {} on a
    # fresh install with no config.yaml/profiles.yaml — using `not cls._config`
    # as the "needs load" check there would re-trigger load_config() forever
    # (get_profile() -> load_config() -> _merge_active_profile_into_config()
    # -> get_profile() -> ... RecursionError). See #216.
    _loaded: bool = False

    # Guards load_config() end-to-end so concurrent callers (e.g. the desktop
    # command relay, which handles each api.xxx() call in its own request
    # task) never observe cls._profiles/_config mid-reset. Reentrant because
    # load_config() -> _merge_active_profile_into_config() -> get_profile()
    # can recurse back into load_config() on the same thread. See #437.
    _lock: threading.RLock = threading.RLock()

    @classmethod
    def get_config(cls) -> Dict[str, Any]:
        """Return the current in-memory configuration."""
        if not cls._loaded:
            cls.load_config()
        return cls._config
    
    @classmethod
    def load_config(cls, path: str = "config/config.yaml") -> Dict[str, Any]:
        """
        Load configuration from disk.
        Loads base config.yaml and overlays preferences.yaml.
        """
        with cls._lock:
            cls._config_path = Path(path)

            # 1. Load Base Config (Read-Onlyish)
            base_config = {}
            if cls._config_path.exists():
                try:
                    with open(cls._config_path, 'r', encoding='utf-8') as f:
                        base_config = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.error(f"Failed to load base config: {e}")
            else:
                logger.warning(f"Config file {path} not found. Using empty base config.")

            # 2. Load User Preferences (Read/Write)
            prefs = {}
            if cls._prefs_path.exists():
                try:
                    with open(cls._prefs_path, 'r', encoding='utf-8') as f:
                        prefs = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.error(f"Failed to load preferences: {e}")

            # 3. Load Secrets (Read-Only)
            # CHAD_SECRETS_PATH lets an eval read the real secrets from OUTSIDE its working dir —
            # e.g. the self-improvement loop (#358) runs a behavioral eval from a coder's clone
            # whose code/prompts are the thing under test, but whose secrets are deliberately kept
            # out of the coder-mounted workspace. Defaults to the in-tree config/secrets.yaml.
            cls._secrets_path = Path(os.environ.get("CHAD_SECRETS_PATH", "config/secrets.yaml"))
            secrets_config = {}
            if cls._secrets_path.exists():
                try:
                    with open(cls._secrets_path, 'r', encoding='utf-8') as f:
                        secrets_config = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.error(f"Failed to load secrets: {e}")

            # 4. Merge: Base -> Secrets -> Preferences (excluding profile overlay)
            # Secrets override base, Preferences override secrets & base.
            # preferences.profile is applied after profiles.yaml — see step 6.
            merged_config = base_config.copy()
            cls._recursive_update(merged_config, secrets_config)
            prefs_for_config = {
                k: v for k, v in prefs.items() if k != "profile"
            }
            cls._recursive_update(merged_config, prefs_for_config)

            # 5. Inject Environment Variables (e.g. for Hubspace)
            # Ensure 'hubspace' section exists if env vars are present
            hub_user = os.getenv("HUBSPACE_USERNAME")
            hub_pass = os.getenv("HUBSPACE_PASSWORD")
            if hub_user or hub_pass:
                if 'hubspace' not in merged_config:
                    merged_config['hubspace'] = {}
                if hub_user:
                    merged_config['hubspace']['username'] = hub_user
                if hub_pass:
                    merged_config['hubspace']['password'] = hub_pass

            # 6. Load Profiles + preferences.profile overlay into runtime config
            cls._profiles_path = Path("config/profiles.yaml")
            profiles = {}
            if cls._profiles_path.exists():
                try:
                    with open(cls._profiles_path, 'r', encoding='utf-8') as f:
                        profiles = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.error(f"Failed to load profiles: {e}")

            # Publish all at once — until this point cls._config/_prefs/_profiles
            # are untouched, so a concurrent reader (e.g. get_profile() on
            # another thread) never observes an emptied-then-refilling state.
            cls._prefs = prefs
            cls._config = merged_config
            cls._profiles = profiles
            cls._loaded = True

            cls._merge_active_profile_into_config()

            return cls._config

    @classmethod
    def _get_profile_overlay(cls) -> Dict[str, Any]:
        overlay = cls._prefs.get("profile")
        return overlay if isinstance(overlay, dict) else {}

    @classmethod
    def _active_profile_id(cls) -> str:
        if not cls._loaded:
            cls.load_config()
        return cls._profiles.get("active_profile", "default")

    @classmethod
    def _merge_active_profile_into_config(cls) -> None:
        """Merge resolved active profile (base + preferences.profile) into _config."""
        active_profile = cls.get_profile()
        if not active_profile:
            return

        for section in cls._PROFILE_SECTIONS:
            if section in active_profile:
                if section not in cls._config:
                    cls._config[section] = {}
                cls._recursive_update(cls._config[section], active_profile[section])
                logger.info(f"Merged profile section: {section}")

        if "voice" in active_profile:
            cls._config["voice_name"] = active_profile["voice"]
            logger.info(f"Set voice from profile: {active_profile['voice']}")

    @classmethod
    def get_profile(cls) -> Dict[str, Any]:
        """Get the active profile (profiles.yaml base + preferences.profile overlay)."""
        if not cls._loaded:
            cls.load_config()

        active_id = cls._active_profile_id()
        base = copy.deepcopy(cls._profiles.get("profiles", {}).get(active_id, {}))
        overlay = cls._get_profile_overlay()
        if overlay:
            cls._recursive_update(base, overlay)
        return base

    @classmethod
    def get_profile_name(cls) -> str:
        """Get the friendly name of the current user."""
        return cls.get_profile().get("name", "User")

    @classmethod
    def get_persona_instruction(cls) -> str:
        """
        Builds a persona instruction string from the active profile.
        Falls back to a default if not defined.
        """
        active_profile = cls.get_profile()
        return "You are CHAD."

    @classmethod
    def get_voice_name(cls) -> str:
        """Get the voice name from profile, falling back to main config."""
        voice = cls.get_profile().get("voice")
        if voice:
            return voice
        return cls._config.get("voice_name", "Charon")

    DEFAULT_LLM_SELECTOR_MODEL = "qwen2.5-3b-instruct"

    @classmethod
    def get_llm_selector_model(cls) -> str:
        """The local model used for tool selection."""
        return cls.get("tools.llm_selector_model", cls.DEFAULT_LLM_SELECTOR_MODEL)

    @classmethod
    def get_routine_builder_model(cls) -> str:
        """Routine creation always follows the selected supervisor model."""
        return cls.get_active_model()

    @classmethod
    def set_routine_builder_model(cls, model_id: str) -> bool:
        """Legacy clients may acknowledge the supervisor, but cannot pin another model."""
        return bool(model_id and model_id.strip() == cls.get_active_model())

    @classmethod
    def get_search_paths(cls) -> List[str]:
        """Get resolved search paths from file_opener config."""
        from src.shared.paths import resolve_path
        paths = cls._config.get("file_opener", {}).get("search_paths", [])
        return [resolve_path(p) for p in paths]

    @classmethod
    def get_writer_config(cls) -> Dict[str, Any]:
        """Get resolved file writer configuration."""
        from src.shared.paths import resolve_path
        writer_cfg = cls._config.get("file_writer", {}).copy()
        
        if "default_directory" in writer_cfg:
            writer_cfg["default_directory"] = resolve_path(writer_cfg["default_directory"])
            
        if "allowed_directories" in writer_cfg:
            writer_cfg["allowed_directories"] = [resolve_path(p) for p in writer_cfg["allowed_directories"]]
            
        return writer_cfg

    @classmethod
    def get_memory_db_path(cls) -> str:
        """Get specific DB path for the active profile."""
        path = cls.get_profile().get("memory_db", "memory/chad_memory.db")
        return cls.expand_path(path)

    @classmethod
    def expand_path(cls, path_str: str) -> str:
        """
        Expand environment variables in path.
        Supports %VAR% syntax (Windows style) and $VAR (Linux style).
        """
        if not path_str:
            return ""
        return os.path.expandvars(path_str)

    @classmethod
    def get_active_model(cls) -> str:
        """
        Returns the project-wide active model identifier.
        Priority:
        1. User preference (preferences.yaml -> models.active_model)
        2. Config default (config.yaml -> active_model)
        3. Terminal: Raise Configuration Error
        """
        # 1. Try centralized supervisor / active_model in config (which merges preferences)
        active = (
            cls.get("local_models.supervisor")
            or cls.get("supervisor")
            or cls.get("local_models.active_model")
            or cls.get("active_model")
        )
        if active:
            if isinstance(active, dict):
                return active.get("name", "")
            return active
            
        # 2. Legacy fallback path (check text mode model)
        text_model = cls.get("models.text.model")
        if text_model:
            return text_model
            
        # 3. Terminal Error: Force Configuration
        raise RuntimeError(
            "CRITICAL ERROR: No active AI model configured. "
            "Please set 'supervisor' or 'active_model' in config/config.yaml or preferences.yaml."
        )

    @classmethod
    def is_local_model_active(cls) -> bool:
        """
        Return True if a local model (LM Studio / Ollama) is currently selected.
        When True, text requests should be routed to the local adapter instead of Gemini.
        """
        return bool(cls.get("local_models.use_local", False))

    # Categories the model selector itself controls. A category_models entry for
    # one of these would silently override the picker (UI shows model A, turns run
    # model B) — see get_local_model_config().
    _PICKER_CONTROLLED_CATEGORIES = ("agent", "chat")

    @classmethod
    def get_local_model_config(cls, category: Optional[str] = None) -> dict:
        """
        Return the current local model configuration dict for a turn category.

        Uses ``local_models.category_models.<category>`` when set, else
        ``local_models.supervisor`` / ``local_models.active_model`` (supervisor default).

        Ignores a category_models entry for a picker-controlled category (agent,
        conversation) rather than letting it silently shadow the user's selection —
        see _PICKER_CONTROLLED_CATEGORIES.
        """
        if not cls.is_local_model_active():
            return {}
        category_models = cls.get("local_models.category_models", {}) or {}
        active = None
        if category and category in category_models:
            if category in cls._PICKER_CONTROLLED_CATEGORIES:
                logger.warning(
                    f"[ CONFIG ] Ignoring local_models.category_models['{category}'] "
                    f"({category_models[category]!r}) — '{category}' is controlled by the "
                    f"model selector and must follow the supervisor model. Remove this "
                    f"entry from config.yaml."
                )
            else:
                active = category_models[category]
        if not active:
            active = (
                cls.get("local_models.supervisor")
                or cls.get("local_models.active_model")
                or cls.get("supervisor")
                or cls.get("active_model")
            )
        logger.debug(f"[ CONFIG ] Local model for category={category!r}: {active}")
        if not active:
            return {}
        if isinstance(active, dict):
            return active
        provider_cfg = cls.get_local_provider_config("lmstudio")
        return {
            "name": active,
            "provider": "lmstudio",
            "base_url": provider_cfg.get("base_url"),
            "api_key": provider_cfg.get("api_key"),
        }

    @classmethod
    def get_local_provider_config(cls, provider: str = "lmstudio") -> dict:
        """
        Return connection details for the given local provider.

        Args:
            provider: "lmstudio" or "ollama"

        Returns:
            Dict with base_url, api_key, enabled — or safe defaults.
        """
        cfg = cls.get(f"local_models.providers.{provider}", {})
        defaults = {
            "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lm-studio", "enabled": True},
            "ollama":   {"base_url": "http://localhost:11434/v1", "api_key": "ollama",    "enabled": False},
        }
        return {**defaults.get(provider, {}), **cfg}


    @classmethod
    def is_headless(cls) -> bool:
        """
        Check if the application should run in headless mode.
        Checks config/preferences or CHAD_HEADLESS environment variable.
        """
        if os.getenv("CHAD_HEADLESS", "").lower() == "true":
            return True
        return cls.get("headless", False)

    @staticmethod
    def _recursive_update(base: Dict, update: Dict) -> None:
        """Recursively update a dictionary."""
        for k, v in update.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                ConfigManager._recursive_update(base[k], v)
            else:
                base[k] = v

    @classmethod
    def get(cls, key_path: str, default: Any = None) -> Any:
        """
        Get a value from config using a dot-separated key path.
        Example: get("audio.preferred_device_name")
        """
        if not cls._loaded:
            cls.load_config()

        keys = key_path.split('.')
        value = cls._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    @classmethod
    def get_secret(cls, key: str, default: Any = None) -> Any:
        """
        Get a secret value.
        Checks: 1. combined config (secrets.yaml merged in) 2. environment variables.

        Placeholder stubs in secrets.yaml (e.g. ``your-brave-search-api-key-here``) are
        treated as missing so a real value in ``.env`` is not shadowed — same pitfall
        OpenHands dispatch already guards against for GitHub tokens.
        """
        # 1. Try config (already merged secrets.yaml in load_config)
        val = cls.get(key)
        if val is not None and not cls._looks_like_secret_placeholder(val):
            return val

        # 2. Try Environment
        # Normalize key to uppercase for env (e.g. hubspace.username -> HUBSPACE_USERNAME)
        env_key = key.replace('.', '_').upper()
        env_val = os.getenv(env_key)
        if env_val is not None and not cls._looks_like_secret_placeholder(env_val):
            logger.debug(f"[ CONFIG ] : Secret '{key}' loaded from OS Environment.")
            return env_val

        # Config had a placeholder and env empty — fall through to default
        if val is not None and cls._looks_like_secret_placeholder(val):
            logger.debug(
                f"[ CONFIG ] : Secret '{key}' in secrets.yaml looks like a placeholder; "
                "no real env override found."
            )

        logger.debug(f"[ CONFIG ] : Secret '{key}' not found in Config or Env. Using default: {default}")
        return default

    @staticmethod
    def _looks_like_secret_placeholder(val: Any) -> bool:
        """True for fresh-config stubs that must not shadow a real ``.env`` value."""
        if val is None:
            return True
        low = str(val).strip().lower()
        if not low:
            return True
        if low.startswith(("<", "changeme", "todo", "replace")):
            return True
        # e.g. your-brave-search-api-key-here / your_api_key_here
        if low.startswith("your") and (
            "here" in low or "api" in low or "key" in low or "token" in low
        ):
            return True
        return False

    @classmethod
    def set_secret(cls, key: str, value: str) -> bool:
        """
        DEPRECATED: Use ConfigRepository.update_secret() instead.

        ConfigManager is now read-only. All mutations should go through
        the ConfigRepository for cleaner separation of concerns.

        Args:
            key: Secret key path
            value: Secret value

        Returns:
            False (always fails, use ConfigRepository)
        """
        logger.error(
            f"ConfigManager.set_secret() is deprecated. "
            f"Use ConfigRepository.update_secret('{key}', '{value}') instead."
        )
        raise NotImplementedError(
            "ConfigManager is read-only. Use ConfigRepository.update_secret() for mutations."
        )

    @classmethod
    def update_preference(cls, key: str, value: Any) -> bool:
        """
        DEPRECATED: Use ConfigRepository.update_preference() instead.

        ConfigManager is now read-only. All mutations should go through
        the ConfigRepository for cleaner separation of concerns.

        Args:
            key: Preference key path
            value: Preference value

        Returns:
            False (always fails, use ConfigRepository)
        """
        logger.error(
            f"ConfigManager.update_preference() is deprecated. "
            f"Use ConfigRepository.update_preference('{key}', {value}) instead."
        )
        raise NotImplementedError(
            "ConfigManager is read-only. Use ConfigRepository.update_preference() for mutations."
        )

    @classmethod
    def save_config(cls, key_path: str, value: Any) -> bool:
        """
        DEPRECATED: Use ConfigRepository.update_preference() instead.

        ConfigManager is now read-only (Phase 4.3 refactoring).
        All configuration mutations should go through ConfigRepository
        for cleaner separation of concerns and better testability.

        Migration:
            OLD: ConfigManager.save_config('audio.volume', 0.8)
            NEW: ConfigRepository().update_preference('audio.volume', 0.8)

        Args:
            key_path: Dot-separated config key
            value: Value to set

        Returns:
            False (always fails, use ConfigRepository)
        """
        logger.error(
            f"ConfigManager.save_config() is deprecated. "
            f"Use ConfigRepository().update_preference('{key_path}', {value}) instead."
        )
        raise NotImplementedError(
            "ConfigManager is read-only. Use ConfigRepository.update_preference() for mutations."
        )





