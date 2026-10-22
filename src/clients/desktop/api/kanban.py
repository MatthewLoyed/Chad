from src.application.commands import (
    ListKanbanCardsCommand,
    CreateKanbanCardCommand,
    UpdateKanbanCardCommand,
    MoveKanbanCardCommand,
    DeleteKanbanCardCommand
)


class KanbanAPIMixin:
    """Mixin for personal kanban board WebView API methods."""

    def get_kanban_cards(self):
        """Get all kanban cards for board rendering."""
        return self._execute_command(ListKanbanCardsCommand(), timeout=5.0, default_fallback=[])

    def create_kanban_card(self, title, description=None, column_id="todo", order_index=0):
        """Create a new kanban card."""
        if not title or not title.strip():
            return {"success": False, "error": "Title cannot be empty"}
        card = self._execute_command(
            CreateKanbanCardCommand(title=title.strip(), description=description, column_id=column_id, order_index=order_index),
            timeout=5.0, default_fallback=None,
        )
        return {"success": card is not None, "card": card}

    def update_kanban_card(self, card_id, title=None, description=None):
        """Update a kanban card's title/description."""
        success = self._execute_command(
            UpdateKanbanCardCommand(card_id=card_id, title=title, description=description),
            timeout=5.0, default_fallback=False,
        )
        return {"success": success}

    def move_kanban_card(self, card_id, column_id, order_index=0):
        """Move a kanban card to a different column/position."""
        success = self._execute_command(
            MoveKanbanCardCommand(card_id=card_id, column_id=column_id, order_index=order_index),
            timeout=5.0, default_fallback=False,
        )
        return {"success": success}

    def delete_kanban_card(self, card_id):
        """Delete a kanban card."""
        success = self._execute_command(DeleteKanbanCardCommand(card_id=card_id), timeout=5.0, default_fallback=False)
        return {"success": success}
