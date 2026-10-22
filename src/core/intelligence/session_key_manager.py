"""
Session Key Manager - Canonical Session Identification

Implements OpenClaw-inspired session key format: {scope}:{account}:{channel}:{peer}
Enables proper conversation isolation and future multi-channel support.

Pattern adopted from OpenClaw's session management architecture.
"""

import logging
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("SessionKeyManager")


@dataclass
class SessionKey:
    """
    Parsed session key components.

    Format: {scope}:{account}:{channel}:{peer}

    Examples:
        - "main:default:gui:user" (default GUI session)
        - "work:john:telegram:personal" (Telegram channel, work scope)
        - "personal:jane:discord:server123" (Discord server)
    """
    scope: str      # Context scope (main, work, personal, etc.)
    account: str    # User account identifier
    channel: str    # Communication channel (gui, telegram, discord, etc.)
    peer: str       # Peer identifier (user, server ID, chat ID, etc.)

    def to_string(self) -> str:
        """Convert to canonical string format."""
        return f"{self.scope}:{self.account}:{self.channel}:{self.peer}"

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary."""
        return {
            'scope': self.scope,
            'account': self.account,
            'channel': self.channel,
            'peer': self.peer
        }


class SessionKeyManager:
    """
    Manages session key encoding and decoding.

    Provides utilities for:
    - Generating canonical session keys
    - Parsing session key components
    - Mapping session keys to storage paths
    - Validating session key format
    """

    DEFAULT_SESSION_KEY = "main:default:gui:user"

    @staticmethod
    def build_key(
        scope: str = "main",
        account: str = "default",
        channel: str = "gui",
        peer: str = "user"
    ) -> str:
        """
        Build a canonical session key.

        Args:
            scope: Context scope (main, work, personal, etc.)
            account: User account identifier
            channel: Communication channel (gui, telegram, discord, etc.)
            peer: Peer identifier

        Returns:
            Canonical session key string

        Example:
            >>> SessionKeyManager.build_key("work", "john", "telegram", "chat123")
            "work:john:telegram:chat123"
        """
        # Validate components (no colons allowed)
        for component in [scope, account, channel, peer]:
            if ':' in component:
                raise ValueError(f"Session key component cannot contain colons: {component}")

        return f"{scope}:{account}:{channel}:{peer}"

    @staticmethod
    def parse_key(session_key: str) -> SessionKey:
        """
        Parse a session key into components.

        Args:
            session_key: Canonical session key string

        Returns:
            SessionKey dataclass with parsed components

        Raises:
            ValueError: If session key format is invalid

        Example:
            >>> key = SessionKeyManager.parse_key("work:john:telegram:chat123")
            >>> key.channel
            "telegram"
        """
        parts = session_key.split(':')

        if len(parts) != 4:
            raise ValueError(
                f"Invalid session key format: {session_key}. "
                f"Expected 'scope:account:channel:peer', got {len(parts)} parts"
            )

        return SessionKey(
            scope=parts[0],
            account=parts[1],
            channel=parts[2],
            peer=parts[3]
        )

    @staticmethod
    def get_memory_path(session_key: str, base_dir: str) -> str:
        """
        Map session key to database file path.

        Creates isolated memory databases per session for proper conversation isolation.

        Args:
            session_key: Canonical session key
            base_dir: Base directory for memory storage

        Returns:
            Path to session-specific database file

        Example:
            >>> SessionKeyManager.get_memory_path("work:john:telegram:chat123", "/data/memory")
            "/data/memory/work/john/telegram_chat123.db"
        """
        try:
            parsed = SessionKeyManager.parse_key(session_key)

            # Create directory structure: base_dir/scope/account/
            session_dir = Path(base_dir) / parsed.scope / parsed.account

            # Database filename: channel_peer.db
            db_filename = f"{parsed.channel}_{parsed.peer}.db"

            return str(session_dir / db_filename)

        except ValueError as e:
            logger.error(f"Failed to map session key to path: {e}")
            # Fallback to default path
            return str(Path(base_dir) / "main.db")

    @staticmethod
    def validate_key(session_key: str) -> Tuple[bool, Optional[str]]:
        """
        Validate session key format.

        Args:
            session_key: Session key to validate

        Returns:
            Tuple of (is_valid, error_message)

        Example:
            >>> SessionKeyManager.validate_key("main:default:gui:user")
            (True, None)
            >>> SessionKeyManager.validate_key("invalid")
            (False, "Invalid session key format...")
        """
        try:
            SessionKeyManager.parse_key(session_key)
            return True, None
        except ValueError as e:
            return False, str(e)

    @staticmethod
    def is_default_session(session_key: str) -> bool:
        """
        Check if this is the default session.

        Args:
            session_key: Session key to check

        Returns:
            True if this is the default GUI session
        """
        return session_key == SessionKeyManager.DEFAULT_SESSION_KEY

    @staticmethod
    def get_channel(session_key: str) -> Optional[str]:
        """
        Extract channel from session key.

        Args:
            session_key: Session key

        Returns:
            Channel name or None if invalid
        """
        try:
            parsed = SessionKeyManager.parse_key(session_key)
            return parsed.channel
        except ValueError:
            return None

    @staticmethod
    def get_scope(session_key: str) -> Optional[str]:
        """
        Extract scope from session key.

        Args:
            session_key: Session key

        Returns:
            Scope name or None if invalid
        """
        try:
            parsed = SessionKeyManager.parse_key(session_key)
            return parsed.scope
        except ValueError:
            return None


# Convenience functions for backward compatibility

def build_session_key(
    scope: str = "main",
    account: str = "default",
    channel: str = "gui",
    peer: str = "user"
) -> str:
    """Build a session key (convenience function)."""
    return SessionKeyManager.build_key(scope, account, channel, peer)


def parse_session_key(session_key: str) -> SessionKey:
    """Parse a session key (convenience function)."""
    return SessionKeyManager.parse_key(session_key)
