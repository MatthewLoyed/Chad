"""Diagnostic: is fetching the bottleneck? Try fuller browser headers + retries.

Run: $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\diagnostics\test_fetch.py
"""
from __future__ import annotations

import httpx

URLS = [
    "https://en.wikipedia.org/wiki/Cordless_drill",
    "https://www.nytimes.com/wirecutter/reviews/best-drill/",
    "https://www.homedepot.com/b/Tools-Power-Tools-Drills/N-5yc1vZc2bg",
    "https://www.amazon.com/dp/B07TMRYP5G",
]

# Fuller, browser-like header set
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def main() -> None:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30.0, http2=True) as client:
        for url in URLS:
            try:
                r = client.get(url)
                print(f"{r.status_code}  {len(r.text):>9,} chars  {url}")
            except Exception as e:
                print(f"ERR  {type(e).__name__}: {e}  {url}")


if __name__ == "__main__":
    main()
