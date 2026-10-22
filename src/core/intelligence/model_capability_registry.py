"""
Model Capability Registry

Single source of truth for per-model capability facts (context window size, feature support).
Loaded from config/model_capabilities.yaml; separate from user preferences in config.yaml.

Usage:
    registry = get_model_capability_registry()
    win = registry.get_context_window("qwen/qwen3.5-9b")   # 32768
    profile = registry.get_context_budget_profile("qwen/qwen3.5-9b")  # "local_small"
    limit = registry.get_max_tool_result_chars("qwen/qwen3.5-9b")     # 4000

The registry also tracks the currently active local model so other components
(e.g. MCPExecutor) can query the active limits without needing the model ID.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("ModelCapabilityRegistry")

# Path relative to project root
_CAPABILITIES_FILE = Path(__file__).parent.parent.parent.parent / "config" / "model_capabilities.yaml"

# Per-profile tool result character limits.
# Cloud models keep the existing 25 000-char ceiling; local models are tighter
# so a handful of MCP calls can't exhaust the available context budget.
_PROFILE_TOOL_CHAR_LIMITS: Dict[str, int] = {
    "local_small": 4000,    # 32K models  — ~1 000 tokens per result
    "local_large": 10000,   # 128K models — ~2 500 tokens per result
    "aggressive":  25000,   # cloud (existing behaviour)
    "balanced":    25000,
    "permissive":  25000,
}

_DEFAULT_CONTEXT_WINDOW = 128000  # safe fallback for unknown cloud models


def _derive_context_budget_profile(context_window: int) -> str:
    """Map a context window size to the best-fit compaction profile."""
    if context_window <= 40000:
        return "local_small"
    elif context_window <= 150000:
        return "local_large"
    return "balanced"


class ModelCapabilityRegistry:
    """
    Loads model_capabilities.yaml once and exposes typed lookups.
    Tracks the currently active local model ID so downstream components
    can query limits without threading the model ID through every call.
    """

    def __init__(self) -> None:
        self._capabilities: Dict[str, Dict[str, Any]] = {}
        self._active_model_id: Optional[str] = None
        self._load()

    def _load(self) -> None:
        try:
            import yaml
            with open(_CAPABILITIES_FILE, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            # Normalise: each value should be a dict; legacy boolean entries (old format) are ignored
            self._capabilities = {
                k: v for k, v in raw.items() if isinstance(v, dict)
            }
            logger.debug(f"[ CAPABILITIES ] Loaded {len(self._capabilities)} model entries")
        except FileNotFoundError:
            logger.warning(f"[ CAPABILITIES ] {_CAPABILITIES_FILE} not found — using defaults")
        except Exception as exc:
            logger.warning(f"[ CAPABILITIES ] Failed to load capabilities: {exc}")

    # ------------------------------------------------------------------
    # Active model tracking (updated by ModelCoordinator on each resolve)
    # ------------------------------------------------------------------

    def set_active_model(self, model_id: Optional[str]) -> None:
        """Call when the active local model changes (pass None when switching to cloud)."""
        self._active_model_id = model_id
        if model_id:
            profile = self.get_context_budget_profile(model_id)
            limit = self.get_max_tool_result_chars(model_id)
            logger.info(
                f"[ CAPABILITIES ] Active model '{model_id}' → "
                f"context={self.get_context_window(model_id)} | "
                f"profile={profile} | tool_limit={limit} chars"
            )

    # ------------------------------------------------------------------
    # Per-model lookups
    # ------------------------------------------------------------------

    def get_context_window(self, model_id: str) -> int:
        entry = self._capabilities.get(model_id, {})
        return entry.get("context_window", _DEFAULT_CONTEXT_WINDOW)

    def get_context_budget_profile(self, model_id: str) -> str:
        entry = self._capabilities.get(model_id, {})
        # Honour an explicit override in the YAML first
        if "context_budget_profile" in entry:
            return entry["context_budget_profile"]
        return _derive_context_budget_profile(entry.get("context_window", _DEFAULT_CONTEXT_WINDOW))

    def get_max_tool_result_chars(self, model_id: str) -> int:
        profile = self.get_context_budget_profile(model_id)
        return _PROFILE_TOOL_CHAR_LIMITS.get(profile, 25000)

    def supports_thinking(self, model_id: str) -> bool:
        return bool(self._capabilities.get(model_id, {}).get("supports_thinking", False))

    def supports_tool_calls(self, model_id: str) -> bool:
        return bool(self._capabilities.get(model_id, {}).get("supports_tool_calls", True))

    # ------------------------------------------------------------------
    # Active-model convenience queries (used by MCPExecutor)
    # ------------------------------------------------------------------

    def get_active_max_tool_result_chars(self) -> int:
        """Return the char limit for the currently active model (25 000 if no local model)."""
        if not self._active_model_id:
            return 25000
        return self.get_max_tool_result_chars(self._active_model_id)

    def get_active_context_budget_profile(self) -> Optional[str]:
        if not self._active_model_id:
            return None
        return self.get_context_budget_profile(self._active_model_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry_instance: Optional[ModelCapabilityRegistry] = None


def get_model_capability_registry() -> ModelCapabilityRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ModelCapabilityRegistry()
    return _registry_instance
