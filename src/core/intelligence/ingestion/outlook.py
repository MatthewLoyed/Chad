"""
Outlook Desktop (COM / MAPI) Ingestor
Extracts, normalizes, and indexes school/work emails from the local Windows
Classic Outlook MAPI profile into Chad's Unified Personal Knowledge Base (personal_index).
Bypasses tenant admin consent blocks by leveraging the already-authenticated local session.
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from src.core.intelligence.ingestion.base import BaseIngestor
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("OutlookIngestor")


def _clean_body_snippet(body: str, max_chars: int = 250) -> str:
    """Normalize body whitespace and truncate to a clean searchable snippet."""
    if not body:
        return ""
    cleaned = " ".join(body.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars - 3].rstrip() + "..."


class OutlookIngestor(BaseIngestor):
    """
    Ingests emails from local Classic Outlook desktop profiles (e.g., msstate.edu).
    Runs safely on Windows via win32com.client with apartment initialization.
    """

    def __init__(self, target_account_keyword: Optional[str] = None, max_messages: int = 40):
        self.target_account_keyword = target_account_keyword
        self.max_messages = max_messages

    def get_source_name(self) -> str:
        return "outlook"

    def is_available(self) -> bool:
        """Check if running on Windows and Outlook COM is available."""
        if sys.platform != "win32":
            return False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                import win32com.client
                outlook = win32com.client.Dispatch("Outlook.Application")
                return outlook is not None
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            return False

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Harvest recent emails from the primary / school account inbox since last_sync.
        """
        if sys.platform != "win32":
            return []

        import pythoncom
        pythoncom.CoInitialize()
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            mapi = outlook.GetNamespace("MAPI")

            account_folder = None
            inbox = None

            # Look through folders, skipping archives
            for folder in mapi.Folders:
                name_lower = folder.Name.lower()
                if "archive" in name_lower and (not self.target_account_keyword or "archive" not in self.target_account_keyword.lower()):
                    continue

                if self.target_account_keyword and self.target_account_keyword.lower() in name_lower:
                    try:
                        inbox = folder.Folders["Inbox"]
                        account_folder = folder
                        break
                    except Exception:
                        pass

                if not account_folder and ("edu" in name_lower or "msstate" in name_lower or "@" in name_lower):
                    try:
                        inbox = folder.Folders["Inbox"]
                        account_folder = folder
                    except Exception:
                        pass

            # Fallback to default inbox (olFolderInbox = 6)
            if not inbox:
                try:
                    inbox = mapi.GetDefaultFolder(6)
                    account_folder = inbox.Parent
                except Exception as e:
                    logger.debug(f"[ OutlookIngestor ] GetDefaultFolder fallback failed: {e}")

            if not account_folder or not inbox:
                logger.info("[ OutlookIngestor ] No accessible Outlook inbox folder detected.")
                return []

            try:
                messages = inbox.Items
                messages.Sort("[ReceivedTime]", True)
            except Exception as e:
                logger.warning(f"[ OutlookIngestor ] Failed to sort Inbox for {account_folder.Name}: {e}")
                return []

            if last_sync:
                if not last_sync.tzinfo:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                cutoff_dt = last_sync
            else:
                cutoff_dt = datetime.now(timezone.utc) - timedelta(days=14)

            items: List[Dict[str, Any]] = []
            count = 0

            for m in messages:
                if count >= self.max_messages:
                    break

                try:
                    if not hasattr(m, "Subject") or not hasattr(m, "ReceivedTime"):
                        continue

                    recv_time = m.ReceivedTime
                    if not recv_time:
                        continue

                    if not getattr(recv_time, "tzinfo", None):
                        recv_dt = recv_time.replace(tzinfo=timezone.utc)
                    else:
                        recv_dt = recv_time

                    if recv_dt <= cutoff_dt:
                        break

                    subject = str(m.Subject or "(No Subject)").strip()
                    sender = str(getattr(m, "SenderName", "") or "Unknown").strip()
                    sender_email = str(getattr(m, "SenderEmailAddress", "") or "").strip()
                    body = str(getattr(m, "Body", "") or "")
                    entry_id = str(getattr(m, "EntryID", "") or "")

                    snippet = _clean_body_snippet(body)

                    # Extract attachments names if present
                    attachments = []
                    att_obj = getattr(m, "Attachments", None)
                    att_count = getattr(att_obj, "Count", 0)
                    if att_count and att_count > 0:
                        for j in range(1, min(att_count + 1, 5)):
                            try:
                                fn = att_obj.Item(j).FileName
                                if fn:
                                    attachments.append(fn)
                            except Exception:
                                pass

                    content_parts = [
                        f"School email from {sender}" + (f" <{sender_email}>" if sender_email and "@" in sender_email else ""),
                        f"Subject: '{subject}'",
                    ]
                    if attachments:
                        content_parts.append(f"Attachments: {', '.join(attachments)}")
                    if snippet:
                        content_parts.append(f"Snippet: {snippet}")

                    content_string = "\n".join(content_parts)
                    item_hash = self.compute_hash(f"outlook_{account_folder.Name}_{entry_id or subject + str(recv_dt)}")

                    items.append({
                        "content": content_string,
                        "importance": 0.5,
                        "metadata": {
                            "source": "outlook",
                            "type": "email",
                            "account": account_folder.Name,
                            "subject": subject,
                            "sender": sender,
                            "sender_email": sender_email,
                            "attachments": attachments,
                            "timestamp": recv_dt.isoformat(),
                            "hash": item_hash,
                        }
                    })
                    count += 1

                except Exception as e:
                    logger.debug(f"[ OutlookIngestor ] Skipping item due to error: {e}")
                    continue

            logger.info(f"[ OutlookIngestor ] Ingestion complete. Harvested {len(items)} emails from {account_folder.Name}.")
            return items
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
