"""
Google Workspace Ingestors
Contains standard ingestors for pulling, normalizing, and indexing Gmail messages 
and Google Drive / Google Doc modifications into Chad's Unified Search Vector DB.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from src.core.intelligence.ingestion.base import BaseIngestor
from src.tools.workspace.manager import GoogleWorkspaceManager

logger = logging.getLogger("GoogleIngestor")


def _truncate_snippet(snippet: str, max_chars: int = 120) -> str:
    snippet = " ".join((snippet or "").split())
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 3].rstrip() + "..."


# Default categories to drop (marketing / social noise). Primary + forums keep
# real-person mail; Updates left in by default (receipts/banks can matter).
_DEFAULT_GMAIL_EXCLUDE_CATEGORIES = ("promotions", "social", "forums")
_DEFAULT_NOREPLY_FROM_TERMS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "notifications",
    "mailer-daemon",
)


def build_gmail_ingest_query(
    *,
    last_sync: Optional[datetime] = None,
    lookback_days: int = 0,
    include_sent: bool = True,
    exclude_categories: Optional[List[str]] = None,
    exclude_noreply: bool = True,
    extra_query: str = "",
) -> str:
    """Build a Gmail ``q`` string for personal-corpus ingest.

    Prefer inbox + sent conversational mail; drop Gmail marketing categories and
    common noreply automation. ``lookback_days <= 0`` with no watermark = uncapped.
    """
    parts: List[str] = []

    # Scope: inbox conversations and (optionally) outbound / threads you start.
    if include_sent:
        parts.append("(in:inbox OR in:sent)")
    else:
        parts.append("-label:SENT")

    cats = exclude_categories
    if cats is None:
        cats = list(_DEFAULT_GMAIL_EXCLUDE_CATEGORIES)
    for cat in cats:
        cat = (cat or "").strip().lower()
        if cat:
            parts.append(f"-category:{cat}")

    if exclude_noreply:
        for term in _DEFAULT_NOREPLY_FROM_TERMS:
            parts.append(f"-from:{term}")

    extra = (extra_query or "").strip()
    if extra:
        parts.append(f"({extra})")

    if last_sync:
        parts.append(f"after:{int(last_sync.timestamp())}")
    elif lookback_days > 0:
        import time

        parts.append(f"after:{int(time.time()) - (lookback_days * 86400)}")

    return " ".join(parts)


class GmailIngestor(BaseIngestor):
    """
    Ingests Gmail messages (inbox + sent by default).
    Indexes From/To/Subject + short snippet — not full body.
    """
    
    def get_source_name(self) -> str:
        return "gmail"

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        try:
            from src.shared.config_manager import ConfigManager

            # 1. Resolve Singleton Workspace Service
            manager = GoogleWorkspaceManager()
            service = manager.get_gmail_service()
            if not service:
                logger.error("[ GmailIngestor ] Failed to acquire Gmail API service.")
                return []

            lookback_days = int(ConfigManager.get("memory.gmail_lookback_days", 0) or 0)
            max_results = int(ConfigManager.get("memory.gmail_max_results", 0) or 0)
            # 0 max_results = paginate until Gmail has no more pages.
            if max_results <= 0:
                max_results = 10**9

            include_sent = bool(ConfigManager.get("memory.gmail_include_sent", True))
            exclude_noreply = bool(ConfigManager.get("memory.gmail_exclude_noreply", True))
            raw_cats = ConfigManager.get(
                "memory.gmail_exclude_categories",
                list(_DEFAULT_GMAIL_EXCLUDE_CATEGORIES),
            )
            if isinstance(raw_cats, str):
                exclude_categories = [c.strip() for c in raw_cats.split(",") if c.strip()]
            elif isinstance(raw_cats, (list, tuple)):
                exclude_categories = [str(c).strip() for c in raw_cats if str(c).strip()]
            else:
                exclude_categories = list(_DEFAULT_GMAIL_EXCLUDE_CATEGORIES)
            extra_query = str(ConfigManager.get("memory.gmail_extra_query", "") or "")

            # 2. Formulate chronological query (inbox+sent, drop marketing categories)
            q_str = build_gmail_ingest_query(
                last_sync=last_sync,
                lookback_days=lookback_days,
                include_sent=include_sent,
                exclude_categories=exclude_categories,
                exclude_noreply=exclude_noreply,
                extra_query=extra_query,
            )
            logger.info(
                f"[ GmailIngestor ] Querying messages with criteria: '{q_str}' "
                f"(max_results={'unlimited' if max_results >= 10**9 else max_results})"
            )
            
            # 3. Retrieve message IDs (paginated — cold sync needs more than one page)
            message_ids: List[Dict[str, Any]] = []
            page_token = None
            while len(message_ids) < max_results:
                page_size = min(100, max_results - len(message_ids))
                results = service.users().messages().list(
                    userId="me",
                    q=q_str,
                    maxResults=page_size,
                    pageToken=page_token,
                ).execute()
                batch = results.get("messages") or []
                message_ids.extend(batch)
                page_token = results.get("nextPageToken")
                if not page_token or not batch:
                    break
            
            if not message_ids:
                logger.info("[ GmailIngestor ] No new messages discovered.")
                return []
                
            logger.info(f"[ GmailIngestor ] Found {len(message_ids)} messages to fetch and process.")
            
            normalized_items = []
            
            # 4. Fetch message details and parse metadata
            for msg_meta in message_ids:
                try:
                    msg_id = msg_meta["id"]
                    msg = service.users().messages().get(
                        userId="me",
                        id=msg_id,
                        format="metadata",
                        metadataHeaders=["Subject", "From", "To", "Date"],
                    ).execute()
                    
                    # Extract internal timestamp (milliseconds since epoch)
                    internal_ts = int(msg.get("internalDate", 0)) / 1000.0
                    msg_dt = datetime.fromtimestamp(internal_ts, tz=timezone.utc)
                    
                    # Extra precaution: if last_sync is provided, skip if absolute internalDate is older
                    if last_sync and msg_dt <= last_sync:
                        continue
                        
                    snippet = msg.get("snippet", "")
                    
                    # Extract Headers
                    headers = msg.get("payload", {}).get("headers", [])
                    headers_dict = {h["name"]: h["value"] for h in headers}
                    
                    sender = headers_dict.get("From", "Unknown Sender")
                    to_addr = headers_dict.get("To", "")
                    subject = headers_dict.get("Subject", "(No Subject)")

                    # Include To so outbound “email to Bryan about salary” is searchable
                    to_line = f"\nTo: {to_addr}" if to_addr else ""
                    content_payload = (
                        f"Email from {sender}: '{subject}'{to_line}\n"
                        f"Snippet: {_truncate_snippet(snippet)}"
                    )
                    
                    # Identity is Gmail message_id (SyncManager upserts by it).
                    # Hash is content fingerprint so snippet/subject edits update the row.
                    item_hash = self.compute_hash(content_payload)
                    
                    meta = {
                        "source": "gmail",
                        "message_id": msg_id,
                        "sender": sender,
                        "subject": subject,
                        "timestamp": msg_dt.isoformat(),
                        "hash": item_hash,
                    }
                    if to_addr:
                        meta["to"] = to_addr
                    normalized_items.append({
                        "content": content_payload,
                        "importance": 0.7, # Emails carry substantial functional context
                        "metadata": meta,
                    })
                except Exception as inner_err:
                    logger.warning(f"[ GmailIngestor ] Failed to fetch detailed message '{msg_meta.get('id')}': {inner_err}")
                    continue
            
            # Sort oldest-to-newest to support incremental item-by-item watermarking
            normalized_items.sort(key=lambda x: x["metadata"]["timestamp"])
            return normalized_items
            
        except Exception as e:
            logger.error(f"[ GmailIngestor ] Critical failure during extraction: {e}", exc_info=True)
            return []


class GoogleDriveIngestor(BaseIngestor):
    """
    Ingests modifications to files on Google Drive (Docs, Sheets, Slides, etc.).
    Registers modified time to support dynamic content snapshots of edited files.
    Includes resolved folder_path when parents are available (catalog is still
    the system of record for full Drive coverage).
    """

    def get_source_name(self) -> str:
        return "google_drive"

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        try:
            from src.core.intelligence.ingestion.drive_path import (
                classify_scope,
                resolve_folder_path,
                build_node_index,
            )

            # 1. Resolve Drive Service
            manager = GoogleWorkspaceManager()
            service = manager.get_drive_service()
            if not service:
                logger.error("[ DriveIngestor ] Failed to acquire Drive API service.")
                return []
                
            # 2. Build chronological query
            query_parts = ["trashed = false"]
            
            if last_sync:
                # RFC3339 format required by Drive API (YYYY-MM-DDTHH:MM:SSZ)
                rfc3339_str = last_sync.isoformat().replace("+00:00", "Z")
                query_parts.append(f"modifiedTime > '{rfc3339_str}'")
            else:
                # Default to last 24 hours
                from datetime import timedelta
                one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
                rfc_str = one_day_ago.isoformat().replace("+00:00", "Z")
                query_parts.append(f"modifiedTime > '{rfc_str}'")
                
            q_str = " and ".join(query_parts)
            logger.info(f"[ DriveIngestor ] Querying modified files with criteria: '{q_str}'")
            
            # 3. List recent file activity (Limit to 50 for efficiency)
            results = service.files().list(
                q=q_str,
                spaces="drive",
                fields=(
                    "files(id, name, mimeType, modifiedTime, webViewLink, parents, "
                    "owners, ownedByMe, shared, driveId, sharedWithMeTime)"
                ),
                orderBy="modifiedTime asc",
                pageSize=50,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            
            files = results.get("files", [])
            if not files:
                logger.info("[ DriveIngestor ] No new document modifications discovered.")
                return []
                
            logger.info(f"[ DriveIngestor ] Found {len(files)} modified files to process.")

            # Resolve parent chain for path (best-effort; walk ancestors)
            parent_ids = {
                p
                for f in files
                for p in (f.get("parents") or [])
            }
            parent_metas: List[Dict[str, Any]] = []
            pending = set(parent_ids)
            seen_parents: set = set()
            depth = 0
            while pending and depth < 8:
                next_pending: set = set()
                for pid in pending:
                    if pid in seen_parents or pid == "root":
                        continue
                    seen_parents.add(pid)
                    try:
                        meta = (
                            service.files()
                            .get(
                                fileId=pid,
                                fields="id, name, parents, mimeType, driveId",
                                supportsAllDrives=True,
                            )
                            .execute()
                        )
                        parent_metas.append(meta)
                        for gp in meta.get("parents") or []:
                            if gp not in seen_parents and gp != "root":
                                next_pending.add(gp)
                    except Exception:
                        continue
                pending = next_pending
                depth += 1
            nodes = build_node_index(list(files) + parent_metas)

            shared_drive_names: Dict[str, str] = {}
            drive_ids = {
                f.get("driveId") for f in list(files) + parent_metas if f.get("driveId")
            }
            for did in drive_ids:
                try:
                    d = service.drives().get(driveId=did, fields="id, name").execute()
                    if d.get("id"):
                        shared_drive_names[d["id"]] = d.get("name") or "Shared Drive"
                except Exception:
                    continue

            my_email = None
            try:
                gmail = manager.get_gmail_service()
                if gmail:
                    my_email = (
                        gmail.users().getProfile(userId="me").execute().get("emailAddress")
                    )
            except Exception:
                pass
            
            normalized_items = []
            
            for file in files:
                try:
                    file_id = file["id"]
                    name = file.get("name", "Untitled File")
                    mime = file.get("mimeType", "unknown")
                    mod_time_str = file.get("modifiedTime", "")
                    link = file.get("webViewLink", "")
                    
                    # Translate raw Mime to readable string
                    file_type = "File"
                    if "vnd.google-apps.document" in mime:
                        file_type = "Google Doc"
                    elif "vnd.google-apps.spreadsheet" in mime:
                        file_type = "Google Sheet"
                    elif "vnd.google-apps.presentation" in mime:
                        file_type = "Google Slides"
                    elif "pdf" in mime:
                        file_type = "PDF Document"

                    scope = classify_scope(file, my_email=my_email)
                    folder_path = resolve_folder_path(
                        file,
                        nodes,
                        scope=scope,
                        shared_drive_names=shared_drive_names,
                    )
                        
                    # Formulate dense representation (path + edit time)
                    content_payload = (
                        f"Modified Google Drive {file_type}: '{name}'\n"
                        f"Path: {folder_path}\n"
                        f"Last Edited: {mod_time_str}"
                    )
                    
                    # INTELLIGENCE TRICK: Deduplication key is file_id + modifiedTime.
                    # If the user edits the file again, it gets a NEW vector entry 
                    # providing semantic continuity of document history!
                    dedup_key = f"{file_id}:{mod_time_str}"
                    item_hash = self.compute_hash(dedup_key)
                    
                    normalized_items.append({
                        "content": content_payload,
                        "importance": 0.75, # Docs carry highest persistent context
                        "metadata": {
                            "source": "google_drive",
                            "file_id": file_id,
                            "file_name": name,
                            "file_type": file_type,
                            "folder_path": folder_path,
                            "scope": scope,
                            "url": link,
                            "timestamp": mod_time_str,
                            "hash": item_hash
                        }
                    })
                except Exception as inner_err:
                    logger.warning(f"[ DriveIngestor ] Failed to parse file metadata: {inner_err}")
                    continue
                    
            return normalized_items
            
        except Exception as e:
            logger.error(f"[ DriveIngestor ] Critical failure during extraction: {e}", exc_info=True)
            return []
