"""
Provider Failover Manager - Automatic provider switching on failures (Pi/OpenClaw Pattern)

Provides automatic failover between AI providers with credential rotation and cooldown tracking.

Features:
    - Multi-provider profiles (Gemini, OpenAI, Claude, etc.)
    - Credential rotation (multiple API keys per provider)
    - Cooldown tracking (temporary disable after failures)
    - Error type detection (auth, rate_limit, quota, timeout)
    - Automatic provider switching on failures
    - Health tracking per provider

Flow:
    1. Primary provider fails (rate limit, quota, auth error)
    2. Detect error type and severity
    3. Apply cooldown to failed provider (5min for rate limit, 1h for quota)
    4. Select next available provider from fallback chain
    5. Rotate API key if multiple keys available
    6. Retry with fallback provider

Use Cases:
    - Gemini hits rate limit → switch to OpenAI
    - OpenAI quota exceeded → switch to Claude
    - API key exhausted → rotate to next key
    - Provider downtime → use backup provider

OpenClaw/Pi Pattern Source:
    - Multi-provider resilience from Pi agent SDK
    - Credential rotation and cooldown tracking
    - Provider-specific error normalization
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import yaml
from pathlib import Path

from src.shared.logger import setup_logger

logger = setup_logger("ProviderFailover")


class ErrorType(Enum):
    """Types of provider errors."""
    AUTH = "auth"                    # Authentication failure
    RATE_LIMIT = "rate_limit"        # Rate limit exceeded
    QUOTA = "quota"                  # Quota/billing limit exceeded
    TIMEOUT = "timeout"              # Request timeout
    SERVER_ERROR = "server_error"    # 5xx server errors
    NETWORK = "network"              # Network connectivity issues
    UNKNOWN = "unknown"              # Unclassified error


@dataclass
class ProviderProfile:
    """
    Configuration for a single AI provider.

    Attributes:
        provider: Provider name (gemini, openai, claude, etc.)
        model: Model identifier
        api_keys: List of API keys for rotation
        priority: Provider priority (lower = preferred)
        cooldown_until: Timestamp when provider becomes available again
        failure_count: Number of consecutive failures
        last_success: Timestamp of last successful request
        enabled: Whether provider is enabled
        max_retries: Maximum retry attempts before cooldown
    """
    provider: str
    model: str
    api_keys: List[str]
    priority: int = 50
    cooldown_until: Optional[datetime] = None
    failure_count: int = 0
    last_success: Optional[datetime] = None
    enabled: bool = True
    max_retries: int = 3
    current_key_index: int = 0

    def is_available(self) -> bool:
        """Check if provider is available (not in cooldown)."""
        if not self.enabled:
            return False

        if self.cooldown_until:
            return datetime.now() >= self.cooldown_until

        return True

    def get_current_api_key(self) -> Optional[str]:
        """Get the current API key from rotation."""
        if not self.api_keys:
            return None
        return self.api_keys[self.current_key_index % len(self.api_keys)]

    def rotate_api_key(self) -> Optional[str]:
        """Rotate to next API key."""
        if not self.api_keys or len(self.api_keys) <= 1:
            return self.get_current_api_key()

        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        logger.info(f"Rotated API key for {self.provider} (index: {self.current_key_index})")
        return self.get_current_api_key()

    def mark_success(self) -> None:
        """Mark successful request."""
        self.last_success = datetime.now()
        self.failure_count = 0
        self.cooldown_until = None

    def mark_failure(self, error_type: ErrorType) -> None:
        """Mark failed request and apply cooldown if needed."""
        self.failure_count += 1

        # Apply cooldown based on error type and failure count
        if error_type == ErrorType.RATE_LIMIT:
            # Rate limit: 5 minutes cooldown
            cooldown = timedelta(minutes=5)
        elif error_type == ErrorType.QUOTA:
            # Quota exceeded: 1 hour cooldown
            cooldown = timedelta(hours=1)
        elif error_type == ErrorType.AUTH:
            # Auth failure: try key rotation first, then 15 min cooldown
            if len(self.api_keys) > 1:
                self.rotate_api_key()
                return  # Don't apply cooldown yet, try next key
            cooldown = timedelta(minutes=15)
        elif error_type == ErrorType.SERVER_ERROR:
            # Server error: 2 minutes cooldown
            cooldown = timedelta(minutes=2)
        else:
            # Other errors: 1 minute cooldown
            cooldown = timedelta(minutes=1)

        # Exponential backoff for repeated failures
        if self.failure_count >= self.max_retries:
            cooldown *= self.failure_count

        self.cooldown_until = datetime.now() + cooldown
        logger.warning(
            f"Provider {self.provider} marked as failed (type: {error_type.value}, "
            f"failures: {self.failure_count}, cooldown until: {self.cooldown_until})"
        )


class ProviderFailoverManager:
    """
    Manages automatic failover between AI providers.

    Features:
    - Maintains provider profiles with health tracking
    - Detects error types from exceptions
    - Selects fallback providers based on priority and availability
    - Rotates API keys for failed providers
    - Applies cooldown periods to prevent hammering failed providers
    """

    def __init__(self):
        """Initialize provider failover manager."""
        self._providers: Dict[str, ProviderProfile] = {}
        self._provider_order: List[str] = []  # Sorted by priority
        self._enabled = True

        logger.debug("ProviderFailoverManager initialized")

    def load_providers(self, config_path: str) -> None:
        """
        Load provider profiles from YAML configuration.

        Args:
            config_path: Path to provider_profiles.yaml

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config format is invalid
        """
        config_file = Path(config_path)

        if not config_file.exists():
            logger.warning(f"Provider config not found: {config_path}, using defaults")
            self._create_default_providers()
            return

        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)

            # Load provider profiles
            providers_config = config.get('providers', {})
            for provider_name, provider_data in providers_config.items():
                profile = ProviderProfile(
                    provider=provider_name,
                    model=provider_data.get('model', ''),
                    api_keys=provider_data.get('api_keys', []),
                    priority=provider_data.get('priority', 50),
                    enabled=provider_data.get('enabled', True),
                    max_retries=provider_data.get('max_retries', 3)
                )
                self._providers[provider_name] = profile

            # Sort by priority (lower = preferred)
            self._provider_order = sorted(
                self._providers.keys(),
                key=lambda p: self._providers[p].priority
            )

            # Check if failover is enabled
            self._enabled = config.get('enabled', True)

            logger.debug(
                f"Loaded {len(self._providers)} provider profiles. "
                f"Priority order: {self._provider_order}"
            )

        except Exception as e:
            logger.error(f"Failed to load provider profiles: {e}")
            self._create_default_providers()

    def _create_default_providers(self) -> None:
        """Create default provider profiles dynamically from environment variables."""
        import os
        
        # Known key suffixes to detect providers dynamically
        known_providers = {
            "GEMINI_API_KEY": ("gemini", "gemini-2.5-flash"),
            "OPENAI_API_KEY": ("openai", "gpt-4o-mini"),
            "ANTHROPIC_API_KEY": ("claude", "claude-3-5-sonnet-latest")
        }

        added_any = False
        for env_var, (name, default_model) in known_providers.items():
            key = os.getenv(env_var)
            if key:
                self._providers[name] = ProviderProfile(
                    provider=name,
                    model=default_model,
                    api_keys=[key],
                    priority=10 if not added_any else 20,
                    enabled=True
                )
                self._provider_order.append(name)
                logger.info(f"Created dynamic default profile for provider: {name}")
                added_any = True

        if not added_any:
            logger.warning("No fallback providers could be automatically initialized from environment.")

    def detect_error_type(self, error: Exception) -> ErrorType:
        """
        Detect error type from exception.

        Args:
            error: Exception raised by provider

        Returns:
            ErrorType classification
        """
        error_str = str(error).lower()

        # Authentication errors
        if any(x in error_str for x in ['auth', 'unauthorized', 'invalid api key', '401']):
            return ErrorType.AUTH

        # Rate limit errors
        if any(x in error_str for x in ['rate limit', 'too many requests', '429']):
            return ErrorType.RATE_LIMIT

        # Quota/billing errors
        if any(x in error_str for x in ['quota', 'billing', 'insufficient', 'exceeded']):
            return ErrorType.QUOTA

        # Timeout errors
        if any(x in error_str for x in ['timeout', 'timed out', 'deadline']):
            return ErrorType.TIMEOUT

        # Server errors
        if any(x in error_str for x in ['500', '502', '503', '504', 'server error']):
            return ErrorType.SERVER_ERROR

        # Network errors
        if any(x in error_str for x in ['network', 'connection', 'unreachable']):
            return ErrorType.NETWORK

        return ErrorType.UNKNOWN

    def mark_failure(self, provider: str, error: Exception) -> None:
        """
        Mark provider as failed and apply cooldown.

        Args:
            provider: Provider name
            error: Exception that occurred
        """
        if provider not in self._providers:
            logger.warning(f"Unknown provider: {provider}")
            return

        profile = self._providers[provider]
        error_type = self.detect_error_type(error)

        profile.mark_failure(error_type)

        logger.warning(
            f"Provider {provider} failed with {error_type.value}: {str(error)[:100]}"
        )

    def mark_success(self, provider: str) -> None:
        """
        Mark provider as successful.

        Args:
            provider: Provider name
        """
        if provider not in self._providers:
            return

        profile = self._providers[provider]
        profile.mark_success()

        logger.debug(f"Provider {provider} marked as successful")

    def get_next_provider(
        self,
        current_provider: Optional[str] = None,
        exclude: Optional[List[str]] = None
    ) -> Optional[ProviderProfile]:
        """
        Get next available provider for failover.

        Args:
            current_provider: Provider that just failed (will be skipped)
            exclude: Additional providers to exclude

        Returns:
            Next available ProviderProfile, or None if no providers available
        """
        if not self._enabled:
            return None

        exclude = exclude or []
        if current_provider:
            exclude.append(current_provider)

        # Find first available provider in priority order
        for provider_name in self._provider_order:
            if provider_name in exclude:
                continue

            profile = self._providers[provider_name]
            if profile.is_available():
                logger.info(
                    f"Selected failover provider: {provider_name} "
                    f"(priority: {profile.priority})"
                )
                return profile

        logger.error("No available providers for failover")
        return None

    def get_provider(self, provider_name: str) -> Optional[ProviderProfile]:
        """
        Get provider profile by name.

        Args:
            provider_name: Provider name

        Returns:
            ProviderProfile or None if not found
        """
        return self._providers.get(provider_name)

    def list_providers(self) -> List[Dict[str, Any]]:
        """
        List all provider profiles with status.

        Returns:
            List of provider info dictionaries
        """
        result = []

        for provider_name in self._provider_order:
            profile = self._providers[provider_name]
            result.append({
                'provider': provider_name,
                'model': profile.model,
                'priority': profile.priority,
                'enabled': profile.enabled,
                'available': profile.is_available(),
                'failure_count': profile.failure_count,
                'cooldown_until': profile.cooldown_until.isoformat() if profile.cooldown_until else None,
                'last_success': profile.last_success.isoformat() if profile.last_success else None,
                'api_key_count': len(profile.api_keys)
            })

        return result

    def enable(self) -> None:
        """Enable failover system."""
        self._enabled = True
        logger.info("Provider failover enabled")

    def disable(self) -> None:
        """Disable failover system."""
        self._enabled = False
        logger.warning("Provider failover disabled")

    def is_enabled(self) -> bool:
        """Check if failover is enabled."""
        return self._enabled

    def reset_provider(self, provider: str) -> bool:
        """
        Reset provider state (clear cooldown, reset failures).

        Args:
            provider: Provider name

        Returns:
            True if reset successful
        """
        if provider not in self._providers:
            return False

        profile = self._providers[provider]
        profile.failure_count = 0
        profile.cooldown_until = None
        profile.current_key_index = 0

        logger.info(f"Reset provider state: {provider}")
        return True

    def reset_all(self) -> None:
        """Reset all provider states."""
        for provider in self._providers:
            self.reset_provider(provider)
        logger.info("Reset all provider states")


# Global singleton instance
_failover_manager: Optional[ProviderFailoverManager] = None


def get_failover_manager() -> ProviderFailoverManager:
    """
    Get the global ProviderFailoverManager instance.

    Returns:
        Singleton ProviderFailoverManager instance
    """
    global _failover_manager

    if _failover_manager is None:
        _failover_manager = ProviderFailoverManager()
        # Attempt to load default config
        try:
            _failover_manager.load_providers("config/provider_profiles.yaml")
        except Exception as e:
            logger.warning(f"Failed to load default provider profiles: {e}")

    return _failover_manager


def scan_available_providers() -> List[str]:
    """
    Scan environment variables and profile configs to detect available AI providers.
    Returns a list of provider strings (e.g. ['gemini', 'openai']).
    """
    import os
    found = []
    
    # Check explicit configuration profiles first
    mgr = get_failover_manager()
    for p in mgr.list_providers():
        if p.get('available') and p.get('api_key_count', 0) > 0:
            found.append(p['provider'])
            
    # Fallback scan of implicit environment keys
    env_map = {
        "GEMINI_API_KEY": "gemini",
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "GROQ_API_KEY": "groq"
    }
    
    for key, name in env_map.items():
        if os.getenv(key) and name not in found:
            found.append(name)
            
    return found
