"""Live benchmark: fetch + extract quality across diverse site types (no LLM).

Loads cases from ``web_extraction_catalog.json`` (52 URLs across 19 categories).
Use ``--playwright`` for the full production pipeline. Default output is summary +
per-category breakdown; add ``--verbose`` or ``--failures-only`` for detail.

Run:
  $env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py
  $env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --playwright
  $env:PYTHONPATH="."; python scripts/diagnostics/benchmark_web_extraction.py --json artifacts/web_extraction_baseline.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from src.tools.web.content_extraction import extract_from_html, llm_friendliness_score, select_page_or_snippet
from src.tools.web.fetch_pipeline import resolve_url_content
from src.tools.web.media_metadata import parse_media_metadata

_CATALOG = Path(__file__).with_name("web_extraction_catalog.json")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class _HttpxFetchShim:
    """Mimics fetch_html MCP for benchmark runs."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def execute_tool(self, name: str, args: dict) -> dict:
        if name != "fetch_html":
            return {"success": False, "error": f"shim missing {name}"}
        url = args["url"]
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return {"success": False, "error": f"HTTP {resp.status_code}"}
            return {"success": True, "result": [{"text": resp.text}]}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


@dataclass
class CaseResult:
    id: str
    category: str
    url: str
    http_status: int | None
    raw_bytes: int
    page_ok: bool
    page_method: str
    page_score: float
    final_ok: bool
    final_source: str
    final_score: float
    preview: str
    media_images: int = 0
    media_videos: int = 0
    expect_og_image: bool = False
    og_image_mismatch: bool = False
    note: str | None = None


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


async def _run_tier1(client: httpx.AsyncClient, case: dict) -> CaseResult:
    status, html, err = None, "", None
    try:
        resp = await client.get(case["url"])
        status, html = resp.status_code, resp.text
    except Exception as exc:
        err = str(exc)

    page = extract_from_html(html) if html else extract_from_html("")
    content, source, ok, note = select_page_or_snippet(page, case.get("snippet", ""))
    if err and not ok:
        note = err
    media = parse_media_metadata(html, case["url"]) if html else {"images": [], "videos": []}
    expect_og_image = bool(case.get("expect_og_image"))
    return CaseResult(
        id=case["id"],
        category=case["category"],
        url=case["url"],
        http_status=status,
        raw_bytes=len(html),
        page_ok=page.ok,
        page_method=page.method,
        page_score=llm_friendliness_score(page.content) if page.content else 0.0,
        final_ok=ok,
        final_source=source or "none",
        final_score=llm_friendliness_score(content) if ok else 0.0,
        preview=(content or "")[:220].replace("\n", " "),
        media_images=len(media["images"]),
        media_videos=len(media["videos"]),
        expect_og_image=expect_og_image,
        og_image_mismatch=expect_og_image and not media["images"],
        note=note,
    )


async def _run_full_pipeline(client: httpx.AsyncClient, case: dict) -> CaseResult:
    shim = _HttpxFetchShim(client)
    hit = {"url": case["url"], "title": case["id"], "description": case.get("snippet", "")}
    resolved = await resolve_url_content(shim, hit, fetch_timeout=30.0, max_chars=6000)
    status, html = None, ""
    try:
        resp = await client.get(case["url"])
        status, html = resp.status_code, resp.text
    except Exception:
        pass
    page = extract_from_html(html) if html else extract_from_html("")
    media = resolved.get("media") or {"images": [], "videos": []}
    expect_og_image = bool(case.get("expect_og_image"))
    return CaseResult(
        id=case["id"],
        category=case["category"],
        url=case["url"],
        http_status=status,
        raw_bytes=len(html),
        page_ok=page.ok,
        page_method=page.method,
        page_score=llm_friendliness_score(page.content) if page.content else 0.0,
        final_ok=bool(resolved.get("success")),
        final_source=str(resolved.get("content_source") or "none"),
        final_score=float(resolved.get("score") or 0.0),
        preview=(resolved.get("content") or "")[:220].replace("\n", " "),
        media_images=len(media.get("images") or []),
        media_videos=len(media.get("videos") or []),
        expect_og_image=expect_og_image,
        og_image_mismatch=expect_og_image and not media.get("images"),
        note=resolved.get("error"),
    )


