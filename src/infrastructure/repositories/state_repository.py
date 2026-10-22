"""
Supabase State Repository
Generic key-value store for system state and cursors.

Local-first (#493 tech debt): the table is registered with `LocalPostgrestShim` so the
same `supabase.query/update/insert` calls below persist to `data/local_chad.db` when
`SUPABASE_URL` isn't configured. Without this, sync watermarks (e.g. browser-history
incremental cursor) silently reset to their 24h default window on every restart.
"""

import logging
from typing import Any, Optional, Dict
from datetime import datetime

from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.database.local_postgrest import register_table

logger = setup_logger("StateRepository")

register_table(
    "system_state",
    columns={
        "key": "TEXT",
        "value": "JSON",
        "updated_at": "TEXT",
    },
    pk="key",
)

class StateRepository:
    """
    Repository for system state persistence using Supabase.
    Used for tracking cursors (like Gmail historyId), active agent states, etc.
    """

    def __init__(self):
        """Initialize repository."""
        self.supabase = get_supabase_manager()
        self.table = "system_state"

    def get_value(self, key: str, default: Any = None) -> Any:
        """
        Get a state value by key.
        
        Args:
            key: State key name.
            default: Default value if key not found.
            
        Returns:
            The stored value (deserialized from JSONB) or default.
        """
        try:
            res = self.supabase.query(
                self.table, 
                select="value", 
                filters={"key": f"eq.{key}"}, 
                limit=1
            )
            if res and "value" in res[0]:
                return res[0]["value"]
            return default
        except Exception as e:
            logger.error(f"Failed to fetch state for key '{key}': {e}")
            return default

    def set_value(self, key: str, value: Any) -> bool:
        """
        Set or update a state value.
        
        Args:
            key: State key name.
            value: Value to store (must be JSON serializable).
            
        Returns:
            True if successful.
        """
        try:
            now = datetime.now().isoformat()
            data = {
                "key": key,
                "value": value,
                "updated_at": now
            }
            
            # Upsert logic: check if exists
            existing = self.supabase.query(self.table, select="key", filters={"key": f"eq.{key}"}, limit=1)
            
            if existing:
                self.supabase.update(self.table, filters={"key": f"eq.{key}"}, data=data)
            else:
                self.supabase.insert(self.table, data)
                
            logger.info(f"Updated system state: {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to set state for key '{key}': {e}")
            return False

    def delete_value(self, key: str) -> bool:
        """Delete a state record."""
        try:
            # We don't have a direct delete in SupabaseManager yet based on previous views?
            # Let's check SupabaseManager if it has delete. Assuming it doesn't, we can skip or implement.
            # For now, let's just use update with empty or similar.
            logger.warning(f"Delete state for key '{key}' called but not implemented.")
            return False
        except Exception as e:
            logger.error(f"Failed to delete state for key '{key}': {e}")
            return False
