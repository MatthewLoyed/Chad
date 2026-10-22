"""
Supabase Workflow Repository

Data access layer for user-defined "workflows" (automations): a named,
ordered sequence of tool calls the user can trigger with one click. Each step
is {tool_name, params, label}. No scheduling/branching — manual trigger only.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from src.shared.logger import setup_logger
from src.infrastructure.database.local_postgrest import register_table
from src.infrastructure.database.supabase_manager import get_supabase_manager

logger = setup_logger("WorkflowRepository")


class WorkflowRepository:
    """Repository for workflow persistence operations. Uses Supabase."""

    def __init__(self):
        register_table(
            "workflows",
            columns={
                "id": "INTEGER",
                "name": "TEXT",
                "description": "TEXT",
                "steps": "JSON",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
            pk="id",
            json_columns=["steps"],
        )
        self.supabase = get_supabase_manager()
        self.table = "workflows"

    async def list_workflows(self) -> List[Dict[str, Any]]:
        return await self.supabase.async_query(self.table, order="created_at.desc")

    async def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        rows = await self.supabase.async_query(self.table, filters={"id": f"eq.{workflow_id}"}, limit=1)
        return rows[0] if rows else None

    async def create_workflow(
        self, name: str, steps: List[Dict[str, Any]], description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        data = {"name": name, "description": description, "steps": steps}
        return await self.supabase.async_insert(self.table, data)

    async def update_workflow(
        self, workflow_id: str, name: Optional[str] = None,
        description: Optional[str] = None, steps: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if steps is not None:
            data["steps"] = steps
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return await self.supabase.async_update(self.table, filters={"id": f"eq.{workflow_id}"}, data=data)

    def delete_workflow(self, workflow_id: str) -> bool:
        return self.supabase.delete(self.table, filters={"id": f"eq.{workflow_id}"})
