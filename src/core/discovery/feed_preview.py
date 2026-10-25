"""Compact source previews and source-provided images for Feed posts."""
import re
from urllib.parse import urljoin, urlparse

from lxml import etree, html

from src.core.tools.limits import get


def compact_preview(text: str) -> str:
    plain = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", plain)
    preview = " ".join(sentences[:3])
    ceiling = int(get("feed_preview_words"))
    words = preview.split()
    if len(words) <= ceiling:
        return preview
    # Prefer whole source sentences within the preview budget; don't invent filler.
    complete = []
    for sentence in sentences[:3]:
        if len(" ".join([*complete, sentence]).split()) > ceiling:
            break
        complete.append(sentence)
    return " ".join(complete) if complete else " ".join(words[:ceiling]) + " …"


def source_image(markup: str, page_url: str, *, embedded: bool = False) -> str | None:
    """Prefer Open Graph/Twitter images, falling back to an RSS embedded image."""
    if not markup.strip():
        return None
    try:
        root = html.fromstring(markup)
        candidates = root.xpath('//meta[@property="og:image"]/@content')
        candidates += root.xpath('//meta[@name="twitter:image"]/@content')
        if embedded:
            candidates += root.xpath('//img/@src')
        for candidate in candidates:
            url = urljoin(page_url, candidate.strip())
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return url
    except (ValueError, TypeError, etree.ParserError):
        pass
    return None
