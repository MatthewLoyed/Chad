"""
HTML → LLM-friendly text for ``web_search``.

Cascade: trafilatura (markdown) → readability-lxml + markdownify → reject.
Every candidate passes a quality gate so CSS shells, bot walls, and SPA junk
never surface as successful page extracts. Callers fall back to search snippets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import trafilatura

try:
    from markdownify import markdownify as _html_to_markdown
    from readability import Document as _ReadabilityDocument

    _HAS_READABILITY = True
except ImportError:  # pragma: no cover - optional at import, required in prod
    _HAS_READABILITY = False

DEFAULT_MAX_CHARS = 6000

# Bot walls, SPA placeholders, and other non-content pages.
_BLOCKLIST_PHRASES = (
    "access denied",
    "you don't have permission to access",
    "enable javascript",
    "please enable cookies",
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "errors.edgesuite.net",
    "sorry, something went wrong",
    "please respect our robot policy",
)

_CSS_PATTERNS = (
    r"rgba\s*\(",
    r"background-color\s*:",
    r"font-family\s*:",
    r"letter-spacing\s*:",
    r"border-radius\s*:",
    r"transition-duration\s*:",
    r"box-shadow\s*:",
    r"html\.[a-zA-Z]\s*\{",
    r"\.[a-zA-Z][\w-]*\s*\{",
)


@dataclass(frozen=True)
class ExtractionResult:
    content: str
    method: str
    ok: bool
    reject_reason: Optional[str] = None


def passes_quality_gate(text: str) -> tuple[bool, Optional[str]]:
    """Return (acceptable, reject_reason). Higher bar than 'non-empty string'."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False, "empty"
    if len(cleaned) < 40:
        return False, "too short"

    lower = cleaned.lower()
    for phrase in _BLOCKLIST_PHRASES:
        if phrase in lower:
            return False, f"blocklist:{phrase}"

    # SPA shells: "Loading Loading Loading..."
    loading_hits = len(re.findall(r"\bloading\b", lower))
    words = cleaned.split()
    word_count = len(words)
    if loading_hits >= 3 and loading_hits / max(word_count, 1) > 0.25:
        return False, "spa_loading_shell"

    entities = len(re.findall(r"&(?:[a-zA-Z]+|#\d+);", cleaned))
    if entities >= 4 or (word_count and entities / word_count > 0.15):
        return False, "html_entities"

    css_hits = sum(len(re.findall(p, cleaned, re.IGNORECASE)) for p in _CSS_PATTERNS)
    brace_count = cleaned.count("{") + cleaned.count("}")
    css_score = css_hits + brace_count
    if css_hits >= 2 or (word_count and css_score / word_count > 0.08):
        return False, "css_noise"

    # Prose sanity: need some alphabetic words, not mostly punctuation/symbols.
    alpha_words = [w for w in words if re.search(r"[a-zA-Z]{3,}", w)]
    if len(alpha_words) < 6:
        return False, "low_prose"

    return True, None


def _trim(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def _try_trafilatura(html: str, *, favor_precision: bool) -> Optional[str]:
    return trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_precision=favor_precision,
        favor_recall=not favor_precision,
        no_fallback=False,
    )


def _try_readability(html: str) -> Optional[str]:
    if not _HAS_READABILITY:
        return None
    try:
        doc = _ReadabilityDocument(html)
        summary = doc.summary()
        if not summary or len(summary.strip()) < 40:
            return None
        return _html_to_markdown(summary, heading_style="ATX")
    except Exception:
        return None


def extract_from_html(html: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> ExtractionResult:
    """Extract the best LLM-friendly page content from raw HTML."""
    if not html or len(html.strip()) < 20:
        return ExtractionResult("", "none", False, "html too short")

    strategies: list[tuple[str, Optional[str]]] = [
        ("trafilatura_precision", _try_trafilatura(html, favor_precision=True)),
        ("trafilatura_recall", _try_trafilatura(html, favor_precision=False)),
        ("readability_markdown", _try_readability(html)),
    ]

    best: Optional[ExtractionResult] = None
    last_reason = "no extractor produced text"

    for method, raw in strategies:
        if not raw:
            continue
        trimmed = _trim(raw, max_chars)
        ok, reason = passes_quality_gate(trimmed)
        if ok:
            return ExtractionResult(trimmed, method, True)
        last_reason = reason or "quality gate failed"
        # Keep the least-bad candidate for debugging, but never return it as ok.
        if best is None or len(trimmed) > len(best.content):
            best = ExtractionResult(trimmed, method, False, last_reason)

    if best and best.content:
        return ExtractionResult(best.content, best.method, False, last_reason)
    return ExtractionResult("", "none", False, last_reason)


def select_page_or_snippet(
    page: ExtractionResult,
    snippet: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, str, bool, Optional[str]]:
    """Pick the best LLM-facing text from page extract vs search snippet."""
    snip = (snippet or "").strip()[:max_chars]
    snip_ok, _ = passes_quality_gate(snip)
    snip_score = llm_friendliness_score(snip) if snip_ok else 0.0

    if page.ok:
        page_score = llm_friendliness_score(page.content)
        # Thin nav/chrome extracts lose to a richer search snippet.
        if snip_ok and snip_score >= 10 and snip_score > page_score * 1.25:
            return snip, "search_snippet", True, f"snippet richer than {page.method}"
        return page.content, page.method, True, None

    if snip_ok:
        reason = page.reject_reason or "page extract failed"
        return snip, "search_snippet", True, f"{reason}; used search snippet"

    return page.content or snip, page.method if page.content else "none", False, page.reject_reason


def llm_friendliness_score(text: str) -> float:
    """Deterministic quality metric for benchmarks (higher = better)."""
    ok, _ = passes_quality_gate(text)
    if not ok or not text:
        return 0.0
    words = len(text.split())
    entities = len(re.findall(r"&(?:[a-zA-Z]+|#\d+);", text))
    css_hits = sum(len(re.findall(p, text, re.IGNORECASE)) for p in _CSS_PATTERNS)
    return max(0.0, float(words) - entities * 2 - css_hits * 5)
