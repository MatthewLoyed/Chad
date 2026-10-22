"""Baseline extraction quality on wedding-vendor URLs (no LLM).

Uses the production ``content_extraction`` module + snippet fallback simulation.

Run: $env:PYTHONPATH="."; python scripts/diagnostics/baseline_extraction_wedding.py
"""
from __future__ import annotations

import asyncio

import httpx

from src.tools.web.content_extraction import (
    extract_from_html,
    llm_friendliness_score,
    select_page_or_snippet,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

CASES = [
    ("Marlon Cordova / mcordovamedia", "https://mcordovamedia.com/",
     "North Alabama Wedding & Production Videographer traveling to capture your special moments!"),
    ("Chelsey K Films investment", "https://www.chelseykfilms.com/investment",
     "INVESTMENT: $3,600+. 8 hours of coverage. 2 videographers."),
    ("New Heights Film", "https://newheightsfilm.com/",
     "Husband-wife wedding videography team based in Birmingham, Alabama."),
    ("WeddingWire Creating Moments", "https://www.weddingwire.com/biz/creating-moments-events/",
     "Contact Creating Moments on WeddingWire. Rating 5 out of 5."),
    ("Facebook Chelsey K Films", "https://www.facebook.com/chelseykfilms/",
     "Chelsey K. Films — Alabama wedding videographer."),
]


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        total = 0.0
        for label, url, snippet in CASES:
            print("=" * 72)
            print(f"{label}\n{url}")
            try:
                resp = await client.get(url)
                print(f"  HTTP {resp.status_code}, raw {len(resp.text):,} chars")
                page = extract_from_html(resp.text)
                content, source, ok, note = select_page_or_snippet(page, snippet)
                score = llm_friendliness_score(content) if ok else 0.0
                total += score
                print(f"  source={source} score={score:.0f} note={note}")
                print(f"  preview: {content[:400].replace(chr(10), ' ')}")
            except Exception as exc:
                print(f"  ERROR: {type(exc).__name__}: {exc}")
        print("=" * 72)
        print(f"TOTAL llm_friendliness_score: {total:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