async def run_benchmark(*, use_playwright: bool) -> list[CaseResult]:
    cases = _load_cases()
    results: list[CaseResult] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=_HEADERS) as client:
        for case in cases:
            if use_playwright:
                results.append(await _run_full_pipeline(client, case))
            else:
                results.append(await _run_tier1(client, case))
    if use_playwright:
        from src.tools.web.playwright_fetch import shutdown_playwright_fetcher
        await shutdown_playwright_fetcher()
    return results


def _category_summary(results: list[CaseResult]) -> dict[str, dict[str, int | float]]:
    by_cat: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    out: dict[str, dict[str, int | float]] = {}
    for cat, rows in sorted(by_cat.items()):
        usable = sum(1 for r in rows if r.final_ok)
        out[cat] = {
            "cases": len(rows),
            "usable": usable,
            "pct": round(100 * usable / max(len(rows), 1), 1),
            "avg_score": round(sum(r.final_score for r in rows if r.final_ok) / max(usable, 1), 1),
        }
    return out


def _print_report(
    results: list[CaseResult], *, mode: str, verbose: bool, failures_only: bool
) -> None:
    usable = sum(1 for r in results if r.final_ok)
    page_only = sum(1 for r in results if r.page_ok)
    with_images = sum(1 for r in results if r.media_images)
    with_videos = sum(1 for r in results if r.media_videos)
    og_mismatches = [r for r in results if r.og_image_mismatch]
    print(f"Mode: {mode}")
    print(f"Cases: {len(results)} | page_extract_ok: {page_only} | final_usable: {usable} "
          f"({100 * usable / max(len(results), 1):.0f}%)")
    print(f"Total final_score: {sum(r.final_score for r in results):.0f}")
    print(f"Media: {with_images} cases with images, {with_videos} cases with videos")
    if og_mismatches:
        print(f"expect_og_image MISMATCHES ({len(og_mismatches)}): {', '.join(r.id for r in og_mismatches)}")
    print()

    print("By category:")
    for cat, stats in _category_summary(results).items():
        print(f"  {cat:20s} {stats['usable']}/{stats['cases']} usable ({stats['pct']}%) "
              f"avg_score={stats['avg_score']}")
    print()

    rows = results
    if failures_only:
        rows = [r for r in results if not r.final_ok]
    if not verbose and not failures_only:
        print(f"Tip: --verbose for all {len(results)} previews, --failures-only for misses only.\n")
        return

    for r in rows:
        flag = "OK" if r.final_ok else "FAIL"
        print("=" * 72)
        print(f"[{flag}] {r.id} ({r.category})")
        print(f"  {r.url}")
        print(f"  HTTP {r.http_status} | page={r.page_ok}/{r.page_method} ({r.page_score:.0f}) "
              f"| final={r.final_source} ({r.final_score:.0f})")
        if r.media_images or r.media_videos:
            print(f"  media: {r.media_images} image(s), {r.media_videos} video(s)")
        if r.og_image_mismatch:
            print("  ! expected og:image but none found")
        if r.note:
            print(f"  note: {r.note}")
        print(f"  preview: {r.preview}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark web extraction quality")
    parser.add_argument("--playwright", action="store_true", help="Run full fetch pipeline incl. Playwright Tier-2")
    parser.add_argument("--verbose", action="store_true", help="Print every case preview (default: summary only)")
    parser.add_argument("--failures-only", action="store_true", help="Print only failed cases")
    parser.add_argument("--json", type=Path, help="Write JSON results to this path")
    args = parser.parse_args()

    mode = "full_pipeline" if args.playwright else "tier1_extract_only"
    results = asyncio.run(run_benchmark(use_playwright=args.playwright))
    _print_report(results, mode=mode, verbose=args.verbose, failures_only=args.failures_only)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": mode,
            "summary": {
                "cases": len(results),
                "page_extract_ok": sum(1 for r in results if r.page_ok),
                "final_usable": sum(1 for r in results if r.final_ok),
                "total_final_score": sum(r.final_score for r in results),
            },
            "by_category": _category_summary(results),
            "results": [asdict(r) for r in results],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
