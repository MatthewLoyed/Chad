"""
Outlook Desktop (COM) Tools
Read and search emails from local Outlook desktop account (e.g., msstate.edu).
Runs asynchronously in dedicated threads with proper COM apartment initialization.
"""

import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.shared.logger import setup_logger

logger = setup_logger("OutlookTools")


def _get_outlook_account_and_inbox(target_keyword: Optional[str] = None):
    """Locate the primary/school account folder and inbox."""
    if sys.platform != "win32":
        raise RuntimeError("Outlook COM tools require Windows.")

    import win32com.client
    outlook = win32com.client.Dispatch("Outlook.Application")
    mapi = outlook.GetNamespace("MAPI")

    candidate_account = None
    candidate_inbox = None

    for folder in mapi.Folders:
        name_lower = folder.Name.lower()
        # Skip online archives unless explicitly queried
        if "archive" in name_lower and (not target_keyword or "archive" not in target_keyword.lower()):
            continue

        if target_keyword and target_keyword.lower() in name_lower:
            try:
                candidate_inbox = folder.Folders["Inbox"]
                candidate_account = folder
                break
            except Exception:
                pass

        if not candidate_account and ("edu" in name_lower or "msstate" in name_lower or "@" in name_lower):
            try:
                candidate_inbox = folder.Folders["Inbox"]
                candidate_account = folder
            except Exception:
                pass

    if candidate_account and candidate_inbox:
        return candidate_account, candidate_inbox

    # Fallback: olFolderInbox (6)
    try:
        default_inbox = mapi.GetDefaultFolder(6)
        parent = default_inbox.Parent
        return parent, default_inbox
    except Exception as e:
        logger.warning(f"Failed to access default Outlook Inbox: {e}")

    # Fallback to first non-archive folder with Inbox
    for folder in mapi.Folders:
        if "archive" not in folder.Name.lower():
            try:
                return folder, folder.Folders["Inbox"]
            except Exception:
                pass

    raise RuntimeError("No valid Outlook Inbox found in desktop profile.")


def _extract_email_item(m) -> Optional[Dict[str, Any]]:
    """Safely extract and format an Outlook MailItem into a compact dict."""
    try:
        subject = str(getattr(m, "Subject", "") or "(No Subject)").strip()
        sender = str(getattr(m, "SenderName", "") or "Unknown").strip()
        sender_email = str(getattr(m, "SenderEmailAddress", "") or "").strip()
        unread = getattr(m, "UnRead", False)
        recv_time = getattr(m, "ReceivedTime", None)
        time_str = recv_time.strftime("%Y-%m-%d %H:%M") if recv_time else ""
        body = str(getattr(m, "Body", "") or "")
        snippet = " ".join(body.split())[:200]

        # Attachments
        attachments = []
        att_obj = getattr(m, "Attachments", None)
        att_count = getattr(att_obj, "Count", 0)
        if att_count and att_count > 0:
            for j in range(1, min(att_count + 1, 6)):
                try:
                    att_item = att_obj.Item(j)
                    fn = getattr(att_item, "FileName", "")
                    if fn:
                        attachments.append(fn)
                except Exception:
                    pass

        # Links (extract up to 3 URLs)
        urls = re.findall(r"https?://[^\s<>\"]+", body)
        links = urls[:3] if urls else []

        item = {
            "subject": subject,
            "sender": sender,
            "received_time": time_str,
            "unread": bool(unread),
            "snippet": snippet,
        }
        if sender_email and "@" in sender_email and sender_email != sender:
            item["sender_email"] = sender_email
        if attachments:
            item["attachments"] = attachments
        if links:
            item["links"] = links

        return item
    except Exception:
        return None


