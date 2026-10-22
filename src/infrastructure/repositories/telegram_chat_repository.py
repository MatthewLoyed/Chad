"""
Telegram Chat Repository

Persists chat_id -> session_key mappings and user metadata, via Supabase when
configured or the local SQLite shim otherwise (issue #471, part of #462).
Enables conversation isolation per Telegram chat.
All methods are synchronous — SupabaseManager now uses httpx.Client (sync).

Boundary: the Telegram bot API + webhook are inherently cloud — they need
internet and a bot token, and can never run purely local. What *is* local is
this routing state (chat_id <-> session_key + user metadata): with a bot token
configured but no `SUPABASE_URL`, inbound messages still route to the right
session using `data/local_chad.db`, surviving a Brain restart.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass

from src.shared.logger import setup_logger
from src.core.intelligence.session_key_manager import SessionKeyManager
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.database.local_postgrest import register_table

logger = setup_logger("TelegramChatRepository")


@dataclass
class TelegramChat:
    """Represents a Telegram chat mapping."""
    chat_id: int
    user_id: int
    session_key: str
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    last_message_at: datetime
    created_at: datetime
    is_active: bool


class TelegramChatRepository:
    """
    Repository for Telegram chat mappings using Supabase.
    All methods are synchronous (no async, no nest_asyncio).
    """

    def __init__(self):
        """Initialize repository."""
        self.supabase = get_supabase_manager()
        self.table = "telegram_chats"
        register_table(
            self.table,
            columns={
                "chat_id": "INTEGER",
                "user_id": "INTEGER",
                "session_key": "TEXT",
                "username": "TEXT",
                "first_name": "TEXT",
                "last_name": "TEXT",
                "last_message_at": "TEXT",
                "created_at": "TEXT",
                "is_active": "BOOLEAN",
            },
            pk="chat_id",
        )

    def get_session_key(self, chat_id: int) -> Optional[str]:
        """Get session key for a chat."""
        res = self.supabase.query(self.table, select="session_key", filters={"chat_id": f"eq.{chat_id}"}, limit=1)
        return res[0]["session_key"] if res else None

    def get_chat_id(self, session_key: str) -> Optional[int]:
        """Get chat ID for a session key."""
        res = self.supabase.query(self.table, select="chat_id", filters={"session_key": f"eq.{session_key}"}, limit=1)
        return res[0]["chat_id"] if res else None

    def create_or_update_chat(
        self,
        chat_id: int,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> str:
        """Create or update chat mapping. Returns session_key."""
        existing = self.supabase.query(self.table, select="session_key", filters={"chat_id": f"eq.{chat_id}"}, limit=1)

        now = datetime.now().isoformat()
        data = {
            "chat_id": chat_id,
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "last_message_at": now,
            "is_active": True
        }

        if existing:
            self.supabase.update(self.table, filters={"chat_id": f"eq.{chat_id}"}, data=data)
            return existing[0]["session_key"]
        else:
            session_key = SessionKeyManager.build_key(
                scope="main",
                account=str(user_id),
                channel="telegram",
                peer=str(chat_id)
            )
            data["session_key"] = session_key
            data["created_at"] = now
            self.supabase.insert(self.table, data)
            return session_key

    def get_chat(self, chat_id: int) -> Optional[TelegramChat]:
        """Get full chat details."""
        res = self.supabase.query(self.table, filters={"chat_id": f"eq.{chat_id}"}, limit=1)
        if not res:
            return None
        row = res[0]
        return TelegramChat(
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            session_key=row["session_key"],
            username=row.get("username"),
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            last_message_at=datetime.fromisoformat(row["last_message_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            is_active=row["is_active"]
        )

    def get_active_chats(self) -> List[TelegramChat]:
        """Get all active chats ordered by last message time."""
        res = self.supabase.query(self.table, filters={"is_active": "eq.true"})
        res.sort(key=lambda x: x["last_message_at"], reverse=True)
        return [
            TelegramChat(
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                session_key=row["session_key"],
                username=row.get("username"),
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                last_message_at=datetime.fromisoformat(row["last_message_at"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                is_active=row["is_active"]
            )
            for row in res
        ]

    def get_default_chat_id(self) -> Optional[int]:
        """Get default chat ID."""
        chats = self.get_active_chats()
        return chats[0].chat_id if chats else None

    def deactivate_chat(self, chat_id: int):
        """Mark chat as inactive."""
        self.supabase.update(self.table, filters={"chat_id": f"eq.{chat_id}"}, data={"is_active": False})

    def get_stats(self) -> Dict[str, Any]:
        """Get stats."""
        all_chats = self.supabase.query(self.table, select="chat_id,user_id,is_active")
        return {
            "total_chats": len(all_chats),
            "active_chats": len([c for c in all_chats if c["is_active"]]),
            "unique_users": len(set(c["user_id"] for c in all_chats))
        }
