"""Parse inbound Telegram messages into text + optional image content_blocks."""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

from src.shared.logger import setup_logger

logger = setup_logger("TelegramInbound")

DEFAULT_PHOTO_PROMPT = "What's in this image?"


def _largest_photo_file_id(msg: Dict[str, Any]) -> Optional[str]:
    photos = msg.get("photo") or []
    if not photos:
        return None
    # Telegram sends sizes ascending; last is largest.
    best = photos[-1] if isinstance(photos, list) else None
    if not isinstance(best, dict):
        return None
    file_id = best.get("file_id")
    return str(file_id) if file_id else None


def _image_document_file_id(msg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    doc = msg.get("document")
    if not isinstance(doc, dict):
        return None
    mime = (doc.get("mime_type") or "").lower()
    if not mime.startswith("image/"):
        return None
    file_id = doc.get("file_id")
    if not file_id:
        return None
    return str(file_id), mime


def extract_inbound_content(
    msg: Dict[str, Any],
    *,
    download_file_bytes,
) -> Tuple[str, Optional[List[Dict[str, Any]]], bool]:
    """Return ``(prompt_text, content_blocks_or_None, has_media)``.

    ``download_file_bytes(file_id) -> bytes`` fetches the Telegram file body.
    Caption/text empty with a photo becomes ``DEFAULT_PHOTO_PROMPT``.
    """
    text = (msg.get("text") or msg.get("caption") or "").strip()
    file_id = _largest_photo_file_id(msg)
    mime = "image/jpeg"
    if not file_id:
        doc = _image_document_file_id(msg)
        if doc:
            file_id, mime = doc

    if not file_id:
        return text, None, False

    try:
        raw = download_file_bytes(file_id)
    except Exception as e:
        logger.warning(f"[ TELEGRAM ] Failed to download inbound media {file_id}: {e}")
        # Still allow caption/text through without the image.
        return text, None, True

    if not raw:
        return text, None, True

    b64 = base64.b64encode(raw).decode("ascii")
    prompt = text or DEFAULT_PHOTO_PROMPT
    blocks: List[Dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": b64,
            },
        },
    ]
    return prompt, blocks, True
