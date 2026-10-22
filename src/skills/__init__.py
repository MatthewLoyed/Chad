"""
Chad Skills Package
===================

Skills in this package implement the THREE-PRIMITIVE TAXONOMY:

  📖 Skill (SKILL.md)  — lightweight markdown expertise document
                          Loaded on demand into the supervisor's context.
                          No code executed. Stateless.

  🔧 Tool Call          — discrete executor, handled by ToolRegistry

  🤖 Subagent (agent.py) — heavyweight Python class that spawns its own
                            TextModeClient + ReAct loop for multi-step work.
                            These inherit from BaseSkill.

Directory structure per skill:
  src/skills/<skill_name>/
    SKILL.md     ← the Skill primitive (always exists)
    agent.py     ← the Subagent primitive (optional, for complex tasks)

Subagent imports (for direct use or registration):
"""

# Subagent classes (Python skill implementations)
from src.skills.homework_assistant.agent import HomeworkAssistantSkill
from src.skills.organize_folder.agent import OrganizeFolderSkill

__all__ = [
    "HomeworkAssistantSkill",
    "OrganizeFolderSkill",
]
