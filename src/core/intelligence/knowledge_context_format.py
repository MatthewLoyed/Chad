"""
Compact knowledge-layer records for prompt injection.

Raw personal_index and graph nodes are stored verbosely for search quality
(full URLs, timestamps, snippets). At injection time we shorten them so the
supervisor prompt stays small and only carries signal the model can act on.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Optional
from urllib.parse import unquote

# Obvious junk that sometimes scores on generic Drive/Gmail queries.
_SPAM_PATTERNS = (
    "final notice: your files are scheduled for removal",
    "cloudsecurityteam",
    "your account will be suspended",
    "verify your account immediately",
)

_DRIVE_PICKER_MARKERS = (
    "drive.google.com/picker/",
    "drive-fe-organize",
    "minpick/main",
)

# Query tokens → extra folded needles so "fiancee" hits "Fiancée is Sam…".
_QUERY_ALIASES = {
    "fiancee": ("fiancee", "fiancée", "fiance", "fiancé", "partner"),
    "fiance": ("fiancee", "fiancée", "fiance", "fiancé", "partner"),
    "wife": ("wife", "spouse", "partner", "fiancee", "fiancée"),
    "husband": ("husband", "spouse", "partner"),
}


def _fold(text: str) -> str:
    """ASCII-fold for matching (fiancée → fiancee)."""
    return (
        unicodedata.normalize("NFKD", text or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _query_needles(query: Optional[str]) -> list[str]:
    if not query:
        return []
    stop = {
        "who", "what", "where", "when", "why", "how", "is", "are", "was", "were",
        "the", "a", "an", "my", "me", "i", "to", "of", "for", "and", "or", "in",
        "on", "at", "do", "does", "did", "about", "tell", "with", "from", "name",
        "names", "called",
    }
    tokens = [t for t in re.findall(r"[a-z0-9]+", _fold(query)) if len(t) >= 3 and t not in stop]
    needles: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        for alias in _QUERY_ALIASES.get(tok, (tok,)):
            folded = _fold(alias)
            if folded and folded not in seen:
                seen.add(folded)
                needles.append(folded)
    return needles


def _truncate(text: str, max_chars: int, *, query: Optional[str] = None) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    relevant = _snippet_for_query(text, query, max_chars) if query else None
    if relevant:
        return relevant
    return text[: max_chars - 3].rstrip() + "..."


def _snippet_for_query(text: str, query: Optional[str], max_chars: int) -> Optional[str]:
    """Prefer a sentence/window that matches the query over a blind prefix cut."""
    needles = _query_needles(query)
    if not needles:
        return None

    folded = _fold(text)
    # Sentence-ish chunks (family facts are period-separated in one ## section).
    chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+", text) if c.strip()]
    if len(chunks) <= 1:
        chunks = [c.strip() for c in re.split(r"[;\n]+", text) if c.strip()] or [text]

    best: Optional[str] = None
    best_score = 0
    for chunk in chunks:
        cf = _fold(chunk)
        score = sum(1 for n in needles if n in cf)
        if score > best_score:
            best_score = score
            best = chunk

    if best and best_score > 0:
        if len(best) <= max_chars:
            return best
        # Keep the match inside the window.
        bf = _fold(best)
        hit_at = min((bf.find(n) for n in needles if n in bf), default=0)
        start = max(0, hit_at - max(0, (max_chars - 3) // 4))
        window = best[start : start + max_chars - 3].rstrip()
        if start > 0:
            window = "..." + window.lstrip()
        return window + ("..." if start + max_chars - 3 < len(best) else "")

    # No sentence hit — center a window on the first needle in the full text.
    hit_at = min((folded.find(n) for n in needles if n in folded), default=-1)
    if hit_at < 0:
        return None
    start = max(0, hit_at - max(0, (max_chars - 3) // 4))
    window = text[start : start + max_chars - 3].rstrip()
    if start > 0:
        window = "..." + window.lstrip()
    return window + ("..." if start + max_chars - 3 < len(text) else "")


def _short_date(iso_timestamp: str) -> str:
    """2026-05-14T19:14:39.019Z -> 2026-05-14"""
    if not iso_timestamp:
        return ""
    return iso_timestamp[:10] if len(iso_timestamp) >= 10 else iso_timestamp


def _short_sender(sender: str) -> str:
    """Alex Smith (via Google Drive) <drive-shares@...> -> Alex Smith"""
    sender = sender.strip()
    if "<" in sender:
        sender = sender.split("<", 1)[0].strip()
    sender = re.sub(r"\s*\(via [^)]+\)\s*$", "", sender, flags=re.IGNORECASE)
    return _truncate(sender, 48)


def extract_picker_filename(url: str) -> Optional[str]:
    """Pull a human filename out of bloated Google Drive picker URLs."""
    if not url:
        return None
    decoded = unquote(url)
    for pattern in (
        r',"([^"]+\.(?:md|docx|xlsx|pdf|txt|csv))"',
        r',"([^"]{3,80})","application/',
        r',null,"([^"]{3,80})","application/',
    ):
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _is_spam(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in _SPAM_PATTERNS)


def _is_drive_picker_noise(url: str) -> bool:
    if not url:
        return False
    return any(marker in url for marker in _DRIVE_PICKER_MARKERS)


def compact_knowledge_item(
    content: str,
    *,
    metadata: Optional[dict] = None,
    max_chars: int = 120,
    query: Optional[str] = None,
) -> Optional[str]:
    """
    Return a short injection-safe line, or None if the record should be skipped.

    When `query` is set, long prose blobs are truncated around the matching
    sentence (so "Fiancée is Sam…" isn't cut to "Fiancée is S..." behind
    earlier family lines).
    """
    content = (content or "").strip()
    if not content or _is_spam(content):
        return None

    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata) if metadata else {}
        except Exception:
            metadata = {}
    metadata = metadata or {}

    drive_match = re.match(
        r"Modified Google Drive (.+?): '([^']+)'\s*\nLast Edited: ([^\n]+)"
        r"(?:\s*\nAccess Link:.*)?",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if drive_match:
        file_type, name, edited = drive_match.groups()
        return _truncate(f"Drive {file_type}: '{name}' ({_short_date(edited)})", max_chars)

    # Drive catalog rows — keep file_id so the model can call google_drive_read_file.
    # Use greedy name capture so apostrophes in titles (e.g. Sam's) don't truncate.
    catalog_match = re.match(
        r"Google Drive (file|folder) '(.+)' at (.+) \(([^)]+)\)(?: \[id=([^\]]+)\])?\s*$",
        content,
        re.IGNORECASE,
    )
    if catalog_match:
        kind, name, path, label, embedded_id = catalog_match.groups()
        file_id = (embedded_id or metadata.get("file_id") or "").strip()
        # Prefer id + name; path is secondary so truncation doesn't drop the id.
        if file_id:
            line = f"Drive {kind}: '{name}' id={file_id} at {path} ({label})"
        else:
            line = f"Drive {kind}: '{name}' at {path} ({label})"
        return _truncate(line, max(max_chars, 200))

    file_match = re.match(
        r"File: '([^']+)' at ([^\n]+)\s*\nLast Modified: ([^\n]+)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if file_match:
        name, path, modified = file_match.groups()
        return _truncate(f"File: '{name}' at {path} ({_short_date(modified)})", max_chars)

    email_match = re.match(
        r"Email from (.+?): '([^']+)'\s*\nSnippet:\s*(.*)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if email_match:
        sender, subject, snippet = email_match.groups()
        line = f"Email: '{subject}' from {_short_sender(sender)}"
        if snippet.strip():
            line += f" — {_truncate(snippet.strip(), 60, query=query)}"
        return _truncate(line, max_chars)

    visit_match = re.match(
        r"(Visited Web Page|Watched YouTube Video|Visited GitHub|Searched Google for): "
        r"'([^']*)'(?: \((.*)\))?\s*$",
        content,
        re.DOTALL,
    )
    if visit_match:
        kind, title, url = visit_match.groups()
        url = (url or metadata.get("url") or "").strip()
        title = (title or metadata.get("title") or "").strip()

        if _is_drive_picker_noise(url):
            picked = extract_picker_filename(url)
            if picked:
                return _truncate(f"Drive file: '{picked}'", max_chars)
            if title and title not in ("My Drive - Google Drive", "Google Drive"):
                return _truncate(f"Drive: '{title}'", max_chars)
            return None

        if kind == "Searched Google for":
            return _truncate(f"Google search: '{title}'", max_chars)

        if kind == "Watched YouTube Video":
            return _truncate(f"YouTube: '{title}'", max_chars)

        if kind == "Visited GitHub":
            return _truncate(f"GitHub: '{title}'", max_chars)

        if url and len(url) <= 80 and "picker" not in url:
            return _truncate(f"Web: '{title}' ({url})", max_chars)
        return _truncate(f"Web: '{title}'", max_chars)

    return _truncate(content.replace("\n", " "), max_chars, query=query)


def format_knowledge_bullets(
    items: list,
    *,
    max_items: int,
    max_chars: int = 120,
    min_similarity: float = 0.0,
    query: Optional[str] = None,
) -> Optional[str]:
    """
    Turn search hits into a deduped bullet list for prompt injection.

    Each item may be a plain string or a dict with content/similarity/metadata.
    """
    lines: list[str] = []
    seen: set[str] = set()

    for item in items:
        if isinstance(item, str):
            content = item
            similarity = 1.0
            metadata = None
        else:
            content = (item.get("content") or "").strip()
            similarity = float(item.get("similarity", 0.0) or 0.0)
            metadata = item.get("metadata")

        if not math.isfinite(similarity) or (min_similarity > 0 and similarity < min_similarity):
            continue

        compact = compact_knowledge_item(
            content, metadata=metadata, max_chars=max_chars, query=query
        )
        if not compact or compact in seen:
            continue

        seen.add(compact)
        lines.append(f"- {compact}")
        if len(lines) >= max_items:
            break

    return "\n".join(lines) if lines else None
