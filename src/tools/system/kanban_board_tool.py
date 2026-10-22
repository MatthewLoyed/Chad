"""
Kanban Board Tool
Lets the agent read the user's personal todo kanban board and update a card's
status as it works through tasks (e.g. moving a card to 'in_progress' when it
starts, 'done' when it finishes).
"""

from typing import Dict, Any, Optional
from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.domain.work_status import VALID_STATUS_VALUES

logger = setup_logger("KanbanBoardTool")

_COLUMN_LABELS = {
    "todo": "Todo",
    "in_progress": "In Progress",
    "needs_review": "Needs Review",
    "done": "Done",
}


class KanbanBoardTool(BaseTool):
    """
    Read-mostly access to the user's personal kanban board (Todo / In
    Progress / Needs Review / Done). The user adds tasks from the UI; this
    tool lets the agent see them and report progress back onto the board as
    it works — a dispatched card should move through the board, never just
    vanish from it.
    """

    name = "kanban_board"
    description = (
        "Read the user's personal todo kanban board, or update a card's column "
        "as you work on it. Actions:\n"
        "1. list: List cards, optionally filtered to one column.\n"
        "2. update_status: Move a card to a different column — 'in_progress' "
        "when you start working on it, 'needs_review' if it needs the user to "
        "check something, 'done' when finished.\n"
        "Columns: todo, in_progress, needs_review, done.\n"
        "When the user's message says it's from their kanban board (e.g. "
        "'Please complete this task from my kanban board ... (kanban "
        "card_id: <uuid>)'), that card_id is already known — call "
        "update_status with it directly, no need to list first. Move it to "
        "'in_progress' as soon as you start (it may already be there), and "
        "to 'needs_review' or 'done' once you finish. Never leave a "
        "dispatched card sitting in 'todo' — that's what makes it look like "
        "it vanished from the board."
    )
    category = "system"
    is_side_effecting = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
        from src.infrastructure.repositories.kanban_repository import KanbanRepository
        self.repo = KanbanRepository()

    @staticmethod
    def _column_label(column_id: Optional[str]) -> str:
        if not column_id:
            return "board"
        return _COLUMN_LABELS.get(column_id, column_id.replace("_", " ").title())

    def _card_title(self, card_id: Optional[str]) -> Optional[str]:
        if not card_id:
            return None
        rows = self.repo.supabase.query(
            self.repo.table,
            select="title",
            filters={"id": f"eq.{card_id}"},
            limit=1,
        )
        if rows:
            title = rows[0].get("title")
            return title if isinstance(title, str) and title.strip() else None
        return None

    def format_action(self, params: Dict[str, Any]) -> str:
        action = params.get("action", "")
        column = params.get("column")

        if action == "list":
            if column:
                return f"Listing {_COLUMN_LABELS.get(column, column)} cards"
            return "Listing kanban board"

        if action == "update_status":
            column_label = self._column_label(column)
            card_title = self._card_title(params.get("card_id"))
            if card_title:
                return f"Moving {card_title} to {column_label}"
            return f"Moving card to {column_label}"

        return "Updating kanban board"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "update_status"],
                        "description": "The operation to perform.",
                    },
                    "column": {
                        "type": "string",
                        "enum": sorted(VALID_STATUS_VALUES),
                        "description": "For 'list': filter to this column only. For 'update_status': the new column.",
                    },
                    "card_id": {
                        "type": "string",
                        "description": "Required for 'update_status' — the id of the card to move.",
                    },
                },
                "required": ["action"],
            },
        }

    async def execute(self, action: str, column: Optional[str] = None, card_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
            if action == "list":
                cards = await self.repo.list_cards(column_id=column)
                return {"success": True, "result": cards}

            if action == "update_status":
                if not card_id or not column:
                    return {"success": False, "error": "update_status requires both card_id and column."}
                ok = await self.repo.move_card(card_id, column_id=column)
                if not ok:
                    return {"success": False, "error": f"Failed to move card {card_id} to '{column}'."}
                return {"success": True, "result": f"Moved card {card_id} to '{column}'."}

            return {"success": False, "error": f"Unknown action: {action}"}
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"KanbanBoardTool error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
