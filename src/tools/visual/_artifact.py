"""Shared artifact registration for visual output tools."""

from __future__ import annotations

import base64
import os
import re
import uuid
from typing import Dict, Optional, Tuple

from src.shared.file_registry import file_registry
from src.shared.logger import setup_logger

logger = setup_logger("VisualArtifacts")

ARTIFACTS_DIR = os.path.abspath("artifacts")

MIME_BY_EXT = {
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def ensure_artifacts_dir() -> str:
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    return ARTIFACTS_DIR


def sanitize_basename(name: str, default: str = "output") -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", (name or "").strip()).strip("._")
    return cleaned[:80] if cleaned else default


def artifact_path(filename: str) -> str:
    ensure_artifacts_dir()
    return os.path.join(ARTIFACTS_DIR, filename)


def unique_filename(base: str, ext: str) -> str:
    stem = sanitize_basename(os.path.splitext(base)[0], "output")
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"{stem}_{uuid.uuid4().hex[:8]}{ext}"


def register_artifact_file(path: str, filename: str) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        encoded = base64.b64encode(raw).decode("utf-8")
        token = file_registry.store_file(filename, encoded, mime)
        logger.info(f"[ ARTIFACT ] Registered {filename}")
        return token
    except Exception as exc:
        logger.warning(f"Artifact registration failed for {filename}: {exc}")
        return None


def deliver_artifact(
    content_bytes: bytes,
    filename: str,
    *,
    caption: str,
    auto_preview: bool = False,
) -> Dict[str, object]:
    ensure_artifacts_dir()
    path = os.path.join(ARTIFACTS_DIR, filename)
    with open(path, "wb") as handle:
        handle.write(content_bytes)
    token = register_artifact_file(path, filename)
    try:
        from src.remote.telegram.artifact_delivery import note_artifact_for_telegram

        note_artifact_for_telegram(path, filename)
    except Exception:
        pass
    result: Dict[str, object] = {
        "success": True,
        "result": caption,
        "path": path,
        "filename": filename,
        "caption": caption,
    }
    if token:
        result["download_token"] = token
        if auto_preview:
            result["auto_preview"] = True
    return result


def write_text_artifact(content: str, filename: str) -> Tuple[str, str]:
    ensure_artifacts_dir()
    path = os.path.join(ARTIFACTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path, filename
