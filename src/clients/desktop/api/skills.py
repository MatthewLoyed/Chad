from src.application.commands import (
    ListSkillsCatalogCommand,
    GetSkillContentCommand,
)
from src.shared.logger import setup_logger

logger = setup_logger("SkillsAPIMixin")


class SkillsAPIMixin:
    """Mixin for SKILL.md catalog browsing in the UI."""

    def get_skills_catalog(self):
        return self._execute_command(ListSkillsCatalogCommand(), timeout=10.0, default_fallback=[])

    def get_skill_content(self, skill_name):
        if not skill_name or not str(skill_name).strip():
            return {"success": False, "error": "skill_name is required"}
        return self._execute_command(
            GetSkillContentCommand(skill_name=str(skill_name).strip()),
            timeout=10.0,
            default_fallback={"success": False, "error": "Server unreachable"},
        )
