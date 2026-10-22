"""
list_skills — returns the skill catalog to the supervisor.

This is a CORE tool (always injected). It lets the supervisor discover
what Skill (SKILL.md) documents are available before deciding to activate one.

Decision:  task needs domain guidance?
  → call list_skills() to see what's available
  → then call load_skill(name) to activate it
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("ListSkillsTool")


class ListSkillsTool(BaseTool):
    """List all available SKILL.md skills and their short descriptions."""

    name = "list_skills"
    description = (
        "Lists loadable SKILL.md workflow guides (not MCP tools). "
        "Use before load_skill for complex SOPs. "
        "For web search, email, files, etc. use tools directly — do not load_skill."
    )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "tag_filter": {
                        "type": "string",
                        "description": "Optional tag to filter skills (e.g. 'writing', 'calendar', 'productivity')."
                    }
                },
                "required": []
            }
        }

    async def execute(self, tag_filter: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            from src.core.skill_system.skill_loader import get_skill_loader
            loader = get_skill_loader()

            if not loader.is_empty:
                skills = loader.list_skills()
            else:
                await loader.initialize()
                skills = loader.list_skills()

            if tag_filter:
                skills = [s for s in skills if tag_filter.lower() in [t.lower() for t in (s.get("tags") or [])]]

            if not skills:
                return {
                    "success": True,
                    "result": f"No skills found{f' with tag: {tag_filter}' if tag_filter else ''}.",
                    "skills": []
                }

            formatted = "\n".join([
                f"• **{s['name']}** (v{s['version']})"
                + (f" [{', '.join(s['tags'])}]" if s['tags'] else "")
                + f": {s['description']}"
                + (f"\n  → Delegates to subagent: `{s['subagent']}`" if s.get("subagent") else "")
                for s in skills
            ])

            return {
                "success": True,
                "result": f"Available skills ({len(skills)} total):\n\n{formatted}\n\nUse `load_skill(skill_name)` to activate one.",
                "skills": skills
            }

        except Exception as e:
            logger.error(f"[ LIST_SKILLS ] Error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
