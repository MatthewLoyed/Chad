"""Render animated SVG/HTML/CSS with headless Chromium so a model can see what it wrote.

Three modes on one tool (mirrors ``render_diagram``'s single-tool-many-types shape):
  - frame:     one screenshot at a chosen moment in the animation
  - filmstrip: several screenshots over time, composed into one strip image
  - diff:      pixel-compare a "before" and "after" snippet at the same moment
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import sys
import threading
from io import BytesIO
from typing import Any, Dict, List, Optional

from PIL import Image, ImageChops, ImageDraw

from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import deliver_artifact, unique_filename

logger = setup_logger("RenderAnimation")

MODES = {"frame", "filmstrip", "diff", "video", "mp4"}
DEFAULT_VIEWPORT = {"width": 800, "height": 600}
MAX_FRAMES = 8


class _AnimationWorker:
    """Owns a background thread running a subprocess-capable Proactor event loop for Playwright on Windows."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()

    def _run(self) -> None:
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
                t = threading.Thread(target=self._run, name="AnimationPlaywrightWorker", daemon=True)
                t.start()
                if not self._ready.wait(timeout=15):
                    raise RuntimeError("Animation Playwright worker loop failed to start within 15s")
                self._thread = t
        assert self._loop is not None
        return self._loop

    async def submit(self, coro_factory):
        loop = self._ensure_started()
        cfut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return await asyncio.wrap_future(cfut)


_worker = _AnimationWorker()
_browser = None
_playwright = None
_lock = asyncio.Lock()


async def _ensure_browser_on_worker() -> bool:
    global _browser, _playwright
    if _browser is not None:
        return True
    async with _lock:
        if _browser is not None:
            return True
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[ RENDER_ANIMATION ] playwright package not installed")
            return False
        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
            return True
        except Exception as exc:
            logger.error(f"[ RENDER_ANIMATION ] Failed to launch browser: {exc}")
            await _shutdown_unlocked_on_worker()
            return False


async def _shutdown_unlocked_on_worker() -> None:
    global _browser, _playwright
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


async def _shutdown_on_worker() -> None:
    async with _lock:
        await _shutdown_unlocked_on_worker()


async def _ensure_browser() -> bool:
    return await _worker.submit(_ensure_browser_on_worker)


async def shutdown_render_animation() -> None:
    if _worker._loop is not None and not _worker._loop.is_closed():
        await _worker.submit(_shutdown_on_worker)


async def _screenshot_on_worker(html: str, delay_ms: int, viewport: Dict[str, int]) -> bytes:
    assert _browser is not None
    page = await _browser.new_page(viewport=viewport)
    try:
        await page.set_content(html, wait_until="domcontentloaded")
        if delay_ms > 0:
            await page.wait_for_timeout(delay_ms)
        return await page.screenshot(type="png")
    finally:
        await page.close()


async def _screenshot(html: str, delay_ms: int, viewport: Dict[str, int]) -> bytes:
    return await _worker.submit(lambda: _screenshot_on_worker(html, delay_ms, viewport))


def _compose_filmstrip(frames: List[bytes], timestamps_ms: List[int], show_timestamps: bool) -> bytes:
    images = [Image.open(BytesIO(f)).convert("RGB") for f in frames]
    w, h = images[0].size
    label_h = 24 if show_timestamps else 0
    strip = Image.new("RGB", (w * len(images), h + label_h), "white")
    for i, img in enumerate(images):
        strip.paste(img, (i * w, 0))
        if show_timestamps:
            draw = ImageDraw.Draw(strip)
            draw.text((i * w + 8, h + 4), f"{timestamps_ms[i]}ms", fill="black")
    buf = BytesIO()
    strip.save(buf, format="PNG")
    return buf.getvalue()


def _pixel_diff(before: bytes, after: bytes) -> tuple[bytes, float]:
    img_before = Image.open(BytesIO(before)).convert("RGB")
    img_after = Image.open(BytesIO(after)).convert("RGB")
    if img_before.size != img_after.size:
        img_after = img_after.resize(img_before.size)
    diff = ImageChops.difference(img_before, img_after)
    # Threshold to grayscale, then read the changed-pixel count off the histogram
    # instead of a per-pixel Python loop (fast even at full viewport resolution).
    mask = diff.convert("L").point(lambda x: 255 if x > 10 else 0)
    histogram = mask.histogram()
    total = mask.width * mask.height
    changed = histogram[255] if len(histogram) > 255 else 0
    percent = round(100.0 * changed / total, 2) if total else 0.0
    buf = BytesIO()
    diff.save(buf, format="PNG")
    return buf.getvalue(), percent


async def _record_video_on_worker(html: str, duration_ms: int, viewport: Dict[str, int]) -> tuple[bytes, str]:
    assert _browser is not None
    with tempfile.TemporaryDirectory() as tmpdir:
        context = await _browser.new_context(
            record_video_dir=tmpdir,
            record_video_size={"width": viewport["width"], "height": viewport["height"]},
            viewport=viewport,
        )
        page = await context.new_page()
        try:
            await page.set_content(html, wait_until="domcontentloaded")
            await page.wait_for_timeout(max(500, duration_ms))
        finally:
            await page.close()
            video_path = await page.video.path()
            await context.close()

        if not video_path or not os.path.exists(video_path):
            raise RuntimeError("Playwright failed to generate video recording")

        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            mp4_path = os.path.join(tmpdir, "rendered.mp4")
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                video_path,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                mp4_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0 and os.path.exists(mp4_path):
                with open(mp4_path, "rb") as f:
                    return f.read(), "mp4"
            logger.warning(f"[ RENDER_ANIMATION ] ffmpeg transcode failed: {proc.stderr}")

        with open(video_path, "rb") as f:
            return f.read(), "webm"


