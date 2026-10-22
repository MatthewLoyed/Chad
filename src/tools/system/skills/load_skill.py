"""
load_skill — loads a SKILL.md document into the supervisor's context.

When the supervisor calls this tool, the full SKILL.md content is returned
as the tool result. LangGraph's ToolNode places this in a ToolMessage which
the supervisor sees in its next reasoning step.

The loaded content becomes part of the supervisor's ChadState via the
'loaded_skill' state field, which is also passed down to subagents via
handoff context so specialists can benefit from the domain guidance too.

Progressive Disclosure (three stages):
  Step 1 - Discovery:  Supervisor sees name + description in system prompt (~50 tokens)
  Step 2 - Activation: Supervisor calls load_skill() → full SKILL.md loaded (~500-2000 tokens)
  Step 3 - Execution:  Supervisor follows the SOP, or delegates to the linked subagent
"""

from typing import Dict, Any
from src.core.skill_system.skill_allowlist import ALLOWED_SKILLS
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("LoadSkillTool")


class LoadSkillTool(BaseTool):
    """Load the full SKILL.md document for a given skill name into the agent context."""

    name = "load_skill"
    description = (
        "Loads a SKILL.md workflow guide for complex multi-step tasks "
        "(e.g. homework, document formatting). "
        "Do NOT use for MCP tools or web search — quick fact → web_search; deep multi-item "
        "research → delegate_to_deep_research_agent. Use list_skills() first to see valid names."
    )

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The name of the skill to load (e.g. 'homework_assistant', 'organize_folder'). Use list_skills to see available options. DO NOT pass subagent names (such as 'workspace_agent', 'browser_agent', etc.) to this tool."
                    }
                },
                "required": ["skill_name"]
            }
        }

    async def execute(self, skill_name: str, **kwargs) -> Dict[str, Any]:
        if skill_name not in ALLOWED_SKILLS:
            logger.warning(f"[ SECURITY ] : Rejected attempt to load disallowed skill: {skill_name}")
            return {
                "success": False,
                "error": f"Access denied: Skill '{skill_name}' is not in the allowlist of approved skills.",
                "suggestion": "Call list_skills() to see available approved skills."
            }

        try:
            from src.core.skill_system.skill_loader import get_skill_loader
            loader = get_skill_loader()

            if not loader.is_empty:
                pass
            else:
                await loader.initialize()

            content = await loader.load_skill_content(skill_name)
            entry = loader.get_entry(skill_name)

            return {
                "success": True,
                "skill_name": skill_name,
                "result": content,
                # Structured metadata for the supervisor to use
                "_skill_metadata": {
                    "name": skill_name,
                    "subagent": entry.subagent if entry else None,
                    "tags": entry.tags if entry else [],
                },
                # Signal to ChadState to store loaded skill content
                "_loaded_skill": skill_name,
            }

        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "suggestion": "Call list_skills() to see available skill names."
            }
        except FileNotFoundError as e:
            logger.error(f"[ LOAD_SKILL ] File not found: {e}")
            return {
                "success": False,
                "error": f"Skill file missing: {e}. Contact admin to resync."
            }
        except Exception as e:
            logger.error(f"[ LOAD_SKILL ] Error loading '{skill_name}': {e}", exc_info=True)
            return {"success": False, "error": str(e)}
