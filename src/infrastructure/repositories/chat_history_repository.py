"""
Chat History Repository
Data access layer for persistent conversations and messages.

The local/cloud storage decision is made **once**, in ``__init__``, by selecting a
:class:`ChatHistoryBackend` strategy (see ``chat_history_backends.py``) — not re-derived by
an ``if not self.supabase.enabled:`` branch at the top of every method. Public methods do
backend-independent normalization (UUID extraction, base64 stripping, title derivation) and
then delegate, so there is a single place a given operation can diverge between local/cloud.
"""

import json
import re
import uuid
from typing import List, Dict, Any, Optional

from src.shared.logger import setup_logger
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.repositories.chat_history_backends import (
    LocalChatHistoryBackend,
    SupabaseChatHistoryBackend,
)

logger = setup_logger("ChatHistoryRepository")


class ChatHistoryRepository:
    """Repository for persistent conversations and messages, backed by one storage strategy.

    Uses Supabase when configured; otherwise a local sqlite store (issue #416) so chat
    history persists across restarts with zero setup. The local path has no cross-device
    sync — see local_store module docstring.
    """

    def __init__(self):
        self.supabase = get_supabase_manager()
        self._backend = (
            SupabaseChatHistoryBackend(self.supabase)
            if self.supabase.enabled
            else LocalChatHistoryBackend()
        )

    def _clean_id(self, conversation_id: str) -> str:
        """Extract a valid UUID from a prefixed session/conversation ID if present."""
        if not conversation_id:
            return conversation_id
        match = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", conversation_id)
        if match:
            return match.group(0)
        return conversation_id

    async def get_conversations(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch conversations with pagination; pinned first, then by updated_at DESC."""
        return await self._backend.get_conversations(limit, offset)

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single conversation by ID."""
        return await self._backend.get_conversation(self._clean_id(conversation_id))

    async def get_messages(self, conversation_id: str, include_archived: bool = True) -> List[Dict[str, Any]]:
        """Fetch all messages for a specific conversation ordered by created_at ASC."""
        return await self._backend.get_messages(self._clean_id(conversation_id), include_archived)

    async def archive_messages(self, message_ids: List[str]) -> bool:
        """Mark a list of messages (UUID strings) as archived. Returns True on success."""
        if not message_ids:
            return True
        return await self._backend.archive_messages(message_ids)

    async def update_message_metadata(self, message_id: str, updates: Dict[str, Any]) -> bool:
        """Merge `updates` into a message's metadata JSON (read-modify-write).

        Used to persist per-message state like the thumbs up/down `user_rating` badge so
        it survives a reload — both with Supabase and on the local sqlite backend (#470).
        """
        return await self._backend.update_message_metadata(self._clean_id(message_id), updates)

    async def create_conversation(self, model: Optional[str] = None, title: str = "New Chat",
                                  domain: Optional[str] = None) -> Dict[str, Any]:
        """Create a new conversation record, optionally scoped to a domain agent."""
        return await self._backend.create_conversation(model, title, domain)

    async def append_message(self, conversation_id: str, role: str, content: Any, metadata: Optional[Dict[str, Any]] = None, message_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Append a message to a conversation.
        Content can be a string or a list of blocks. Base64 data in content blocks is
        replaced with placeholders before storage (backend-independent).
        """
        cleaned_id = self._clean_id(conversation_id)
        processed_content = self._strip_base64(content)
        return await self._backend.append_message(
            cleaned_id, role, processed_content, metadata, self._safe_message_id(message_id)
        )

    @staticmethod
    def _safe_message_id(message_id: Optional[str]) -> Optional[str]:
        """Drop a client-supplied id that can't be a primary key, rather than losing the row.

        The id is written straight into the messages table's PK. Supabase types that column
        as `uuid`, so a non-UUID value makes Postgres 400 the whole INSERT — and because the
        user message is written WITHOUT an id (the DB generates one) while the assistant
        reply carries the turn_id, exactly one half of every turn survived. Chats on the PWA
        came back with the user's questions and none of Chad's answers.

        Local sqlite types the column TEXT, so it accepted anything and the bug was
        invisible in dev — it only appeared against the Supabase backend.

        Losing the client's correlation id is a far smaller cost than losing the message, so
        an unusable id is dropped (the backend then generates one) and logged loudly.
        """
        if not message_id:
            return None
        try:
            uuid.UUID(str(message_id))
            return message_id
        except (ValueError, AttributeError, TypeError):
            logger.warning(
                "[ HISTORY ] Ignoring non-UUID message_id %r — storing with a generated id "
                "so the message is not dropped.", message_id,
            )
            return None

    @staticmethod
    def _strip_base64(content: Any) -> str:
        """Replace base64 blobs in multimodal content blocks with placeholders and serialize
        non-string content to JSON, so history rows never carry raw image data."""
        if isinstance(content, list):
            processed_blocks = []
            for block in content:
                new_block = block.copy()
                b_type = block.get("type")
                # Nested source.data (multimodal system)
                source = block.get("source", {})
                if source and isinstance(source, dict) and "data" in source:
                    new_source = source.copy()
                    new_source["data"] = f"[{b_type} base64 omitted from history]"
                    new_block["source"] = new_source
                # Fallback for flat data field
                elif "data" in block:
                    new_block["data"] = f"[{b_type} base64 omitted from history]"
                processed_blocks.append(new_block)
            return json.dumps(processed_blocks)
        if not isinstance(content, str):
            return json.dumps(content)
        return content

    async def update_conversation_title(self, conversation_id: str, title: str) -> bool:
        """Update the title of a conversation."""
        return await self._backend.update_conversation_title(self._clean_id(conversation_id), title)

    async def update_conversation_pinned(self, conversation_id: str, is_pinned: bool) -> bool:
        """Pin or unpin a conversation in the sidebar."""
        return await self._backend.update_conversation_pinned(self._clean_id(conversation_id), is_pinned)

    async def update_conversation_project(self, conversation_id: str, project_path: Optional[str]) -> bool:
        """Associate a conversation with a workspace project path (or clear with None)."""
        return await self._backend.update_conversation_project(self._clean_id(conversation_id), project_path)

    async def update_conversation_domain(self, conversation_id: str, domain: Optional[str]) -> bool:
        """Scope a conversation to a domain agent (or clear with None)."""
        return await self._backend.update_conversation_domain(self._clean_id(conversation_id), domain)

    async def get_conversation_domain(self, conversation_id: str) -> Optional[str]:
        """Return the domain a conversation is scoped to, or None for the general tier.

        This is the authoritative source for turn scoping — the Brain resolves the domain
        from here rather than trusting a client-supplied field, so a stale or spoofed Limb
        payload can't pull another domain's memories into a turn.
        """
        conv = await self.get_conversation(conversation_id)
        return (conv or {}).get("domain") or None

    async def reassign_project_path(self, old_path: str, new_path: str) -> bool:
        """Retarget every conversation pointed at old_path to new_path (used on project rename)."""
        return await self._backend.reassign_project_path(old_path, new_path)

    async def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation. Cascade delete handles messages automatically."""
        return await self._backend.delete_conversation(self._clean_id(conversation_id))

    def derive_title(self, text: str) -> str:
        """
        Derive a title from the first ~60 chars of a message.
        Trims at the last word boundary before 60 chars.
        """
        if not text:
            return "New Chat"

        # Remove markdown/newlines for title
        clean_text = re.sub(r'[\n\r#*`>]', ' ', text).strip()

        if len(clean_text) <= 60:
            return clean_text

        # Find last space before 60 chars
        truncated = clean_text[:60]
        last_space = truncated.rfind(' ')

        if last_space > 20:  # Ensure we don't trim too much
            return truncated[:last_space].strip() + "..."
        return truncated.strip() + "..."

    async def search_chat_messages(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search for messages containing the query string."""
        if not query:
            return []
        return await self._backend.search_chat_messages(query, limit)

    async def search_conversations(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for conversations with titles matching the query string."""
        if not query:
            return []
        return await self._backend.search_conversations(query, limit)

    async def truncate_conversation_after(self, conversation_id: str, keep_count: Optional[int] = None) -> bool:
        """Archive messages in a conversation after the first `keep_count` messages.

        Used by prompt retry / regenerate flows to remove previous turn attempts.
        """
        cleaned_id = self._clean_id(conversation_id)
        return await self._backend.truncate_conversation_after(cleaned_id, keep_count)

