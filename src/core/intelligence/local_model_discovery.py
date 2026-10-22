"""
Local Model Discovery Service

Detects and enumerates AI models available from local inference servers
(LM Studio, Ollama). Returns structured info including tool-calling capability.

Quick start:
    discovery = LocalModelDiscovery()
    models = await discovery.discover_all()
    for m in models:
        print(m.display_name, "tools:", m.supports_tools)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import time

from src.shared.logger import setup_logger

logger = setup_logger("LocalModelDiscovery")

# Model IDs containing any of these are not chat models and must never be
# listed (or probed) as a candidate for the conversational LLM dropdown.
NON_CHAT_MODEL_TERMS = (
    "embed", "rerank", "clip", "tts", "kokoro", "whisper", "stt", "speecht5",
)


@dataclass
class LocalModelInfo:
    """Metadata for a discovered local model."""
    name: str                    # Raw model ID from the API
    display_name: str            # Human-friendly name for the UI
    provider: str                # "lmstudio" or "ollama"
    base_url: str                # API base URL
    api_key: str                 # API key (arbitrary for local)
    supports_tools: bool = True  # False only when explicitly verified unsupported
    tools_verified: bool = False # True when value comes from cache or active probe
    size_hint: str = ""          # e.g. "32B" parsed from model name


def _parse_display_name(model_id: str) -> str:
    """Extract a readable name from a model ID string."""
    # Strip common path prefixes (e.g. "lmstudio-community/Qwen3-32B-GGUF")
    name = model_id.split("/")[-1]
    # Strip file extensions
    for ext in (".gguf", ".bin", ".safetensors"):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
    return name


def _parse_size_hint(model_id: str) -> str:
    """Try to extract a parameter count from a model name (e.g. '32B')."""
    import re
    # Look for patterns like 32B, 7b, 14B, 72b
    match = re.search(r'(\d+\.?\d*[bBmM])', model_id)
    return match.group(1).upper() if match else ""


class LocalModelDiscovery:
    """
    Discovers and verifies local AI models from LM Studio and Ollama.

    Results are cached for 30 seconds to avoid hammering the local API on
    every keystroke / page load.
    """

    CACHE_TTL = 600  # seconds (10 minutes)

    def __init__(self):
        self._cache: List[LocalModelInfo] = []
        self._cache_ts: float = 0.0
        self._capability_cache: Dict[str, bool] = {}  # {model_id: supports_tools}
        self._lock = asyncio.Lock()
        self._load_capability_cache()

    def _load_capability_cache(self) -> None:
        """Load capability cache from disk."""
        try:
            from src.infrastructure.repositories.config_repository import ConfigRepository
            repo = ConfigRepository()
            cached = repo.load("local_model_tool_support_cache", use_cache=False)
            if cached and isinstance(cached, dict):
                self._capability_cache = cached
                logger.debug(f"[ DISCOVERY ] Loaded {len(cached)} model capabilities from disk")
        except Exception as e:
            logger.warning(f"[ DISCOVERY ] Failed to load capability cache: {e}")

    def _save_capability_cache(self) -> None:
        """Save capability cache to disk."""
        try:
            from src.infrastructure.repositories.config_repository import ConfigRepository
            repo = ConfigRepository()
            repo.save("local_model_tool_support_cache", self._capability_cache)
            logger.debug("[ DISCOVERY ] Saved capability cache to disk")
        except Exception as e:
            logger.warning(f"[ DISCOVERY ] Failed to save capability cache: {e}")

    def _has_heuristic_tool_support(self, model_id: str) -> bool:
        """
        Check if a model is known to support tools based on its name.
        This allows skipping the 'weather' test for common modern models.
        """
        id_lower = model_id.lower()
        
        # 1. Known tool-capable families
        tool_families = [
            "qwen3", "qwen2.5", "qwen2", "qwen-72b", "qwen-32b", "qwen-14b", "qwen-7b",
            "llama-3.1", "llama-3.2", "llama-3-70b", "llama-3-8b", "llama3", "llama-3",
            "mistral-nemo", "mistral-large", "mixtral-8x22b", "mixtral", "mistral",
            "command-r", "hermes-3", "hermes-2-pro", "granite-3", "granite",
            "gemma-3", "gemma-2", "gemma2", "gemma",
            "deepseek", "phi-4", "phi-3", "phi3",
            "ministral", "devstral", "magistral",
        ]
        
        if any(f in id_lower for f in tool_families):
            return True
            
        # 2. Keywords indicating instruction/tool focus
        # Qwen and Llama-3 models with 'instruct' in name almost always support tools
        if "instruct" in id_lower and ("qwen" in id_lower or "llama-3" in id_lower or "llama-3.1" in id_lower):
            return True
            
        return False

    async def discover_lmstudio(
        self,
        base_url: str = "http://localhost:1234/v1",
        api_key: str = "lm-studio",
        verify_tools: bool = False,
    ) -> List[LocalModelInfo]:
        """Discover models from a running LM Studio server."""
        from src.core.intelligence.adapters.openai_adapter import OpenAIAdapter

        # A temporary adapter just for discovery (model_name not used for listing)
        probe = OpenAIAdapter(base_url=base_url, api_key=api_key, model_name="probe")
        model_ids = await probe.discover_models(timeout=2.0)

        if not model_ids:
            logger.debug("[ DISCOVERY ] LM Studio: no models found (server may not be running)")
            return []

        results = []
        for model_id in model_ids:
            # Skip non-chat models (embeddings, rerankers, vision encoders, TTS/STT)
            if any(term in model_id.lower() for term in NON_CHAT_MODEL_TERMS):
                continue

            info = LocalModelInfo(
                name=model_id,
                display_name=_parse_display_name(model_id),
                provider="lmstudio",
                base_url=base_url,
                api_key=api_key,
                size_hint=_parse_size_hint(model_id),
            )

            # 1. Check cache first (only source of verified negatives)
            if model_id in self._capability_cache:
                info.supports_tools = self._capability_cache[model_id]
                info.tools_verified = True
                logger.debug(f"[ DISCOVERY ] Using cached capability for '{model_id}': {info.supports_tools}")
            elif self._has_heuristic_tool_support(model_id):
                # 2. Heuristic check (reliable for known models)
                info.supports_tools = True
                info.tools_verified = False
                self._capability_cache[model_id] = True
                self._save_capability_cache()
                logger.debug(f"[ DISCOVERY ] Heuristic: '{model_id}' supports tools")
            elif verify_tools:
                # 3. ONLY run active test if explicitly requested
                adapter = OpenAIAdapter(
                    base_url=base_url,
                    api_key=api_key,
                    model_name=model_id,
                )
                result = await adapter.test_tool_calling()
                if result is not None:
                    info.supports_tools = result
                    info.tools_verified = True
                    self._capability_cache[model_id] = result
                    self._save_capability_cache()
            else:
                # Unknown models: assume capable — listing must stay cheap (no probe-load).
                info.supports_tools = True
                info.tools_verified = False

            results.append(info)
            logger.debug(
                f"[ DISCOVERY ] LM Studio: found '{info.display_name}' "
                f"(tools={'[YES]' if info.supports_tools else '[NO]'})"
            )

        return results

    async def discover_ollama(
        self,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        verify_tools: bool = False,
    ) -> List[LocalModelInfo]:
        """Discover models from a running Ollama server."""
        from src.core.intelligence.adapters.openai_adapter import OpenAIAdapter

        probe = OpenAIAdapter(base_url=base_url, api_key=api_key, model_name="probe")
        model_ids = await probe.discover_models(timeout=2.0)

        if not model_ids:
            logger.debug("[ DISCOVERY ] Ollama: no models found (server may not be running)")
            return []

        results = []
        for model_id in model_ids:
            # Skip non-chat models (embeddings, rerankers, vision encoders, TTS/STT)
            if any(term in model_id.lower() for term in NON_CHAT_MODEL_TERMS):
                continue

            info = LocalModelInfo(
                name=model_id,
                display_name=_parse_display_name(model_id),
                provider="ollama",
                base_url=base_url,
                api_key=api_key,
                size_hint=_parse_size_hint(model_id),
            )

            if model_id in self._capability_cache:
                info.supports_tools = self._capability_cache[model_id]
                info.tools_verified = True
            elif self._has_heuristic_tool_support(model_id):
                info.supports_tools = True
                info.tools_verified = False
                self._capability_cache[model_id] = True
                self._save_capability_cache()
            elif verify_tools:
                adapter = OpenAIAdapter(
                    base_url=base_url,
                    api_key=api_key,
                    model_name=model_id,
                )
                result = await adapter.test_tool_calling()
                if result is not None:
                    info.supports_tools = result
                    info.tools_verified = True
                    self._capability_cache[model_id] = result
                    self._save_capability_cache()
            else:
                info.supports_tools = True
                info.tools_verified = False

            results.append(info)
            logger.debug(
                f"[ DISCOVERY ] Ollama: found '{info.display_name}' "
                f"(tools={'[YES]' if info.supports_tools else '[NO]'})"
            )

        return results

    async def discover_all(
        self,
        verify_tools: bool = False,
        use_cache: bool = True,
    ) -> List[LocalModelInfo]:
        """
        Discover all available local models from configured providers.
        """
        async with self._lock:
            now = time.monotonic()
            if use_cache and self._cache and (now - self._cache_ts) < self.CACHE_TTL:
                logger.debug("[ DISCOVERY ] Returning cached model list")
                return list(self._cache)

            from src.shared.config_manager import ConfigManager
            local_cfg = ConfigManager.get("local_models", {})
            providers_cfg = local_cfg.get("providers", {})

            tasks = []

            lm_cfg = providers_cfg.get("lmstudio", {})
            if lm_cfg.get("enabled", True):
                tasks.append(
                    self.discover_lmstudio(
                        base_url=lm_cfg.get("base_url", "http://localhost:1234/v1"),
                        api_key=lm_cfg.get("api_key", "lm-studio"),
                        verify_tools=verify_tools,
                    )
                )

            ollama_cfg = providers_cfg.get("ollama", {})
            if ollama_cfg.get("enabled", False):
                tasks.append(
                    self.discover_ollama(
                        base_url=ollama_cfg.get("base_url", "http://localhost:11434/v1"),
                        api_key=ollama_cfg.get("api_key", "ollama"),
                        verify_tools=verify_tools,
                    )
                )

            if not tasks:
                return []

            # Run discovery for all providers concurrently
            results_per_provider = await asyncio.gather(*tasks, return_exceptions=True)

            all_models: List[LocalModelInfo] = []
            for res in results_per_provider:
                if isinstance(res, Exception):
                    logger.warning(f"[ DISCOVERY ] Provider discovery failed: {res}")
                else:
                    all_models.extend(res)

            self._cache = all_models
            self._cache_ts = now
            logger.debug(f"[ DISCOVERY ] Found {len(all_models)} local model(s) total")
            return all_models

    def invalidate_cache(self) -> None:
        """Force next discover_all() to re-query servers."""
        self._cache = []
        self._cache_ts = 0.0


# Module-level singleton so the cache is shared across components
_discovery_instance: Optional[LocalModelDiscovery] = None


def get_local_model_discovery() -> LocalModelDiscovery:
    """Get or create the shared LocalModelDiscovery instance."""
    global _discovery_instance
    if _discovery_instance is None:
        _discovery_instance = LocalModelDiscovery()
    return _discovery_instance
