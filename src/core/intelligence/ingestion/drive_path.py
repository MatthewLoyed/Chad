"""Pure helpers for Google Drive catalog path resolution and content formatting.

No Drive API calls — unit-testable without credentials.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

FOLDER_MIME = "application/vnd.google-apps.folder"

SCOPE_MY_DRIVE = "my_drive"
SCOPE_SHARED_WITH_ME = "shared_with_me"
SCOPE_SHARED_DRIVE = "shared_drive"

SCOPE_PREFIX = {
    SCOPE_MY_DRIVE: "My Drive",
    SCOPE_SHARED_WITH_ME: "Shared with me",
    SCOPE_SHARED_DRIVE: "Shared Drive",
}


def is_video_mime(mime: str) -> bool:
    """True for video blobs (and Google video-like types). Catalog skips these by default."""
    mime = (mime or "").lower()
    if mime.startswith("video/"):
        return True
    # Rare / legacy Drive video container types
    if mime in {
        "application/vnd.google-apps.video",
        "application/x-matroska",
        "application/x-mpegURL",
        "application/vnd.apple.mpegurl",
    }:
        return True
    return False


def mime_label(mime: str) -> str:
    mime = mime or "unknown"
    if mime == FOLDER_MIME:
        return "folder"
    if "vnd.google-apps.document" in mime:
        return "document"
    if "vnd.google-apps.spreadsheet" in mime:
        return "spreadsheet"
    if "vnd.google-apps.presentation" in mime:
        return "presentation"
    if "pdf" in mime:
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if is_video_mime(mime):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "file"


def classify_scope(
    file_meta: Dict[str, Any],
    *,
    my_email: Optional[str] = None,
) -> str:
    """Classify a Drive file into my_drive / shared_with_me / shared_drive."""
    if file_meta.get("driveId"):
        return SCOPE_SHARED_DRIVE
    # Explicit stamps from catalog BFS / query filters
    if file_meta.get("sharedWithMe") or file_meta.get("_from_shared_with_me"):
        return SCOPE_SHARED_WITH_ME
    if file_meta.get("ownedByMe") is False:
        return SCOPE_SHARED_WITH_ME

    owners = file_meta.get("owners") or []
    if my_email and owners:
        emails = {(o.get("emailAddress") or "").lower() for o in owners}
        if my_email.lower() not in emails:
            return SCOPE_SHARED_WITH_ME
    elif owners:
        if file_meta.get("shared") and not any(o.get("me") for o in owners):
            return SCOPE_SHARED_WITH_ME

    return SCOPE_MY_DRIVE


def build_node_index(files: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map file id -> {name, parents, mimeType, ...} for path walking."""
    index: Dict[str, Dict[str, Any]] = {}
    for f in files:
        fid = f.get("id")
        if not fid:
            continue
        index[fid] = f
    return index


def resolve_folder_path(
    file_meta: Dict[str, Any],
    nodes: Dict[str, Dict[str, Any]],
    *,
    scope: str,
    shared_drive_names: Optional[Dict[str, str]] = None,
    max_depth: int = 40,
) -> str:
    """Walk parents to build a human path, e.g. 'Shared with me/Wedding/Finances'."""
    shared_drive_names = shared_drive_names or {}
    drive_id = file_meta.get("driveId")
    if scope == SCOPE_SHARED_DRIVE and drive_id:
        prefix = shared_drive_names.get(drive_id) or SCOPE_PREFIX[SCOPE_SHARED_DRIVE]
    else:
        prefix = SCOPE_PREFIX.get(scope, SCOPE_PREFIX[SCOPE_MY_DRIVE])

    parts: List[str] = []
    parents = list(file_meta.get("parents") or [])
    seen: Set[str] = set()
    depth = 0

    while parents and depth < max_depth:
        parent_id = parents[0]
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent = nodes.get(parent_id)
        if not parent:
            break
        # Stop at Drive root aliases — they have no useful name in the path.
        if parent_id in ("root",):
            break
        name = (parent.get("name") or "").strip()
        if name:
            parts.append(name)
        parents = list(parent.get("parents") or [])
        depth += 1

    parts.reverse()
    if parts:
        return f"{prefix}/{'/'.join(parts)}"
    return prefix


def format_catalog_content(
    *,
    name: str,
    folder_path: str,
    mime: str,
    is_folder: bool = False,
    file_id: str = "",
) -> str:
    label = mime_label(mime)
    kind = "folder" if is_folder or mime == FOLDER_MIME else "file"
    base = f"Google Drive {kind} '{name}' at {folder_path} ({label})"
    if file_id:
        return f"{base} [id={file_id}]"
    return base


def content_fingerprint(content: str, modified_time: str = "") -> str:
    """Stable hash input for catalog upsert change detection."""
    return f"{content}|{modified_time}"