async def _record_video(html: str, duration_ms: int, viewport: Dict[str, int]) -> tuple[bytes, str]:
    return await _worker.submit(lambda: _record_video_on_worker(html, duration_ms, viewport))


class RenderAnimationTool(BaseTool):
    name = "render_animation"
    description = (
        "Render animated SVG/HTML/CSS with headless Chromium so you can see what you wrote "
        "instead of guessing blind. mode='frame' takes one screenshot at a chosen moment; "
        "mode='filmstrip' takes several screenshots over time composed into one strip so you "
        "can check the motion; mode='diff' pixel-compares a before/after pair to confirm an "
        "edit changed what you intended; mode='video' (or 'mp4') records the animation and exports "
        "a video (MP4/WebM) artifact. Use after load_skill('motion_design')."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        mode = str(params.get("mode") or "frame")
        title = truncate_label(str(params.get("title") or ""))
        labels = {
            "frame": "Rendering animation frame",
            "filmstrip": "Rendering animation filmstrip",
            "diff": "Diffing rendered animation",
            "video": "Rendering animation video",
            "mp4": "Rendering animation video",
        }
        base = labels.get(mode, "Rendering animation")
        return f'{base} "{title}"' if title else base

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "html": {
                        "type": "string",
                        "description": (
                            "Self-contained HTML/SVG with inline <style>/CSS — no external "
                            "asset references. In diff mode this is the 'before' version."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": sorted(MODES),
                        "description": "frame (default), filmstrip, diff, or video (mp4 export).",
                    },
                    "compare_html": {
                        "type": "string",
                        "description": "Required for mode='diff' — the 'after' version to compare against html.",
                    },
                    "delay_ms": {
                        "type": "integer",
                        "description": "Milliseconds to wait before capturing (mode='frame'/'diff'). Default 300.",
                    },
                    "frames": {
                        "type": "integer",
                        "description": "Number of frames for mode='filmstrip' (2-8). Default 4.",
                    },
                    "duration_ms": {
                        "type": "integer",
                        "description": "Total time span sampled across for mode='filmstrip'. Default 1000.",
                    },
                    "show_timestamps": {
                        "type": "boolean",
                        "description": "Label each filmstrip frame with its timestamp. Default true.",
                    },
                    "viewport_width": {"type": "integer", "description": "Default 800."},
                    "viewport_height": {"type": "integer", "description": "Default 600."},
                    "title": {"type": "string", "description": "Short label for the output filename."},
                },
                "required": ["html"],
            },
        }

    async def execute(
        self,
        html: str,
        mode: str = "frame",
        compare_html: Optional[str] = None,
        delay_ms: int = 300,
        frames: int = 4,
        duration_ms: int = 1000,
        show_timestamps: bool = True,
        viewport_width: int = DEFAULT_VIEWPORT["width"],
        viewport_height: int = DEFAULT_VIEWPORT["height"],
        title: str = "animation",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        mode = (mode or "frame").strip().lower()
        if mode not in MODES:
            return {"success": False, "error": f"Unsupported mode '{mode}'."}
        if not (html or "").strip():
            return {"success": False, "error": "html is required."}

        if not await _ensure_browser():
            return {
                "success": False,
                "error": "Could not start headless Chromium.",
                "hint": "Run `python -m playwright install chromium` and retry.",
            }

        viewport = {"width": viewport_width, "height": viewport_height}

        try:
            if mode == "frame":
                png = await _screenshot(html, delay_ms, viewport)
                filename = unique_filename(title, "png")
                payload = deliver_artifact(png, filename, caption=f'Rendered "{title}"', auto_preview=True)
                payload["mode"] = mode
                return payload

            if mode == "filmstrip":
                n = max(2, min(int(frames or 4), MAX_FRAMES))
                span = max(int(duration_ms or 1000), 0)
                timestamps = [round(i * span / (n - 1)) for i in range(n)]
                shots = [await _screenshot(html, t, viewport) for t in timestamps]
                png = _compose_filmstrip(shots, timestamps, bool(show_timestamps))
                filename = unique_filename(title, "png")
                payload = deliver_artifact(
                    png, filename, caption=f'Filmstrip "{title}" ({n} frames)', auto_preview=True
                )
                payload["mode"] = mode
                payload["frame_timestamps_ms"] = timestamps
                return payload

            if mode in {"video", "mp4"}:
                span = max(int(duration_ms or 2000), 500)
                video_bytes, ext = await _record_video(html, span, viewport)
                filename = unique_filename(title, ext)
                payload = deliver_artifact(
                    video_bytes, filename, caption=f'Rendered video "{title}" ({span}ms)', auto_preview=True
                )
                payload["mode"] = mode
                payload["duration_ms"] = span
                payload["format"] = ext
                return payload

            # diff
            if not (compare_html or "").strip():
                return {"success": False, "error": "compare_html is required for mode='diff'."}
            before_png = await _screenshot(html, delay_ms, viewport)
            after_png = await _screenshot(compare_html, delay_ms, viewport)
            diff_png, percent = _pixel_diff(before_png, after_png)
            filename = unique_filename(title, "png")
            payload = deliver_artifact(
                diff_png, filename, caption=f'Diff "{title}" ({percent}% changed)', auto_preview=True
            )
            payload["mode"] = mode
            payload["percent_changed"] = percent
            return payload

        except Exception as exc:
            logger.error(f"[ RENDER_ANIMATION ] {mode} render failed: {exc}")
            return {"success": False, "error": f"Render failed: {exc}"}