@tool_display("Reading Outlook email")
class ReadOutlookEmailsTool(BaseTool):
    """Read recent emails from the local Outlook inbox."""

    name = "read_outlook_emails"
    description = (
        "Read recent emails from your Outlook desktop account (e.g., Mississippi State). "
        "Returns subjects, senders, dates, attachments, links, and preview snippets. "
        "Supports filtering by sender, query keyword, unread status, or attachments."
    )
    category = "workspace"
    is_side_effecting = False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent emails to retrieve (default: 10, max: 50).",
                        "default": 10,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "If true, only returns unread emails.",
                        "default": False,
                    },
                    "sender": {
                        "type": "string",
                        "description": "Optional sender name or email address substring to filter by.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword to filter within subject or body.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "If true, only returns emails with attachments.",
                    },
                },
                "required": [],
            },
        }

    async def execute(
        self,
        limit: int = 10,
        unread_only: bool = False,
        sender: Optional[str] = None,
        query: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._read_sync,
            limit=limit,
            unread_only=unread_only,
            sender=sender,
            query=query,
            has_attachments=has_attachments,
        )

    def _read_sync(
        self,
        limit: int = 10,
        unread_only: bool = False,
        sender: Optional[str] = None,
        query: Optional[str] = None,
        has_attachments: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if sys.platform != "win32":
            return {"success": False, "error": "Outlook COM tools require Windows."}

        import pythoncom
        pythoncom.CoInitialize()
        try:
            account_folder, inbox = _get_outlook_account_and_inbox()
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            limit = max(1, min(50, int(limit)))
            sender_lower = sender.strip().lower() if sender else None
            query_lower = query.strip().lower() if query else None

            results = []
            for m in messages:
                if len(results) >= limit:
                    break

                try:
                    if not hasattr(m, "Subject"):
                        continue

                    unread = getattr(m, "UnRead", False)
                    if unread_only and not unread:
                        continue

                    m_sender = str(getattr(m, "SenderName", "") or "")
                    m_sender_email = str(getattr(m, "SenderEmailAddress", "") or "")
                    if sender_lower and sender_lower not in m_sender.lower() and sender_lower not in m_sender_email.lower():
                        continue

                    m_subject = str(getattr(m, "Subject", "") or "")
                    m_body = str(getattr(m, "Body", "") or "")
                    if query_lower and (query_lower not in m_subject.lower() and query_lower not in m_body.lower()):
                        continue

                    att_count = getattr(getattr(m, "Attachments", None), "Count", 0)
                    if has_attachments is True and att_count == 0:
                        continue
                    if has_attachments is False and att_count > 0:
                        continue

                    extracted = _extract_email_item(m)
                    if extracted:
                        results.append(extracted)
                except Exception:
                    continue

            return {
                "success": True,
                "account": account_folder.Name,
                "count": len(results),
                "emails": results,
            }
        except Exception as e:
            logger.error(f"Failed to read Outlook emails: {e}")
            return {"success": False, "error": f"Outlook read failed: {e}"}
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


@tool_display("Searching Outlook email")
class SearchOutlookEmailsTool(BaseTool):
    """Search emails in the local Outlook account by keyword."""

    name = "search_outlook_emails"
    description = (
        "Search Outlook emails by keyword in the subject, sender name, or body content. "
        "Returns matching subjects, senders, timestamps, attachments, and snippets."
    )
    category = "workspace"
    is_side_effecting = False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword to search for in emails (e.g. 'Tuition', 'Homework', professor name).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 10, max: 30).",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(self, query: str, limit: int = 10, **kwargs) -> Dict[str, Any]:
        if not query or not query.strip():
            return {"success": False, "error": "Search query is required."}

        return await asyncio.to_thread(self._search_sync, query=query.strip(), limit=limit)

    def _search_sync(self, query: str, limit: int = 10) -> Dict[str, Any]:
        if sys.platform != "win32":
            return {"success": False, "error": "Outlook COM tools require Windows."}

        import pythoncom
        pythoncom.CoInitialize()
        try:
            account_folder, inbox = _get_outlook_account_and_inbox()
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            q_lower = query.strip().lower()
            limit = max(1, min(30, int(limit)))
            matches = []

            for m in messages:
                if len(matches) >= limit:
                    break

                try:
                    subject = str(getattr(m, "Subject", "") or "")
                    sender = str(getattr(m, "SenderName", "") or "")
                    sender_email = str(getattr(m, "SenderEmailAddress", "") or "")
                    body = str(getattr(m, "Body", "") or "")

                    if (
                        q_lower in subject.lower()
                        or q_lower in sender.lower()
                        or q_lower in sender_email.lower()
                        or q_lower in body.lower()
                    ):
                        extracted = _extract_email_item(m)
                        if extracted:
                            matches.append(extracted)
                except Exception:
                    continue

            return {
                "success": True,
                "account": account_folder.Name,
                "query": query,
                "count": len(matches),
                "emails": matches,
            }
        except Exception as e:
            logger.error(f"Failed to search Outlook emails: {e}")
            return {"success": False, "error": f"Outlook search failed: {e}"}
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
