"""
Manage Persona Rules Tool - Autonomous Persona Update Tool

Enables the agent to edit its own persona fragments. As of #589 those are local
files (`config/persona/*.md`, overriding the `prompts/persona/*.md` baseline) —
not Supabase rows — so every description below says "local" rather than the old
"cloud". The model reasons about these strings, so leaving them stale would have
it believe it is writing somewhere it isn't.

Writes go through MemoryRepository.update_persona_config -> persona_store, which
records a version in local history first, so an edit made here is revertable via
persona_store.rollback().
"""

import os
from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("ManagePersonaRules")

class ManagePersonaRules(BaseTool):
    """
    Tool for autonomously updating modular persona files (agent rules/personality).
    """
    
    name = "manage_persona_rules"
    description = (
        "COMMAND: Use this tool to evolve your persona or remember durable meta-rules by editing your local persona files.\n"
        "1. append: Add content to the end of a persona fragment (e.g., adding a new truth to soul or a new rule to agent)\n"
        "2. write: Overwrite the entire fragment (use for structural updates or cleanups)\n"
        "3. read: Inspect the current content of a persona fragment.\n"
        "Persona IDs: agent (core behavior), identity (who you are), soul (deep values), tools (tool usage rules), heartbeat (background-loop behavior).\n"
        "Facts about the USER do not belong here — save those with manage_user_memory, which writes the knowledge graph."
    )
    category = "system"

    # `user` is deliberately absent (#589): it was retired in favour of the knowledge
    # graph, and persona_store refuses writes to it. Advertising it would just make the
    # model attempt an edit that silently does nothing.
    ALLOWED_IDS = ["agent", "identity", "soul", "tools", "heartbeat"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
        from src.infrastructure.repositories.memory_repository import MemoryRepository
        self.repo = MemoryRepository()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Evolve your internal persona records and system rules (local persona files). Use 'append' for new rules, 'write' for full updates, or 'read' to verify context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["append", "write", "read"],
                        "description": "The operation to perform."
                    },
                    "persona_id": {
                        "type": "string",
                        "description": f"The persona component to target. Allowed: {', '.join(self.ALLOWED_IDS)}"
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to add or write (optional for 'read')."
                    }
                },
                "required": ["action", "persona_id"]
            }
        }
    
    async def execute(self, action: str, persona_id: str, content: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        # Normalize ID to lowercase
        config_id = persona_id.lower().replace(".md", "")
        
        if config_id not in self.ALLOWED_IDS:
            return {"success": False, "error": f"ID '{config_id}' is not a protected persona record."}
            
        try:
            if action == "read":
                local_content = self.repo.get_persona_config(config_id)
                if local_content:
                    return {"success": True, "result": local_content, "source": "local"}
                else:
                    return {"success": False, "error": f"Persona fragment '{config_id}' does not exist."}
                    
            elif action == "append":
                if content is None:
                    return {"success": False, "error": "Content is required for 'append' action."}
                
                current = self.repo.get_persona_config(config_id)
                if current is None:
                    current = ""

                new_content = current + ("\n" if current and not current.endswith('\n') else "") + content
                self.repo.update_persona_config(config_id, new_content)
                
                return {"success": True, "result": f"Successfully evolved {config_id} rules."}
                
            elif action == "write":
                if content is None:
                    return {"success": False, "error": "Content is required for 'write' action."}
                    
                self.repo.update_persona_config(config_id, content)
                return {"success": True, "result": f"Successfully updated persona fragment {config_id}."}
                
            else:
                return {"success": False, "error": f"Invalid action: {action}"}
                
        except Exception as e:
            logger.error(f"Persona Update Error: {e}")
            return {"success": False, "error": str(e)}

    def validate_params(self, action: str = None, persona_id: str = None, **kwargs) -> tuple[bool, str | None]:
        if not action or not persona_id:
            return False, "action and persona_id are required"
        return True, None
