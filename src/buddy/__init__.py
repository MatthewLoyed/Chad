"""Desktop buddy — event-driven animations and ambient desk observations."""

from src.buddy.engine import BuddyEngine
from src.buddy.models import BuddyComment, BuddyCue, BuddySnapshot
from src.buddy.observer import BuddyObserver
from src.buddy.persona import build_buddy_persona_prompt
from src.buddy.rules import BuddyRegistry, BuddyRule
from src.buddy.triggers import BuddyTrigger, BuddyTriggerRegistry

__all__ = [
    "BuddyComment",
    "BuddyCue",
    "BuddyEngine",
    "BuddyObserver",
    "BuddyRegistry",
    "BuddyRule",
    "BuddySnapshot",
    "BuddyTrigger",
    "BuddyTriggerRegistry",
    "build_buddy_persona_prompt",
]
