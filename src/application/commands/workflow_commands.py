"""
Workflow Commands
Commands for managing user-defined workflows (automations): named, ordered
tool-call sequences triggered manually with one click.
"""

from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from src.application.commands.base_command import Command
from src.infrastructure.repositories.workflow_repository import WorkflowRepository


@dataclass
class ListWorkflowsCommand(Command):
    """Command to fetch all saved workflows."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        repo = WorkflowRepository()
        return await repo.list_workflows()


@dataclass
class CreateWorkflowCommand(Command):
    """Command to create a new workflow."""
    name: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    description: Optional[str] = None

    async def execute(self, facade) -> Optional[Dict[str, Any]]:
        repo = WorkflowRepository()
        return await repo.create_workflow(name=self.name, steps=self.steps, description=self.description)


@dataclass
class UpdateWorkflowCommand(Command):
    """Command to update a workflow's name/description/steps."""
    workflow_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None

    async def execute(self, facade) -> bool:
        repo = WorkflowRepository()
        return await repo.update_workflow(
            self.workflow_id, name=self.name, description=self.description, steps=self.steps,
        )


@dataclass
class DeleteWorkflowCommand(Command):
    """Command to delete a workflow."""
    workflow_id: str

    async def execute(self, facade) -> bool:
        repo = WorkflowRepository()
        return repo.delete_workflow(self.workflow_id)
