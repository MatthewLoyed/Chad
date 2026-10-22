"""
Drive Catalog Ingestor — indexes all accessible Google Drive files/folders
(My Drive + Shared with me + Shared Drives) with resolved folder paths for RAG.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from src.core.intelligence.ingestion.base import BaseIngestor
from src.core.intelligence.ingestion.drive_path import (
    FOLDER_MIME,
    build_node_index,
    classify_scope,
    content_fingerprint,
    format_catalog_content,
    is_video_mime,
    resolve_folder_path,
)
from src.tools.workspace.manager import GoogleWorkspaceManager

logger = logging.getLogger("DriveCatalogIngestor")

_FILE_FIELDS = (
    "nextPageToken, files(id, name, mimeType, parents, owners, ownedByMe, shared, "
    "sharedWithMeTime, driveId, webViewLink, modifiedTime, trashed)"
)


class DriveCatalogIngestor(BaseIngestor):
    """
    Full Drive catalog for location-aware retrieval.

    Sets ``was_full_sync``, ``last_seen_file_ids``, and ``was_truncated`` after each
    ingest so the sync manager can reconcile stale catalog rows on complete full passes.
    """

    def __init__(self):
        self.was_full_sync = False
        self.was_truncated = False
        self.last_seen_file_ids: Set[str] = set()

    def get_source_name(self) -> str:
        return "drive_catalog"

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        from src.shared.config_manager import ConfigManager

        self.was_full_sync = False
        self.was_truncated = False
        self.last_seen_file_ids = set()

        if not ConfigManager.get("drive_catalog.enabled", True):
            logger.info("[ DriveCatalog ] Disabled via drive_catalog.enabled")
            return []

        include_shared_with_me = bool(
            ConfigManager.get("drive_catalog.include_shared_with_me", True)
        )
        include_shared_drives = bool(
            ConfigManager.get("drive_catalog.include_shared_drives", True)
        )
        max_files = int(ConfigManager.get("drive_catalog.max_files", 0) or 0)
        skip_videos = bool(ConfigManager.get("drive_catalog.skip_videos", True))
        reconcile_days = int(
            ConfigManager.get("drive_catalog.full_reconcile_days", 7) or 7
        )
        # 0 / negative = unlimited (overnight second-brain syncs).
        if max_files <= 0:
            max_files = 10**9

        force_full = last_sync is None
        if last_sync is not None and reconcile_days > 0:
            age = datetime.now(timezone.utc) - (
                last_sync if last_sync.tzinfo else last_sync.replace(tzinfo=timezone.utc)
            )
            if age.days >= reconcile_days:
                force_full = True
                logger.info(
                    f"[ DriveCatalog ] Forcing full reconcile (last sync {age.days}d ago)."
                )

        try:
            manager = GoogleWorkspaceManager()
            service = manager.get_drive_service()
            if not service:
                logger.error("[ DriveCatalog ] Failed to acquire Drive API service.")
                return []

            my_email = self._get_my_email(manager)
            shared_drive_names = (
                self._list_shared_drive_names(service) if include_shared_drives else {}
            )

            all_files, truncated = self._collect_files(
                service,
                include_shared_with_me=include_shared_with_me,
                include_shared_drives=include_shared_drives,
                max_files=max_files,
                skip_videos=skip_videos,
            )
            self.was_truncated = truncated
            if not all_files:
                logger.info("[ DriveCatalog ] No files discovered.")
                self.was_full_sync = force_full
                # Empty successful full scan — allow reconcile to clear stale catalog.
                self.last_seen_file_ids = set()
                return []

            nodes = build_node_index(all_files)
            self.last_seen_file_ids = set(nodes.keys())
            self.was_full_sync = force_full

            records: List[Dict[str, Any]] = []
            for file_meta in all_files:
                if file_meta.get("trashed"):
                    continue
                file_id = file_meta.get("id")
                if not file_id:
                    continue

                mod_time_str = file_meta.get("modifiedTime") or ""
                if not force_full and last_sync and mod_time_str:
                    try:
                        mod_dt = datetime.fromisoformat(
                            mod_time_str.replace("Z", "+00:00")
                        )
                        sync_cmp = (
                            last_sync
                            if last_sync.tzinfo
                            else last_sync.replace(tzinfo=timezone.utc)
                        )
                        if mod_dt <= sync_cmp:
                            continue
                    except ValueError:
                        pass

                scope = classify_scope(file_meta, my_email=my_email)
                folder_path = resolve_folder_path(
                    file_meta,
                    nodes,
                    scope=scope,
                    shared_drive_names=shared_drive_names,
                )
                name = file_meta.get("name") or "Untitled"
                mime = file_meta.get("mimeType") or "unknown"
                if skip_videos and is_video_mime(mime):
                    continue
                is_folder = mime == FOLDER_MIME
                content = format_catalog_content(
                    name=name,
                    folder_path=folder_path,
                    mime=mime,
                    is_folder=is_folder,
                    file_id=file_id,
                )
                fp = content_fingerprint(content, mod_time_str)
                item_hash = self.compute_hash(fp)

                records.append(
                    {
                        "content": content,
                        "importance": 0.8,
                        "metadata": {
                            "source": "drive_catalog",
                            "file_id": file_id,
                            "file_name": name,
                            "mime_type": mime,
                            "folder_path": folder_path,
                            "scope": scope,
                            "url": file_meta.get("webViewLink") or "",
                            "timestamp": mod_time_str
                            or datetime.now(timezone.utc).isoformat(),
                            "hash": item_hash,
                            "is_folder": is_folder,
                            "drive_id": file_meta.get("driveId"),
                        },
                    }
                )

            # Oldest-first so watermark advances safely mid-cycle
            records.sort(key=lambda r: r["metadata"].get("timestamp") or "")

            logger.info(
                f"[ DriveCatalog ] Prepared {len(records)} records "
                f"(indexed={len(all_files)}, full={force_full}, truncated={truncated})."
            )
            if truncated and max_files < 10**9:
                logger.warning(
                    f"[ DriveCatalog ] Hit max_files={max_files}; catalog may be incomplete."
                )
            return records

        except Exception as e:
            logger.error(f"[ DriveCatalog ] Critical failure: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    def _get_my_email(self, manager: GoogleWorkspaceManager) -> Optional[str]:
        try:
            gmail = manager.get_gmail_service()
            if gmail:
                profile = gmail.users().getProfile(userId="me").execute()
                return (profile.get("emailAddress") or "").strip() or None
        except Exception as e:
            logger.debug(f"[ DriveCatalog ] Could not resolve profile email: {e}")
        return None

    def _list_shared_drive_names(self, service) -> Dict[str, str]:
        names: Dict[str, str] = {}
        page_token = None
        try:
            while True:
                resp = (
                    service.drives()
                    .list(pageSize=100, pageToken=page_token, fields="nextPageToken, drives(id, name)")
                    .execute()
                )
                for d in resp.get("drives", []):
                    if d.get("id"):
                        names[d["id"]] = d.get("name") or "Shared Drive"
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            logger.warning(f"[ DriveCatalog ] Shared drives list failed: {e}")
        return names

    def _paginate_files(self, service, *, q: str, supports_all: bool = True) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        page_token = None
        while True:
            kwargs: Dict[str, Any] = {
                "q": q,
                "pageSize": 100,
                "fields": _FILE_FIELDS,
                "pageToken": page_token,
                "orderBy": "folder,modifiedTime",
            }
            if supports_all:
                kwargs["supportsAllDrives"] = True
                kwargs["includeItemsFromAllDrives"] = True
            resp = service.files().list(**kwargs).execute()
            files.extend(resp.get("files") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def _collect_files(
        self,
        service,
        *,
        include_shared_with_me: bool,
        include_shared_drives: bool,
        max_files: int,
        skip_videos: bool = True,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Return (files, truncated). Shared-with-me is collected first so it is not starved."""
        by_id: Dict[str, Dict[str, Any]] = {}
        truncated = False
        skipped_videos = 0

        def _add(batch: List[Dict[str, Any]]) -> None:
            nonlocal skipped_videos
            for f in batch:
                fid = f.get("id")
                if not fid or f.get("trashed"):
                    continue
                mime = f.get("mimeType") or ""
                # Keep folders always (needed for path resolution); drop video blobs
                # so they don't consume max_files.
                if skip_videos and mime != FOLDER_MIME and is_video_mime(mime):
                    skipped_videos += 1
                    continue
                by_id[fid] = f

        def _at_cap() -> bool:
            return len(by_id) >= max_files

        # 1. Shared-with-me first (API only returns top-level shares; BFS into folders)
        if include_shared_with_me:
            shared_roots = self._paginate_files(
                service,
                q="sharedWithMe = true and trashed = false",
                supports_all=False,
            )
            for f in shared_roots:
                f["_from_shared_with_me"] = True
            _add(shared_roots)
            folder_queue: deque[str] = deque(
                f["id"]
                for f in shared_roots
                if f.get("id") and f.get("mimeType") == FOLDER_MIME
            )
            visited_folders: Set[str] = set()
            while folder_queue and not _at_cap():
                folder_id = folder_queue.popleft()
                if folder_id in visited_folders:
                    continue
                visited_folders.add(folder_id)
                try:
                    children = self._paginate_files(
                        service,
                        q=f"'{folder_id}' in parents and trashed = false",
                        supports_all=True,
                    )
                except Exception as e:
                    logger.warning(
                        f"[ DriveCatalog ] Failed listing children of {folder_id}: {e}"
                    )
                    continue
                for child in children:
                    child["_from_shared_with_me"] = True
                _add(children)
                for child in children:
                    if (
                        child.get("mimeType") == FOLDER_MIME
                        and child.get("id")
                        and child["id"] not in visited_folders
                    ):
                        folder_queue.append(child["id"])
            if folder_queue or _at_cap():
                # Remaining folders mean we stopped early
                if folder_queue:
                    truncated = True

        # 2. Primary corpus (My Drive + optional shared drives) into remaining budget
        if not _at_cap():
            page_token = None
            while not _at_cap():
                kwargs: Dict[str, Any] = {
                    "q": "trashed = false",
                    "pageSize": 100,
                    "fields": _FILE_FIELDS,
                    "pageToken": page_token,
                    "orderBy": "folder,modifiedTime",
                }
                if include_shared_drives:
                    kwargs["supportsAllDrives"] = True
                    kwargs["includeItemsFromAllDrives"] = True
                resp = service.files().list(**kwargs).execute()
                _add(resp.get("files") or [])
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            if page_token or _at_cap():
                # If we stopped because of cap while pages remain, truncated
                if page_token:
                    truncated = True
                elif len(by_id) > max_files:
                    truncated = True
        else:
            truncated = True

        items = list(by_id.values())
        if len(items) > max_files:
            truncated = True
            items = items[:max_files]
        if skipped_videos:
            logger.info(
                f"[ DriveCatalog ] Skipped {skipped_videos} video file(s) (skip_videos=true)."
            )
        return items, truncated
