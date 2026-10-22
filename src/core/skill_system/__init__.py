"""Skill system package — manages Chad's Skill (SKILL.md) primitive."""
from .skill_loader import SkillLoader, SkillEntry, get_skill_loader

__all__ = ["SkillLoader", "SkillEntry", "get_skill_loader"]
