"""Backward-compatible import — prefer ``src.buddy.BuddyEngine``."""

from src.buddy.engine import BuddyEngine

# Legacy alias
BuddyBridge = BuddyEngine

__all__ = ["BuddyBridge", "BuddyEngine"]
