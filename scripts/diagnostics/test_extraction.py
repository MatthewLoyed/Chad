"""Diagnostic: validate trafilatura extraction quality on real pages.

Mirrors the extraction logic in src/skills/deep_research/agent.py (_process_single_url)
so we can judge content quality BEFORE swapping the search backend.

Run: $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\diagnostics\test_extraction.py
"""
from __future__ import annotations

import re
import sys

import httpx
import trafilatura

# Representative pages: product/spec, encyclopedia, retailer category, news/blog.
URLS = [
    ("DeWalt drill product page", "https://www.dewalt.com/product/dcd771c2/20v-max-cordless-drill-driver-kit"),
    ("Wikipedia (clean article)", "https://en.wikipedia.org/wiki/Cordless_drill"),
    ("Wirecutter review (boilerplate-heavy)", "https://www.nytimes.com/wirecutter/reviews/best-drill/"),
    ("Home Depot category (directory)", "https://www.homedepot.com/b/Tools-Power-Tools-Drills/N-5yc1vZc2bg"),
]


def extract(html: str) -> tuple[str, str]:
    """Return (content, method) using the same strategy as deep_research."""
    cleaned = trafilatura.extract(
        html, include_tables=True, include_formatting=False, no_fallback=False
    )
    if cleaned and len(cleaned.strip()) > 150:
        return cleaned.strip(), "trafilatura"

    # regex salvage fallback (same as agent.py)
    f = re.sub(r"<(script|style|head|header|footer|nav).*?>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    f = re.sub(r"<[^>]+>", " ", f)
    f = re.sub(r"\s+", " ", f).strip()
    if len(f) > 100:
        return f[:5000], "regex-salvage"
    return "", "empty"


def main() -> None:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
        for label, url in URLS:
            print("=" * 80)
            print(f"{label}\n{url}")
            try:
                r = client.get(url)
                print(f"  HTTP {r.status_code}, raw HTML {len(r.text):,} chars")
                if r.status_code != 200:
                    print("  -> non-200, skipping extraction")
                    continue
                content, method = extract(r.text)
                print(f"  extraction: {method}, content {len(content):,} chars")
                preview = content[:600].replace("\n", " ")
                print(f"  preview: {preview}")
            except Exception as e:
                print(f"  ERROR: {type(e).__name__}: {e}")
    print("=" * 80)


if __name__ == "__main__":
    main()
