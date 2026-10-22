"""
Tier-2 page fetch for ``web_search`` — headless Playwright render.

Uses an ephemeral Chromium instance (NOT the ChadBrowser automation profile).
Fetches JS-rendered pages that ``fetch_html`` + plain httpx cannot populate.

WHY THE WORKER THREAD (issue: web-search fallback dead in prod):
    The Brain server forces ``WindowsSelectorEventLoopPolicy`` (src/remote/server.py) because
    async psycopg3 requires a selector loop on Windows. But a *selector* loop CANNOT spawn
    subprocesses, and ``chromium.launch()`` spawns the browser as a subprocess — so on the
    server's loop it fails with an opaque, empty error. (Playwright works fine standalone; only
    the server process is affected. ``browser_agent`` is unaffected because it connects to an
    already-running Chrome over CDP — no subprocess.)

    Fix: run ALL Playwright work on a dedicated thread that owns its own **Proactor** event loop
    (subprocess-capable on Windows), decoupled from the server's selector loop. The public
    coroutines below marshal their work onto that loop and await the result from the caller's
    loop. Browser/page objects live on — and are only ever touched from — the worker loop.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import Optional
from urllib.parse import urlparse

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("PlaywrightFetch")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_TIMEOUT_MS = 25_000
# Domains where Tier-1 repeatedly fails — skip straight to Playwright next time.
_playwright_preferred: set[str] = set()

# Browser state — created on, and only touched from, the worker loop.
_browser = None
_playwright = None
_lock: Optional[asyncio.Lock] = None
_semaphore: Optional[asyncio.Semaphore] = None


# ── Dedicated Proactor-loop worker thread ────────────────────────────────────


class _PlaywrightWorker:
    """Owns a background thread running a subprocess-capable event loop for Playwright."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()

    def _run(self) -> None:
        # Proactor loop on Windows can spawn the chromium subprocess; the server's selector loop can't.
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        with self._start_lock:
            if self._loop is None:
                t = threading.Thread(target=self._run, name="PlaywrightWorker", daemon=True)
                t.start()
                if not self._ready.wait(timeout=15):
                    raise RuntimeError("Playwright worker loop failed to start within 15s")
                self._thread = t
        assert self._loop is not None
        return self._loop

    async def submit(self, coro_factory):
        """Schedule ``coro_factory()`` on the worker loop; await its result from the caller's loop.

        ``coro_factory`` is a zero-arg callable returning a fresh coroutine, so the coroutine is
        bound to the worker loop where it actually runs (never the caller's loop)."""
        loop = self._ensure_started()
        cfut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return await asyncio.wrap_future(cfut)


_worker = _PlaywrightWorker()


# ── Public config/helpers (loop-agnostic) ────────────────────────────────────


def playwright_fallback_enabled() -> bool:
    raw = ConfigManager.get("tools.web_search_playwright_fallback", True)
    return bool(raw)


def prefer_playwright_for_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return domain in _playwright_preferred


def mark_playwright_preferred(url: str) -> None:
    domain = urlparse(url).netloc.lower()
    if domain:
        _playwright_preferred.add(domain)


# ── Worker-loop coroutines (only ever run on the worker thread) ───────────────


async def _ensure_browser_on_worker() -> bool:
    global _browser, _playwright, _lock, _semaphore
    if _browser is not None:
        return True
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _browser is not None:
            return True
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[ PLAYWRIGHT ] playwright package not installed; Tier-2 fetch disabled")
            return False
        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
            _semaphore = asyncio.Semaphore(2)
            logger.info("[ PLAYWRIGHT ] Tier-2 fetch browser ready")
            return True
        except Exception as exc:
            msg = str(exc).encode("ascii", "backslashreplace").decode("ascii")
            logger.error(f"[ PLAYWRIGHT ] Failed to launch browser: {msg!r} ({type(exc).__name__})")
            await _shutdown_on_worker()
            return False


async def _shutdown_on_worker() -> None:
    global _browser, _playwright, _semaphore
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _browser = None
    _playwright = None
    _semaphore = None


async def _fetch_on_worker(url: str) -> tuple[Optional[str], Optional[str]]:
    if not await _ensure_browser_on_worker():
        return None, "playwright unavailable"
    assert _browser is not None and _semaphore is not None
    async with _semaphore:
        page = None
        try:
            page = await _browser.new_page(user_agent=_USER_AGENT)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)
            if resp and resp.status >= 400:
                return None, f"playwright HTTP {resp.status}"
            # Give lazy SPAs a beat to hydrate text modules.
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            html = await page.content()
            if not html or len(html.strip()) < 100:
                return None, "playwright returned empty document"
            return html, None
        except Exception as exc:
            logger.warning(f"[ PLAYWRIGHT ] fetch failed for {url}: {exc}")
            return None, str(exc)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass


async def _run_with_page_on_worker(coro) -> object:
    if not await _ensure_browser_on_worker():
        raise RuntimeError("playwright unavailable")
    assert _browser is not None and _semaphore is not None
    async with _semaphore:
        page = await _browser.new_page(user_agent=_USER_AGENT)
        try:
            return await coro(page)
        finally:
            try:
                await page.close()
            except Exception:
                pass


# ── Public async API (unchanged signatures; marshals onto the worker loop) ────


async def shutdown_playwright_fetcher() -> None:
    """Release the shared browser (call on Brain shutdown if needed)."""
    await _worker.submit(lambda: _shutdown_on_worker())


async def fetch_rendered_html(url: str) -> tuple[Optional[str], Optional[str]]:
    """Render ``url`` in headless Chromium. Returns (html, error)."""
    if not playwright_fallback_enabled():
        return None, "playwright fallback disabled"
    try:
        return await _worker.submit(lambda: _fetch_on_worker(url))
    except Exception as exc:
        logger.error(f"[ PLAYWRIGHT ] worker submit failed for {url}: {exc}")
        return None, f"playwright worker error: {exc}"


async def run_with_page(coro):
    """Run ``await coro(page)`` on a shared headless browser tab (worker loop)."""
    return await _worker.submit(lambda: _run_with_page_on_worker(coro))
