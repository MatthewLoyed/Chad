"""
Idempotency Manager - Prevent Duplicate Tool Executions

Implements OpenClaw-inspired idempotency cache to prevent duplicate execution
of side-effecting tools (emails, messages, file operations) on network retries.

Pattern adopted from OpenClaw's tool execution safeguards.
"""

import hashlib
import json
import logging
import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("IdempotencyManager")


@dataclass
class CachedResult:
    """Cached tool execution result with metadata."""
    result: Any
    timestamp: float
    tool_name: str
    success: bool


class IdempotencyManager:
    """
    Manages idempotency cache for tool executions.

    Prevents duplicate execution of side-effecting tools by caching results
    based on tool name + parameters hash.

    Features:
    - 8-hour TTL (time-to-live) for cached results
    - Automatic cache cleanup on access
    - Thread-safe (designed for single-threaded async use)
    - Supports both successful and failed results
    """

    def __init__(self, ttl_seconds: int = 28800):  # 8 hours default
        """
        Initialize IdempotencyManager.

        Args:
            ttl_seconds: Time-to-live for cached results (default: 8 hours)
        """
        self._cache: Dict[str, CachedResult] = {}
        self._ttl_seconds = ttl_seconds
        logger.info(f"IdempotencyManager initialized (TTL: {ttl_seconds}s)")

    def _generate_key(self, tool_name: str, params: Dict[str, Any]) -> str:
        """
        Generate cache key from tool name and parameters.

        Args:
            tool_name: Name of the tool
            params: Tool parameters dictionary

        Returns:
            MD5 hash of tool_name + sorted parameters

        Example:
            >>> manager._generate_key("gmail_send", {"to": "test@example.com", "subject": "Hello"})
            "gmail_send:a1b2c3d4..."
        """
        # Sort parameters for consistent hashing
        sorted_params = json.dumps(params, sort_keys=True, default=str)

        # Create hash
        param_hash = hashlib.md5(sorted_params.encode()).hexdigest()

        return f"{tool_name}:{param_hash}"

    def _is_expired(self, cached: CachedResult) -> bool:
        """
        Check if cached result has expired.

        Args:
            cached: Cached result to check

        Returns:
            True if expired (older than TTL)
        """
        age = time.time() - cached.timestamp
        return age > self._ttl_seconds

    def _cleanup_expired(self) -> None:
        """Remove expired entries from cache."""
        expired_keys = [
            key for key, cached in self._cache.items()
            if self._is_expired(cached)
        ]

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")

    def get_cached(self, tool_name: str, params: Dict[str, Any]) -> Optional[CachedResult]:
        """
        Get cached result for a tool execution.

        Args:
            tool_name: Name of the tool
            params: Tool parameters

        Returns:
            CachedResult if found and not expired, None otherwise
        """
        # Cleanup expired entries opportunistically
        self._cleanup_expired()

        key = self._generate_key(tool_name, params)

        if key in self._cache:
            cached = self._cache[key]

            if not self._is_expired(cached):
                logger.info(
                    f"[ IDEMPOTENCY ] Cache hit for {tool_name} "
                    f"(age: {time.time() - cached.timestamp:.1f}s)"
                )
                return cached
            else:
                # Remove expired entry
                del self._cache[key]

        return None

    def cache_result(
        self,
        tool_name: str,
        params: Dict[str, Any],
        result: Any,
        success: bool
    ) -> None:
        """
        Cache a tool execution result.

        Args:
            tool_name: Name of the tool
            params: Tool parameters
            result: Execution result
            success: Whether execution succeeded
        """
        key = self._generate_key(tool_name, params)

        cached = CachedResult(
            result=result,
            timestamp=time.time(),
            tool_name=tool_name,
            success=success
        )

        self._cache[key] = cached

        logger.debug(f"[ IDEMPOTENCY ] Cached result for {tool_name} (key: {key[:16]}...)")

    def should_skip(
        self,
        tool_name: str,
        params: Dict[str, Any]
    ) -> Tuple[bool, Optional[CachedResult]]:
        """
        Check if tool execution should be skipped (cached result available).

        Args:
            tool_name: Name of the tool
            params: Tool parameters

        Returns:
            Tuple of (should_skip, cached_result)

        Example:
            >>> should_skip, cached = manager.should_skip("gmail_send", {...})
            >>> if should_skip:
            ...     return cached.result
        """
        cached = self.get_cached(tool_name, params)

        if cached:
            return True, cached

        return False, None

    def invalidate(self, tool_name: str, params: Dict[str, Any]) -> bool:
        """
        Manually invalidate a cached result.

        Args:
            tool_name: Name of the tool
            params: Tool parameters

        Returns:
            True if entry was found and removed
        """
        key = self._generate_key(tool_name, params)

        if key in self._cache:
            del self._cache[key]
            logger.info(f"[ IDEMPOTENCY ] Invalidated cache for {tool_name}")
            return True

        return False

    def clear(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"[ IDEMPOTENCY ] Cleared {count} cache entries")
        return count

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        self._cleanup_expired()

        total_entries = len(self._cache)
        oldest_age = 0.0

        if self._cache:
            oldest_timestamp = min(cached.timestamp for cached in self._cache.values())
            oldest_age = time.time() - oldest_timestamp

        return {
            'total_entries': total_entries,
            'ttl_seconds': self._ttl_seconds,
            'oldest_age_seconds': oldest_age,
            'cache_keys': list(self._cache.keys())
        }

    def __repr__(self) -> str:
        return f"<IdempotencyManager(entries={len(self._cache)}, ttl={self._ttl_seconds}s)>"
