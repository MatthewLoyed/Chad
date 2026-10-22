"""
Kanban Commands
Commands for managing the personal todo kanban board (Backlog / Todo / In
Progress / Review / Done).
"""

from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from src.application.commands.base_command import Command
from src.infrastructure.repositories.kanban_repository import KanbanRepository
from src.domain.work_status import DEFAULT_STATUS


@dataclass
class ListKanbanCardsCommand(Command):
    """Command to fetch all kanban cards."""

    async def execute(self, facade) -> List[Dict[str, Any]]:
        repo = KanbanRepository()
        return await repo.list_cards()


@dataclass
class CreateKanbanCardCommand(Command):
    """Command to create a new kanban card."""
    title: str
    description: Optional[str] = None
    column_id: str = DEFAULT_STATUS.value
    order_index: int = 0

    async def execute(self, facade) -> Optional[Dict[str, Any]]:
        repo = KanbanRepository()
        return await repo.create_card(
            title=self.title, description=self.description,
            column_id=self.column_id, order_index=self.order_index,
        )


@dataclass
class UpdateKanbanCardCommand(Command):
    """Command to update a kanban card's title/description."""
    card_id: str
    title: Optional[str] = None
    description: Optional[str] = None

    async def execute(self, facade) -> bool:
        repo = KanbanRepository()
        fields = {}
        if self.title is not None:
            fields["title"] = self.title
        if self.description is not None:
            fields["description"] = self.description
        return await repo.update_card(self.card_id, **fields)


@dataclass
class MoveKanbanCardCommand(Command):
    """Command to move a kanban card to a different column/position."""
    card_id: str
    column_id: str
    order_index: int = 0

    async def execute(self, facade) -> bool:
        repo = KanbanRepository()
        return await repo.move_card(self.card_id, self.column_id, self.order_index)


@dataclass
class DeleteKanbanCardCommand(Command):
    """Command to delete a kanban card."""
    card_id: str

    async def execute(self, facade) -> bool:
        repo = KanbanRepository()
        return repo.delete_card(self.card_id)
