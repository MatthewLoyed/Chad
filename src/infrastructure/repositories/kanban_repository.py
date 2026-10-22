"""
Supabase Kanban Repository

Data access layer for the personal todo kanban board (Todo / In Progress /
Needs Review / Done — see src/domain/work_status.py for the shared status
model). Single-user app — no per-user scoping.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from src.shared.logger import setup_logger
from src.infrastructure.database.local_postgrest import register_table
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.domain.work_status import (
    DEFAULT_STATUS,
    VALID_STATUS_VALUES,
    assert_valid_transition,
)

logger = setup_logger("KanbanRepository")


class KanbanRepository:
    """Repository for kanban card persistence operations. Uses Supabase."""

    def __init__(self):
        register_table(
            "kanban_cards",
            columns={
                "id": "INTEGER",
                "title": "TEXT",
                "description": "TEXT",
                "column_id": "TEXT",
                "order_index": "INTEGER",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
            pk="id",
        )
        self.supabase = get_supabase_manager()
        self.table = "kanban_cards"

    async def list_cards(self, column_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all cards, optionally filtered to one column. Ordered for board rendering."""
        filters = {}
        if column_id:
            filters["column_id"] = f"eq.{column_id}"
        rows = await self.supabase.async_query(
            self.table, filters=filters, order="column_id.asc,order_index.asc",
        )
        return rows

    async def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        rows = await self.supabase.async_query(self.table, filters={"id": f"eq.{card_id}"}, limit=1)
        return rows[0] if rows else None

    async def create_card(
        self, title: str, description: Optional[str] = None,
        column_id: str = DEFAULT_STATUS.value, order_index: int = 0,
    ) -> Optional[Dict[str, Any]]:
        if column_id not in VALID_STATUS_VALUES:
            raise ValueError(f"Invalid column_id '{column_id}'. Must be one of {sorted(VALID_STATUS_VALUES)}.")
        data = {
            "title": title,
            "description": description,
            "column_id": column_id,
            "order_index": order_index,
        }
        return await self.supabase.async_insert(self.table, data)

    async def update_card(self, card_id: str, **fields: Any) -> bool:
        """Partial update. Pass only the fields that changed (title, description,
        column_id, order_index). For status changes prefer move_card, which
        validates the transition against the work-status state machine."""
        if "column_id" in fields and fields["column_id"] not in VALID_STATUS_VALUES:
            raise ValueError(f"Invalid column_id '{fields['column_id']}'. Must be one of {sorted(VALID_STATUS_VALUES)}.")
        data = {k: v for k, v in fields.items() if v is not None or k == "description"}
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return await self.supabase.async_update(self.table, filters={"id": f"eq.{card_id}"}, data=data)

    async def move_card(self, card_id: str, column_id: str, order_index: int = 0) -> bool:
        """Move a card to a new status/column, validated against the shared
        todo -> in_progress -> needs_review -> done state machine."""
        card = await self.get_card(card_id)
        if card is None:
            raise ValueError(f"No kanban card with id '{card_id}'.")
        assert_valid_transition(card["column_id"], column_id)
        return await self.update_card(card_id, column_id=column_id, order_index=order_index)

    def delete_card(self, card_id: str) -> bool:
        return self.supabase.delete(self.table, filters={"id": f"eq.{card_id}"})
