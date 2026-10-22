"""Classify browser page surfaces (list vs detail) from URLs / Playwright dumps.

Browser agents often treat inbox/search *list-row previews* as full message bodies.
Annotating every browser tool result with an explicit surface label + a continue
nudge on list-like pages is more reliable than persona text alone.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional
from urllib.parse import urlparse


class PageSurface(str, Enum):
    CONVERSATION_DETAIL = "conversation_detail"
    INBOX_LIST = "inbox_list"
    SEARCH_RESULTS = "search_results"
    LOGIN = "login"
    PARKED_DOMAIN = "parked_domain"
    UNKNOWN = "unknown"


LIST_LIKE_SURFACES = frozenset({PageSurface.INBOX_LIST, PageSurface.SEARCH_RESULTS})

# Playwright MCP embeds this on navigate / snapshot / evaluate failures.
_PAGE_URL_RE = re.compile(r"- Page URL:\s*(\S+)", re.IGNORECASE)

# Path segments that mean "opened a specific thread/message", not the folder list.
_DETAIL_PATH_RE = re.compile(
    r"/(?:conversations?|threads?|dms?)/[^/?#]+"
    r"|/messages?/[^/?#]+",
    re.IGNORECASE,
)

_LOGIN_PATH_RE = re.compile(
    r"/(?:login|log-in|signin|sign-in|auth|authenticate)(?:/|$)",
    re.IGNORECASE,
)

_INBOX_LIST_PATH_RE = re.compile(
    r"/(?:inbox|messages?|mailbox|mail)(?:/|$)"
    r"|/mail/u/\d+/#(?:inbox|starred|sent|drafts?)(?:$|[/?])",
    re.IGNORECASE,
)

_SEARCH_PATH_RE = re.compile(r"/(?:search|s)(?:/|$)", re.IGNORECASE)

# Parked / For Sale / Broker domain signatures (URL host, path, or page content)
_PARKED_HOST_RE = re.compile(
    r"(?:^|\.)(?:forsale\.godaddy\.com|dan\.com|sedo\.com|afternic\.com|hugedomains\.com|"
    r"parkingcrew\.net|bodis\.com|domainmarket\.com|undeveloped\.com|godaddy\.com)$",
    re.IGNORECASE,
)

_PARKED_PATH_RE = re.compile(
    r"/(?:domainsearch|domain-for-sale|buy-domain|parked)(?:/|$)",
    re.IGNORECASE,
)

_PARKED_TEXT_RE = re.compile(
    r"\b(?:buy this domain|domain is for sale|domain may be for sale|inquire about this domain|"
    r"parked free, courtesy of|search for domain names|domain broker service|domain name is parked|"
    r"get this domain|is for sale for)\b",
    re.IGNORECASE,
)

LIST_PREVIEW_NUDGE = (
    "[SURFACE GATE] This is a LIST/INBOX/SEARCH view. Row text is almost always a "
    "truncated PREVIEW (ellipsis or cut mid-sentence) — not the full message body. "
    "If the user asked what a message says / the latest message / full text: click "
    "into the conversation or detail view NOW, re-snapshot, then answer. Do NOT "
    "stop and quote a list preview."
)

DETAIL_HINT = (
    "[SURFACE] Conversation/detail view — read the full latest body on this page "
    "before answering. Prefer the chronologically latest vendor/counterpart line."
)

PARKED_DOMAIN_NUDGE = (
    "[SURFACE GATE: PARKED DOMAIN / NOT REAL SITE]\n"
    "This page is a PARKED DOMAIN or DOMAIN FOR SALE (not the genuine web application).\n"
    "- DO NOT interact with or click buttons/links on this page.\n"
    "- The domain you navigated to is incorrect, expired, or misspelled.\n"
    "- Immediately run a web_search to find the real service URL, or report that the domain is invalid."
)


def extract_page_url(text: Optional[str]) -> Optional[str]:
    """Pull `- Page URL: ...` from a Playwright MCP tool dump."""
    if not text or not isinstance(text, str):
        return None
    match = _PAGE_URL_RE.search(text)
    if not match:
        return None
    url = match.group(1).rstrip(").,]\"'")
    return url or None


def classify_page_surface(url: Optional[str], text: Optional[str] = None) -> PageSurface:
    """Map a URL and optional page content to a coarse page surface."""
    # 1. Content check for parked / for-sale domains
    if text and isinstance(text, str) and _PARKED_TEXT_RE.search(text):
        return PageSurface.PARKED_DOMAIN

    if not isinstance(url, str) or not url.strip():
        return PageSurface.UNKNOWN

    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = parsed.netloc.split(":")[0].lower()
    path = (parsed.path or "").rstrip("/") or "/"
    fragment = parsed.fragment or ""
    query = parsed.query or ""
    combined = f"{path}#{fragment}" if fragment else path

    # 2. Host / Path check for domain sales/parking
    if _PARKED_HOST_RE.search(host) or _PARKED_PATH_RE.search(path):
        return PageSurface.PARKED_DOMAIN

    if _LOGIN_PATH_RE.search(path):
        return PageSurface.LOGIN

    # Gmail-style hash routes: #inbox vs #inbox/<messageId>
    if re.search(r"/mail/u/\d+", path, re.I) and fragment:
        if "/" in fragment and len(fragment.split("/", 1)[-1]) >= 8:
            return PageSurface.CONVERSATION_DETAIL
        if fragment.lower().split("/")[0] in {"inbox", "starred", "sent", "drafts", "spam"}:
            return PageSurface.INBOX_LIST

    if _DETAIL_PATH_RE.search(combined):
        return PageSurface.CONVERSATION_DETAIL

    if _SEARCH_PATH_RE.search(path) or re.search(r"(?:^|&)q=", query):
        return PageSurface.SEARCH_RESULTS

    if _INBOX_LIST_PATH_RE.search(combined):
        return PageSurface.INBOX_LIST

    return PageSurface.UNKNOWN


def format_surface_banner(surface: PageSurface, url: Optional[str] = None) -> str:
    """Short header prepended to browser tool results for the model."""
    url_bit = f" url={url}" if url else ""
    lines = [f"[PAGE SURFACE: {surface.value}]{url_bit}"]
    if surface in LIST_LIKE_SURFACES:
        lines.append(LIST_PREVIEW_NUDGE)
    elif surface == PageSurface.CONVERSATION_DETAIL:
        lines.append(DETAIL_HINT)
    elif surface == PageSurface.LOGIN:
        lines.append("[SURFACE] Login wall — stop and report LOGIN REQUIRED; never type credentials.")
    elif surface == PageSurface.PARKED_DOMAIN:
        lines.append(PARKED_DOMAIN_NUDGE)
    return "\n".join(lines)


def looks_like_truncated_preview(text: Optional[str]) -> bool:
    """Cheap cue that visible text is a cut-off list preview, not a full body."""
    if not text or not isinstance(text, str):
        return False
    sample = text.strip()
    if len(sample) < 12:
        return False
    if "…" in sample or "..." in sample:
        return True
    # Mid-clause cutoffs seen on The Knot list rows, etc.
    if re.search(r"\b(so you(?:'ll| will)?|looking forward to|please let me)\s*$", sample, re.I):
        return True
    if re.search(r"\b(the|a|an|to|for|and|or|with|your|our)\s*$", sample, re.I):
        return True
    return False
