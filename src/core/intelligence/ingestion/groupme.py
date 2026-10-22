"""
GroupMe Ingestor
Pulls, normalizes, and indexes recent GroupMe group chat messages into
Chad's Unified Personal Knowledge / Vector Base (personal_index).
"""

import os
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from src.core.intelligence.ingestion.base import BaseIngestor
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("GroupMeIngestor")


class GroupMeIngestor(BaseIngestor):
    """
    Ingests messages from the user's GroupMe group chats.
    Read-only integration: only observes and structures chat history for personal context.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        max_messages_per_group: int = 40,
        lookback_days_on_first_sync: int = 7,
    ):
        self._explicit_token = token
        self.max_messages_per_group = max_messages_per_group
        self.lookback_days_on_first_sync = lookback_days_on_first_sync

    def get_source_name(self) -> str:
        return "groupme"

    def _resolve_token(self) -> Optional[str]:
        """Resolve GroupMe access token from explicit arg, ConfigManager, or env."""
        if self._explicit_token:
            return self._explicit_token.strip()
        
        token = (
            ConfigManager.get_secret("groupme.access_token")
            or os.getenv("GROUPME_ACCESS_TOKEN")
        )
        return token.strip() if token else None

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Fetch new messages across all user groups since last_sync.
        """
        token = self._resolve_token()
        if not token:
            logger.debug("[ GroupMeIngestor ] No GROUPME_ACCESS_TOKEN configured; skipping ingest.")
            return []

        logger.info(f"[ GroupMeIngestor ] Starting GroupMe ingestion (last_sync={last_sync})...")
        
        headers = {
            "X-Access-Token": token,
            "User-Agent": "ChadAI/1.0",
        }

        # 1. Fetch active groups
        groups = self._fetch_groups(headers)
        if not groups:
            logger.info("[ GroupMeIngestor ] No accessible groups found.")
            return []

        # Determine cutoff timestamp
        if last_sync:
            if not last_sync.tzinfo:
                last_sync = last_sync.replace(tzinfo=timezone.utc)
            cutoff_timestamp = int(last_sync.timestamp())
        else:
            # First sync: look back max N days
            oldest_dt = datetime.now(timezone.utc) - timedelta(days=self.lookback_days_on_first_sync)
            cutoff_timestamp = int(oldest_dt.timestamp())

        normalized_items: List[Dict[str, Any]] = []

        # 2. Ingest messages per group
        for group in groups:
            group_id = str(group.get("id"))
            group_name = group.get("name", "Group")
            
            try:
                group_items = self._ingest_group_messages(
                    group_id=group_id,
                    group_name=group_name,
                    cutoff_timestamp=cutoff_timestamp,
                    headers=headers
                )
                normalized_items.extend(group_items)
            except Exception as e:
                logger.warning(
                    f"[ GroupMeIngestor ] Failed to ingest group '{group_name}' ({group_id}): {e}"
                )

        logger.info(
            f"[ GroupMeIngestor ] Ingestion complete. Prepared {len(normalized_items)} message(s) across {len(groups)} group(s)."
        )
        return normalized_items

    def _fetch_groups(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch all groups the user belongs to (up to 50)."""
        url = "https://api.groupme.com/v3/groups?per_page=50&omit=memberships"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", []) or []
        except Exception as e:
            logger.error(f"[ GroupMeIngestor ] Error fetching groups: {e}")
            return []

    def _ingest_group_messages(
        self,
        group_id: str,
        group_name: str,
        cutoff_timestamp: int,
        headers: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Fetch and normalize messages for a single group."""
        url = f"https://api.groupme.com/v3/groups/{group_id}/messages?limit={self.max_messages_per_group}"
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            messages = data.get("response", {}).get("messages", []) or []

        items: List[Dict[str, Any]] = []

        for m in messages:
            created_at = m.get("created_at")
            if not created_at or created_at <= cutoff_timestamp:
                continue

            text = (m.get("text") or "").strip()
            sender = m.get("name") or "Unknown"
            msg_id = str(m.get("id"))
            system = bool(m.get("system", False))

            # Handle attachments if no plain text
            attachments = m.get("attachments") or []
            if not text:
                if attachments:
                    att_types = [a.get("type", "attachment") for a in attachments if isinstance(a, dict)]
                    text = f"[{', '.join(att_types)}]"
                else:
                    # Skip empty messages / system membership joins
                    continue

            # Format natural context string for vector search
            if system:
                content_string = f"GroupMe system notification in '{group_name}': {text}"
                importance = 0.2
            else:
                content_string = f"GroupMe in '{group_name}' from {sender}: {text}"
                importance = 0.45

            msg_dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
            item_hash = self.compute_hash(f"groupme_{group_id}_{msg_id}")

            items.append({
                "content": content_string,
                "importance": importance,
                "metadata": {
                    "source": "groupme",
                    "type": "group_message",
                    "group_id": group_id,
                    "group_name": group_name,
                    "message_id": msg_id,
                    "author": sender,
                    "sender_id": str(m.get("sender_id", "")),
                    "timestamp": msg_dt.isoformat(),
                    "hash": item_hash,
                }
            })

        return items
