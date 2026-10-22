r"""
browser_hybrid_probe.py — Smarter text model + DOM snapshot + tools.

Stable session refs (data-chad-ref) + honest action feedback so the model can
correct mistakes instead of repeating them.

Run:
    $env:PYTHONPATH="."; <venv>\python.exe evals\browser_hybrid_probe.py --task hn
    $env:PYTHONPATH="."; <venv>\python.exe evals\browser_hybrid_probe.py --task autotrader_simple
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import base64
import io

from openai import AsyncOpenAI
from PIL import Image
from playwright.async_api import BrowserContext, Page, async_playwright

from src.shared.chad_browser_profile import resolve_chad_user_data_dir
from src.shared.config_manager import ConfigManager

VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_MODEL = "google/gemma-4-26b-a4b-qat"
DEFAULT_BASE_URL = "http://localhost:1234/v1"
MAX_SNAPSHOT_CHARS = 14_000
MAX_READABLE_CHARS = 4_000
MAX_SCREENSHOTS_PER_RUN = 3
SCREENSHOT_MAX_WIDTH = 1280

PRESET_TASKS = {
    "hn": (
        "Go to https://news.ycombinator.com. Report the title of the #1 story on the "
        "front page and how many points it has. Put the answer in terminate as: "
        "TITLE=<title> | POINTS=<n>. Do not invent values — read them from the page."
    ),
    "autotrader_simple": (
        "Go to https://www.autotrader.ca/. Search for used Mazda vehicles (make=Mazda). "
        "You may need a Canadian postal code (e.g. M5V2T6) for search. Scroll if needed "
        "and report one listing: year, model/trim if visible, and price. "
        "terminate with: YEAR=<y> MODEL=<m> PRICE=<p>. Do not invent values."
    ),
    # Harder: multi-page site navigation; no search engines.
    "gta6_people": (
        "Using ONLY rockstargames.com (and its subpages), find information about Grand Theft "
        "Auto VI / GTA 6. Report the names of the people featured (main characters or named "
        "people shown on the official site pages — e.g. character names). "
        "terminate with: NAMES=<comma-separated names you actually read on the site>. "
        "Rules: do NOT use google, bing, wikipedia, or any site other than rockstargames.com. "
        "Do NOT invent names from memory — only names visible in READABLE TEXT or CONTROLS "
        "on rockstargames.com pages you opened."
    ),
}

SYSTEM = """You are a browser agent. Each turn you receive:
- Current URL
- READABLE TEXT (page text extract — use this to answer when present)
- CONTROLS snapshot with stable refs like [e12]
- FEEDBACK from your last action (truthful — read it carefully)
- Optional SCREENSHOT image (only after you request one)
- Recent action log / BANNED actions

Reply with EXACTLY one JSON object (no markdown fences):
{"action":"goto","url":"https://..."}
{"action":"click","ref":"e12"}
{"action":"type","ref":"e12","text":"...","press_enter":true}
{"action":"select","ref":"e12","value":"option label or value"}
{"action":"scroll","direction":"down"}
{"action":"wait","seconds":2}
{"action":"back"}
{"action":"screenshot","reason":"short why you need vision now"}
Use select for <select> dropdowns (membership tier, etc.) — prefer select over screenshot when the option text is in CONTROLS.
{"action":"terminate","status":"success","answer":"..."}
{"action":"terminate","status":"failure","answer":"..."}

Rules:
- Only use refs listed under CONTROLS this turn. Never invent refs.
- Prefer FEEDBACK over assumptions.
- NEVER repeat an action that FEEDBACK marked FAIL, NO_EFFECT, or BLOCKED.
- Only terminate success with values you actually saw in READABLE TEXT, CONTROLS, Current URL, or a SCREENSHOT.
- If the task forbids other sites, never goto google/bing/wikipedia/etc.
- One action per turn.

When to use screenshot (expensive — use sparingly, max a few times per task):
- USE when: scroll reports NO_EFFECT (canvas/SPA), READABLE TEXT is empty or only cookies/privacy chrome,
  you are on the right URL but cannot find the answer in text, or 2+ different actions failed and you are stuck.
- DO NOT use when: the answer is already in READABLE TEXT/CONTROLS, or you have not tried any click/read yet,
  or you just took a screenshot and have not acted on it yet.
- After a screenshot, read the image + SCREENSHOT NOTES carefully, then click or terminate — do not screenshot again immediately.
"""

# Deterministic DOM walk shared by collect + stamp (order must match).
_COLLECT_JS = r"""() => {
  const isVisible = (el) => {
    const s = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s && s.visibility !== 'hidden' && s.display !== 'none'
      && r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < window.innerHeight + 400;
  };
  const nameOf = (el) => {
    return (el.getAttribute('aria-label')
      || el.getAttribute('placeholder')
      || el.getAttribute('name')
      || el.getAttribute('title')
      || (el.innerText || '').trim().split('\n')[0]
      || el.getAttribute('value')
      || '').trim().slice(0, 80);
  };
  const nodes = [];
  const seen = new Set();
  const sels = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="combobox"]', '[role="option"]', '[role="menuitem"]',
    '[role="searchbox"]',
  ];
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el) || !isVisible(el)) continue;
      seen.add(el);
      const role = el.getAttribute('role')
        || (el.tagName === 'A' ? 'link'
          : el.tagName === 'BUTTON' ? 'button'
          : el.tagName === 'INPUT' ? (el.type || 'input')
          : el.tagName === 'SELECT' ? 'combobox'
          : el.tagName === 'TEXTAREA' ? 'textbox'
          : el.tagName.toLowerCase());
      let name = nameOf(el);
      let value = (el.value != null ? String(el.value) : '').slice(0, 80);
      let options = '';
      if (el.tagName === 'SELECT') {
        options = [...el.options].map(o => (o.text || o.value || '').trim())
          .filter(Boolean).slice(0, 16).join(' | ');
        if (!name) name = 'dropdown';
      }
      if (!name && !value && !options && role !== 'textbox' && role !== 'search'
          && role !== 'searchbox' && el.type !== 'search' && el.type !== 'text') {
        continue;
      }
      const r = el.getBoundingClientRect();
      nodes.push({
        role,
        name,
        value,
        options,
        href: el.tagName === 'A' ? (el.getAttribute('href') || '') : '',
        tag: el.tagName.toLowerCase(),
        x: Math.round(r.x),
        y: Math.round(r.y),
      });
      if (nodes.length >= 90) break;
    }
    if (nodes.length >= 90) break;
  }
  return nodes;
}"""


@dataclass
class StepRecord:
    turn: int
    url: str
    action: Optional[dict[str, Any]]
    result: str
    model_raw: str
    elapsed_s: float


@dataclass
class Trajectory:
    task: str
    prompt: str
    model: str
    started_at: str
    steps: list[StepRecord] = field(default_factory=list)
    final_status: str = "running"
    final_answer: str = ""
    wall_s: float = 0.0
    failure_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RefRegistry:
    """Stable refs for the session: same control fingerprint → same eN."""

    def __init__(self) -> None:
        self.fp_to_ref: dict[str, str] = {}
        self.ref_meta: dict[str, dict[str, Any]] = {}
        self._next = 0

    def fingerprint(self, node: dict[str, Any], occ: int) -> str:
        role = (node.get("role") or "").lower()
        name = re.sub(r"\s+", " ", (node.get("name") or "").strip().lower())[:60]
        href = (node.get("href") or "").split("?")[0][:120]
        tag = (node.get("tag") or "").lower()
        return f"{role}|{name}|{href}|{tag}|#{occ}"

    def assign(self, nodes: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        """Return [(ref, node), ...] in collection order; updates live meta."""
        counts: dict[str, int] = {}
        live: list[tuple[str, dict[str, Any]]] = []
        live_refs: set[str] = set()
        for node in nodes:
            base = (
                f"{(node.get('role') or '').lower()}|"
                f"{re.sub(r'\s+', ' ', (node.get('name') or '').strip().lower())[:60]}|"
                f"{(node.get('href') or '').split('?')[0][:120]}|"
                f"{(node.get('tag') or '').lower()}"
            )
            occ = counts.get(base, 0)
            counts[base] = occ + 1
            fp = self.fingerprint(node, occ)
            if fp not in self.fp_to_ref:
                ref = f"e{self._next}"
                self._next += 1
                self.fp_to_ref[fp] = ref
            ref = self.fp_to_ref[fp]
            meta = {
                "role": node.get("role") or "element",
                "name": (node.get("name") or "").replace("\n", " ")[:80],
                "value": (node.get("value") or "")[:80],
                "options": (node.get("options") or "")[:200],
                "href": node.get("href") or "",
                "tag": node.get("tag") or "",
                "fp": fp,
            }
            self.ref_meta[ref] = meta
            live.append((ref, meta))
            live_refs.add(ref)
        self._live_refs = live_refs
        return live

    def is_live(self, ref: str) -> bool:
        return ref in getattr(self, "_live_refs", set())

    def describe(self, ref: str) -> str:
        m = self.ref_meta.get(ref)
        if not m:
            return f"[{ref}] (unknown)"
        bits = [f"[{ref}]", m["role"]]
        if m["name"]:
            bits.append(f'"{m["name"]}"')
        if m["value"]:
            bits.append(f'value="{m["value"]}"')
        if m["href"]:
            href = m["href"]
            if len(href) > 60:
                href = href[:57] + "..."
            bits.append(f"href={href}")
        return " ".join(bits)


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))


def extract_model_text(message: Any) -> str:
    content = (getattr(message, "content", None) or "").strip()
    reasoning = (getattr(message, "reasoning_content", None) or "").strip()
    return content or reasoning


def parse_action(raw: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    candidates: list[str] = []
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL):
        candidates.append(m.group(0))
    for blob in reversed(candidates):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            return obj
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and "action" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    return None


async def collect_readable_text(page: Page) -> str:
    """Richer page text: meta, headings, labels, body lines, prices, names-ish tokens."""
    chunks = await page.evaluate(
        """() => {
        const out = [];
        const push = (t, maxLen) => {
          maxLen = maxLen || 200;
          t = (t || '').trim().replace(/\\s+/g, ' ');
          if (t.length >= 2 && t.length <= maxLen) out.push(t);
        };
        push(document.title || '');
        const metaDesc = document.querySelector('meta[name="description"]');
        if (metaDesc) push(metaDesc.getAttribute('content') || '', 300);
        const ogTitle = document.querySelector('meta[property="og:title"]');
        if (ogTitle) push(ogTitle.getAttribute('content') || '');
        const ogDesc = document.querySelector('meta[property="og:description"]');
        if (ogDesc) push(ogDesc.getAttribute('content') || '', 300);

        const sel = [
          'h1', 'h2', 'h3', 'h4',
          'p', 'li', 'figcaption', 'blockquote',
          '[itemprop="name"]', '[itemprop="description"]',
          '[class*="title" i]', '[class*="name" i]', '[class*="character" i]',
          '[class*="price" i]', '[class*="heading" i]', '[class*="hero" i]',
          '[data-testid]', 'label', 'legend',
          '.titleline', '.score',
        ];
        for (const s of sel) {
          for (const el of document.querySelectorAll(s)) {
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            push(el.innerText || el.textContent || '');
            if (out.length > 80) break;
          }
          if (out.length > 80) break;
        }
        // aria-labels on non-controls (images, sections)
        for (const el of document.querySelectorAll('[aria-label]')) {
          push(el.getAttribute('aria-label') || '');
          if (out.length > 100) break;
        }
        // alt text
        for (const el of document.querySelectorAll('img[alt]')) {
          push(el.getAttribute('alt') || '');
          if (out.length > 110) break;
        }

        const body = (document.body && document.body.innerText) || '';
        // First meaningful body lines (skip tiny crumbs)
        const lines = body.split('\\n').map(l => l.trim()).filter(l => l.length >= 3 && l.length <= 200);
        for (const l of lines.slice(0, 40)) push(l);

        const priceRe = /\\$[\\d,]+(?:\\.\\d{2})?/g;
        for (const p of (body.match(priceRe) || []).slice(0, 15)) push(p);
        const scoreRe = /\\b\\d{1,4}\\s+points?\\b/gi;
        for (const s of (body.match(scoreRe) || []).slice(0, 10)) push(s);
        // Capitalized name-like tokens (2+ words)
        const nameRe = /\\b[A-Z][a-z]+(?:\\s+[A-Z][a-z]+){1,2}\\b/g;
        const names = body.match(nameRe) || [];
        const nameCounts = {};
        for (const n of names) {
          if (n.length < 4) continue;
          nameCounts[n] = (nameCounts[n] || 0) + 1;
        }
        const topNames = Object.keys(nameCounts).sort((a,b) => nameCounts[b]-nameCounts[a]).slice(0, 20);
        if (topNames.length) push('name_like_phrases: ' + topNames.join(', '), 400);

        return out;
        }"""
    )
    noise = {
        "privacy preference center",
        "manage consent preferences",
        "cookie list",
        "accept all cookies",
        "reject all",
        "cookie settings",
    }
    seen: set[str] = set()
    lines: list[str] = []
    for t in chunks or []:
        key = t.lower()
        if key in seen or key in noise:
            continue
        seen.add(key)
        lines.append(f"- {t}")
    text = "\n".join(lines)
    if len(text) > MAX_READABLE_CHARS:
        text = text[:MAX_READABLE_CHARS] + "\n…(truncated)"
    return text or "(no readable text extracted — consider action screenshot if stuck)"


async def refresh_page_state(
    page: Page, registry: RefRegistry
) -> tuple[str, str, set[str]]:
    """Stamp stable data-chad-ref attrs; return controls text, readable text, live refs."""
    nodes = await page.evaluate(_COLLECT_JS)
    assigned = registry.assign(list(nodes or []))
    refs_in_order = [ref for ref, _ in assigned]

    # Stamp attributes in the same collection order.
    await page.evaluate(
        """(refs) => {
          const isVisible = (el) => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s && s.visibility !== 'hidden' && s.display !== 'none'
              && r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < window.innerHeight + 400;
          };
          const nameOf = (el) => {
            return (el.getAttribute('aria-label')
              || el.getAttribute('placeholder')
              || el.getAttribute('name')
              || el.getAttribute('title')
              || (el.innerText || '').trim().split('\\n')[0]
              || el.getAttribute('value')
              || '').trim().slice(0, 80);
          };
          // Clear old stamps
          for (const el of document.querySelectorAll('[data-chad-ref]')) {
            el.removeAttribute('data-chad-ref');
          }
          const seen = new Set();
          const els = [];
          const sels = [
            'a[href]', 'button', 'input', 'select', 'textarea',
            '[role="button"]', '[role="link"]', '[role="textbox"]',
            '[role="combobox"]', '[role="option"]', '[role="menuitem"]',
            '[role="searchbox"]',
          ];
          for (const sel of sels) {
            for (const el of document.querySelectorAll(sel)) {
              if (seen.has(el) || !isVisible(el)) continue;
              seen.add(el);
              const role = el.getAttribute('role')
                || (el.tagName === 'A' ? 'link'
                  : el.tagName === 'BUTTON' ? 'button'
                  : el.tagName === 'INPUT' ? (el.type || 'input')
                  : el.tagName === 'SELECT' ? 'combobox'
                  : el.tagName === 'TEXTAREA' ? 'textbox'
                  : el.tagName.toLowerCase());
              const name = nameOf(el);
              const value = (el.value != null ? String(el.value) : '');
              if (!name && !value && role !== 'textbox' && role !== 'search'
                  && role !== 'searchbox' && el.type !== 'search' && el.type !== 'text') {
                continue;
              }
              els.push(el);
              if (els.length >= 90) break;
            }
            if (els.length >= 90) break;
          }
          for (let i = 0; i < els.length && i < refs.length; i++) {
            els[i].setAttribute('data-chad-ref', refs[i]);
          }
          return els.length;
        }""",
        refs_in_order,
    )

    lines: list[str] = []
    for ref, meta in assigned:
        role = meta["role"]
        name = meta["name"]
        value = meta["value"]
        href = meta["href"]
        parts = [f"[{ref}]", role]
        if name:
            parts.append(f'"{name}"')
        if value:
            parts.append(f'value="{value}"')
        opts = meta.get("options") or ""
        if opts:
            parts.append(f"options=[{opts}]")
        if href:
            h = href if len(href) <= 70 else href[:67] + "..."
            parts.append(f"href={h}")
        lines.append(" ".join(parts))

    controls = "\n".join(lines) if lines else "(no interactive controls found)"
    if len(controls) > MAX_SNAPSHOT_CHARS:
        controls = controls[:MAX_SNAPSHOT_CHARS] + "\n…(truncated)"

    readable = await collect_readable_text(page)
    # URL path often encodes listing identity
    url = page.url or ""
    if "/offers/" in url or "/vehicle/" in url or "/cars-for-sale/" in url:
        readable = f"- (listing URL path) {url.split('?')[0]}\n" + readable

    live = {ref for ref, _ in assigned}
    return controls, readable, live


async def _url_after(page: Page) -> str:
    return page.url or ""


async def execute(
    page: Page,
    action: dict[str, Any],
    registry: RefRegistry,
    live_refs: set[str],
    url_before: str,
) -> str:
    """Run action and return honest, specific feedback."""
    act = (action.get("action") or "").lower().strip()

    async def footer() -> str:
        url_after = await _url_after(page)
        if url_after != url_before:
            return f" URL changed: {url_before} → {url_after}."
        return f" URL unchanged ({url_after})."

    try:
        if act == "goto":
            url = str(action.get("url") or "").strip()
            if not url:
                return "FAIL: goto requires a url."
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(0.5)
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            body_snip = ""
            try:
                body_snip = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 200)"
                )
            except Exception:
                pass
            note = ""
            low = (body_snip or "").lower()
            if any(
                x in low
                for x in ("unavailable", "not available", "access denied", "blocked", "captcha")
            ):
                note = (
                    " WARNING: page text looks like an error/unavailable/block page "
                    f"(starts with: {body_snip[:120]!r}). Try a different URL (e.g. .ca)."
                )
            return (
                f"OK: navigated to {page.url}."
                + (f' Title: "{title}".' if title else "")
                + note
                + f" Was: {url_before}."
            )

        if act in ("click", "type"):
            ref = str(action.get("ref") or "").strip()
            if not ref:
                return f"FAIL: {act} requires ref (e.g. e12)."
            if not registry.is_live(ref):
                sample = ", ".join(sorted(live_refs)[:12])
                return (
                    f"FAIL: [{ref}] is not on this page (stale or invented). "
                    f"Use a ref from CONTROLS this turn. Live examples: {sample}."
                )
            desc = registry.describe(ref)
            loc = page.locator(f'[data-chad-ref="{ref}"]')
            count = await loc.count()
            if count == 0:
                return (
                    f"FAIL: [{ref}] was in the snapshot but is not in the DOM now "
                    f"({desc}). Page may have re-rendered — pick a ref from the new CONTROLS."
                )

            if act == "click":
                try:
                    await loc.first.click(timeout=5000)
                except Exception as e:
                    err = str(e).split("\n")[0][:120]
                    return (
                        f"FAIL: could not click {desc} ({err}). "
                        f"Do NOT retry [{ref}] — it is not clickable right now "
                        f"(covered, off-screen, or disabled). Pick a different control."
                        + f" URL still {page.url}."
                    )
                await asyncio.sleep(0.4)
                url_after = await _url_after(page)
                if url_after == url_before:
                    return (
                        f"OK: clicked {desc}, but the URL did not change "
                        f"({url_after}). If that was not useful, do NOT click [{ref}] "
                        f"again — try another control or read READABLE TEXT."
                    )
                return f"OK: clicked {desc}." + await footer()

            # type
            text = str(action.get("text") or "")
            press_enter = bool(action.get("press_enter", False))
            await loc.first.click(timeout=5000)
            await asyncio.sleep(0.15)
            modifier = "Meta" if sys.platform == "darwin" else "Control"
            await page.keyboard.press(f"{modifier}+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(text, delay=20)
            if press_enter:
                await page.keyboard.press("Enter")
            await asyncio.sleep(0.4)
            # Re-read value if input-like
            actual_val = ""
            try:
                actual_val = await loc.first.input_value(timeout=500)
            except Exception:
                try:
                    actual_val = await loc.first.inner_text(timeout=500)
                except Exception:
                    actual_val = ""
            val_note = (
                f' Control value now: "{actual_val[:80]}".'
                if actual_val
                else " (could not read control value after type — keystrokes were sent)."
            )
            enter_note = " Pressed Enter." if press_enter else ""
            return (
                f"OK: typed {text!r} into {desc}.{val_note}{enter_note}"
                + await footer()
            )

        if act == "select":
            ref = str(action.get("ref") or "").strip()
            value = str(action.get("value") or "").strip()
            if not ref or not value:
                return "FAIL: select requires ref and value (option label or value)."
            if not registry.is_live(ref):
                sample = ", ".join(sorted(live_refs)[:12])
                return (
                    f"FAIL: [{ref}] is not on this page. Live examples: {sample}."
                )
            desc = registry.describe(ref)
            loc = page.locator(f'[data-chad-ref="{ref}"]')
            if await loc.count() == 0:
                return f"FAIL: [{ref}] not in DOM ({desc}). Pick another ref."
            try:
                await loc.first.select_option(label=value, timeout=3000)
            except Exception:
                try:
                    await loc.first.select_option(value=value, timeout=3000)
                except Exception:
                    # Partial label match
                    try:
                        await loc.first.select_option(
                            label=re.compile(re.escape(value), re.I), timeout=3000
                        )
                    except Exception as e:
                        err = str(e).split("\n")[0][:160]
                        return (
                            f"FAIL: could not select {value!r} on {desc} ({err}). "
                            f"Do NOT retry the same value — check CONTROLS for exact option text."
                        )
            await asyncio.sleep(0.3)
            selected = ""
            try:
                selected = await loc.first.input_value(timeout=500)
            except Exception:
                pass
            sel_note = f' Selected value now: "{selected}".' if selected else ""
            return f"OK: selected {value!r} on {desc}.{sel_note}" + await footer()

        if act == "scroll":
            direction = str(action.get("direction") or "down").lower()
            before = await page.evaluate(
                "() => ({y: window.scrollY, h: document.documentElement.scrollHeight, "
                "vh: window.innerHeight})"
            )
            if direction == "up":
                await page.keyboard.press("PageUp")
            else:
                await page.keyboard.press("PageDown")
            await asyncio.sleep(0.3)
            after = await page.evaluate(
                "() => ({y: window.scrollY, h: document.documentElement.scrollHeight, "
                "vh: window.innerHeight})"
            )
            moved = int(after["y"] - before["y"])
            max_y = max(1, int(after["h"] - after["vh"]))
            pct = int(100 * after["y"] / max_y)
            if moved == 0:
                return (
                    f"NO_EFFECT: window scroll {direction} did nothing "
                    f"(y stayed {after['y']}). This page likely scrolls inside a panel "
                    f"or is not window-scrollable. Do NOT scroll again. "
                    f"Click a content link/section in CONTROLS, or read READABLE TEXT."
                    + await footer()
                )
            return (
                f"OK: scrolled {direction} (y {before['y']}→{after['y']}, ~{pct}% down page)."
                + await footer()
            )

        if act == "wait":
            secs = float(action.get("seconds") or 2)
            await asyncio.sleep(secs)
            return f"OK: waited {secs}s." + await footer()

        if act == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(0.3)
            return f"OK: went back." + await footer()

        if act == "screenshot":
            # Handled in run loop (needs image plumbing). Should not reach here.
            return "FAIL: screenshot must be handled by the harness."

        if act == "terminate":
            status = action.get("status", "success")
            answer = action.get("answer") or action.get("message") or ""
            return f"TERMINATE:{status}:{answer}"

        return (
            f"FAIL: unknown action {act!r}. "
            "Use goto|click|type|scroll|wait|back|screenshot|terminate."
        )
    except Exception as e:
        err = str(e).split("\n")[0][:200]
        return f"FAIL: {act} raised error: {err}." + f" URL still {page.url}."


def pil_to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


async def capture_screenshot(page: Page) -> tuple[Image.Image, str]:
    """Viewport PNG, downscaled for the model; returns (image, notes)."""
    png = await page.screenshot(type="png", full_page=False)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    if w > SCREENSHOT_MAX_WIDTH:
        nh = int(h * (SCREENSHOT_MAX_WIDTH / w))
        img = img.resize((SCREENSHOT_MAX_WIDTH, nh))
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    notes = (
        f"Viewport screenshot at URL: {page.url}\n"
        f'Title: "{title}"\n'
        f"Image size sent to you: {img.size[0]}x{img.size[1]} pixels "
        f"(original viewport {w}x{h}).\n"
        "Read any visible names, labels, and text in the image. "
        "They count as evidence for terminate."
    )
    return img, notes


def action_signature(action: dict[str, Any]) -> str:
    """Canonical signature for loop detection (ignore irrelevant fields)."""
    act = (action.get("action") or "").lower()
    if act == "click":
        return f"click|{action.get('ref')}"
    if act == "type":
        return f"type|{action.get('ref')}|{action.get('text')}|{action.get('press_enter')}"
    if act == "scroll":
        return f"scroll|{action.get('direction') or 'down'}"
    if act == "goto":
        return f"goto|{action.get('url')}"
    if act == "wait":
        return f"wait|{action.get('seconds')}"
    if act == "back":
        return "back"
    if act == "select":
        return f"select|{action.get('ref')}|{action.get('value')}"
    if act == "screenshot":
        # All screenshots share one ban bucket after max uses; reason ignored for spam.
        return "screenshot"
    if act == "terminate":
        return f"terminate|{action.get('status')}|{action.get('answer')}"
    return json.dumps(action, sort_keys=True, default=str)


def is_dead_end_feedback(feedback: str) -> bool:
    """Only true failures — not OK messages that merely advise against retrying."""
    u = (feedback or "").upper()
    if u.startswith("OK:"):
        return False
    return (
        u.startswith("FAIL:")
        or u.startswith("NO_EFFECT:")
        or u.startswith("BLOCKED:")
    )


def build_user_message(
    *,
    prompt: str,
    url: str,
    controls: str,
    readable: str,
    last_feedback: str,
    history: list[str],
    banned: list[str],
    screenshot_notes: str = "",
    screenshots_left: int = MAX_SCREENSHOTS_PER_RUN,
) -> str:
    parts = [
        f"Task: {prompt}",
        f"Current URL: {url}",
        "READABLE TEXT (use for answers):\n" + readable,
        "CONTROLS (stable refs — only use these):\n" + controls,
    ]
    if screenshot_notes:
        parts.append(
            "SCREENSHOT NOTES (an image of the current viewport is attached above/below — "
            "study it):\n" + screenshot_notes
        )
    if last_feedback:
        parts.append("FEEDBACK from your last action:\n" + last_feedback)
    if banned:
        parts.append(
            "BANNED (already tried — will be blocked if you repeat):\n- "
            + "\n- ".join(banned[-12:])
        )
    if history:
        parts.append("Recent actions:\n" + "\n".join(history[-8:]))
    if any(x in url for x in ("/offers/", "/vehicle/", "/cars-for-sale/vehicle")):
        parts.append(
            "NOTE: Current URL looks like a listing/detail page. "
            "Read READABLE TEXT (and URL) and terminate with the required fields. "
            "Do not goto the homepage."
        )
    # Nudge screenshot when text is weak and scroll is useless
    readable_l = readable.lower()
    weak_text = (
        "no readable text" in readable_l
        or readable.count("\n") < 3
        or (
            "cookie" in readable_l
            and "privacy" in readable_l
            and "name_like" not in readable_l
        )
    )
    if weak_text and screenshots_left > 0:
        parts.append(
            f"HINT: READABLE TEXT looks thin/cookie-heavy. "
            f"You have {screenshots_left} screenshot(s) left. "
            'If stuck, use {"action":"screenshot","reason":"..."} once, then act on the image.'
        )
    parts.append("Next action JSON (must not be in BANNED):")
    return "\n\n".join(parts)


async def call_model(
    client: AsyncOpenAI,
    model: str,
    user_text: str,
    image: Optional[Image.Image] = None,
) -> str:
    if image is not None:
        user_content: Any = [
            {
                "type": "text",
                "text": (
                    "SCREENSHOT IMAGE: viewport of the page. "
                    "Read visible text and names from this image."
                ),
            },
            {"type": "image_url", "image_url": {"url": pil_to_data_url(image)}},
            {"type": "text", "text": user_text},
        ]
    else:
        user_content = user_text
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    return extract_model_text(resp.choices[0].message)


async def run_probe(
    prompt: str,
    model: str,
    max_turns: int,
    headless: bool,
    task_name: str,
) -> int:
    provider_cfg = ConfigManager.get_local_provider_config("lmstudio") or {}
    base_url = provider_cfg.get("base_url", DEFAULT_BASE_URL)
    api_key = provider_cfg.get("api_key", "lm-studio")
    user_data = resolve_chad_user_data_dir()

    print("=== browser_hybrid_probe (stable refs + honest feedback) ===")
    print(f"model={model}")
    print(f"prompt={prompt!r}")
    print(f"user_data_dir={user_data}")
    print(f"max_turns={max_turns}")
    print()

    traj = Trajectory(
        task=task_name,
        prompt=prompt,
        model=model,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    t0 = time.perf_counter()
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    try:
        ids = [m.id for m in (await client.models.list()).data]
        print(f"[lmstudio] models include target: {model in ids}")
    except Exception as e:
        print(f"[FAIL] LM Studio unreachable: {e}")
        return 1

    async with async_playwright() as pw:
        try:
            context: BrowserContext = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data,
                channel="chrome",
                headless=headless,
                viewport=VIEWPORT,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            print(f"[FAIL] Chrome launch: {e}")
            return 1

        page = context.pages[0] if context.pages else await context.new_page()
        await page.set_viewport_size(VIEWPORT)
        await page.goto("about:blank")

        registry = RefRegistry()
        history: list[str] = []
        last_feedback = ""
        banned_sigs: set[str] = set()
        banned_labels: list[str] = []
        no_progress_counts: dict[str, int] = {}
        screenshots_used = 0
        pending_image: Optional[Image.Image] = None
        pending_shot_notes = ""
        exit_code = 1
        try:
            for turn in range(1, max_turns + 1):
                step_t0 = time.perf_counter()
                url = page.url
                print(f"\n--- turn {turn} ---")
                print(f"[url] {url}")

                controls, readable, live_refs = await refresh_page_state(page, registry)
                screenshots_left = MAX_SCREENSHOTS_PER_RUN - screenshots_used
                user_text = build_user_message(
                    prompt=prompt,
                    url=url,
                    controls=controls,
                    readable=readable,
                    last_feedback=last_feedback,
                    history=history,
                    banned=banned_labels,
                    screenshot_notes=pending_shot_notes,
                    screenshots_left=screenshots_left,
                )

                # Attach screenshot image only for the turn after capture, then clear.
                image_for_model = pending_image
                shot_notes_for_turn = pending_shot_notes
                try:
                    raw = await call_model(client, model, user_text, image=image_for_model)
                except Exception as e:
                    err = str(e)
                    # Vision turn sometimes unloads the model — retry text-only once.
                    if image_for_model is not None and "unload" in err.lower():
                        print(f"[warn] vision call failed ({err}); retrying text-only")
                        try:
                            fallback = (
                                user_text
                                + "\n\nNOTE: screenshot image could not be loaded by the model "
                                "(runtime unload). Use CONTROLS/READABLE TEXT; prefer select "
                                "for dropdowns. options=[...] lists choices."
                            )
                            raw = await call_model(client, model, fallback, image=None)
                        except Exception as e2:
                            traj.final_status = "model_error"
                            traj.failure_mode = str(e2)
                            print(f"[FAIL] model error: {e2}")
                            break
                    else:
                        traj.final_status = "model_error"
                        traj.failure_mode = err
                        print(f"[FAIL] model error: {e}")
                        break
                finally:
                    # Image delivered once (even if model errors on it).
                    pending_image = None
                    pending_shot_notes = ""

                _safe_print(f"[model] {raw[:800]}{'…' if len(raw) > 800 else ''}")
                action = parse_action(raw)
                if not action:
                    last_feedback = (
                        "FAIL: your reply was not valid action JSON. "
                        "Do not write prose. Reply with ONLY one JSON object, e.g. "
                        '{"action":"click","ref":"e12"} or '
                        '{"action":"screenshot","reason":"no names in readable text"}.'
                    )
                    history.append(f"{turn}. parse_error: {last_feedback}")
                    traj.steps.append(
                        StepRecord(
                            turn, url, None, last_feedback, raw, time.perf_counter() - step_t0
                        )
                    )
                    print(f"[parse_error] continuing — {last_feedback}")
                    continue

                print(f"[action] {json.dumps(action)}")
                sig = action_signature(action)
                label = sig.replace("|", " ")
                act_name = (action.get("action") or "").lower()

                if sig in banned_sigs:
                    result = (
                        f"BLOCKED: you already tried [{label}] and it failed or did nothing. "
                        f"Do NOT repeat it. Pick a different ref or approach "
                        f"(see BANNED and FEEDBACK)."
                    )
                    print(f"[loop_breaker] {result}")
                elif act_name == "screenshot":
                    if screenshots_used >= MAX_SCREENSHOTS_PER_RUN:
                        result = (
                            f"BLOCKED: screenshot budget exhausted "
                            f"({MAX_SCREENSHOTS_PER_RUN} per task). "
                            "Use READABLE TEXT / CONTROLS only."
                        )
                        banned_sigs.add(sig)
                        if label not in banned_labels:
                            banned_labels.append(label)
                    else:
                        reason = str(action.get("reason") or "").strip() or "(no reason given)"
                        try:
                            img, notes = await capture_screenshot(page)
                            pending_image = img
                            pending_shot_notes = notes + f"\nYour reason: {reason}"
                            screenshots_used += 1
                            left = MAX_SCREENSHOTS_PER_RUN - screenshots_used
                            result = (
                                f"OK: captured viewport screenshot at {page.url}. "
                                f"Reason: {reason}. "
                                f"Next turn includes the IMAGE plus SCREENSHOT NOTES — "
                                f"read them, then click or terminate. "
                                f"Screenshots left after this: {left}. "
                                f"Do not screenshot again until you have used this one."
                            )
                            # Prevent immediate screenshot spam: ban until another action succeeds.
                            # (We allow more screenshots later by removing ban after a non-screenshot OK.)
                            banned_sigs.add("screenshot")
                            if "screenshot" not in banned_labels:
                                banned_labels.append("screenshot (use current image first)")
                        except Exception as e:
                            result = f"FAIL: screenshot capture error: {e}"
                else:
                    result = await execute(page, action, registry, live_refs, url)
                    # After a real action, allow screenshot again if budget remains.
                    if (
                        not is_dead_end_feedback(result)
                        and "screenshot" in banned_sigs
                        and screenshots_used < MAX_SCREENSHOTS_PER_RUN
                    ):
                        banned_sigs.discard("screenshot")
                        banned_labels[:] = [
                            b for b in banned_labels if not b.startswith("screenshot")
                        ]
                    if is_dead_end_feedback(result):
                        banned_sigs.add(sig)
                        if label not in banned_labels:
                            banned_labels.append(label)
                    elif act_name in ("click", "scroll", "type", "goto"):
                        url_now = page.url
                        if url_now == url and act_name != "type":
                            no_progress_counts[sig] = no_progress_counts.get(sig, 0) + 1
                            if no_progress_counts[sig] >= 2:
                                banned_sigs.add(sig)
                                if label not in banned_labels:
                                    banned_labels.append(label)
                                result += (
                                    f" NOTE: [{label}] produced no navigation twice — "
                                    f"it is now BANNED. Try something else"
                                    + (
                                        " or screenshot if text is insufficient."
                                        if screenshots_used < MAX_SCREENSHOTS_PER_RUN
                                        else "."
                                    )
                                )
                print(f"[result] {result}")
                last_feedback = result
                history.append(f"{turn}. {action.get('action')}: {result}")
                traj.steps.append(
                    StepRecord(
                        turn, url, action, result, raw, time.perf_counter() - step_t0
                    )
                )

                if result.startswith("TERMINATE:"):
                    parts = result.split(":", 2)
                    status = parts[1] if len(parts) > 1 else "success"
                    answer = parts[2] if len(parts) > 2 else ""
                    traj.final_status = status
                    traj.final_answer = answer
                    print(f"\n[final_answer] {answer}")
                    exit_code = 0 if status == "success" and answer.strip() else 1
                    if exit_code != 0:
                        traj.failure_mode = "empty or failed terminate"
                    break

                await asyncio.sleep(0.8)
            else:
                traj.final_status = "max_turns"
                traj.failure_mode = f"hit max_turns={max_turns}"
                print(f"\n[FAIL] max_turns={max_turns}")
        finally:
            traj.wall_s = time.perf_counter() - t0
            await context.close()

    runs_dir = Path(__file__).resolve().parent / "runs"
    runs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = runs_dir / f"hybrid_{task_name}_{ts}.json"
    latest = runs_dir / "hybrid_latest.json"
    payload = traj.to_dict()
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== SUMMARY ===")
    print(f"turns: {len(traj.steps)}")
    print(f"wall_s: {traj.wall_s:.1f}")
    print(f"final_status: {traj.final_status}")
    print(f"final_answer: {traj.final_answer!r}")
    print(f"failure_mode: {traj.failure_mode!r}")
    print(f"trajectory: {out}")
    ok = traj.final_status == "success" and bool(traj.final_answer.strip())
    print(f"PASS: {ok}")
    return 0 if ok else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", choices=sorted(PRESET_TASKS), default="hn")
    p.add_argument("--prompt", default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-turns", type=int, default=15)
    p.add_argument("--headless", action="store_true")
    args = p.parse_args()
    prompt = args.prompt or PRESET_TASKS[args.task]
    task_name = "custom" if args.prompt else args.task
    raise SystemExit(
        asyncio.run(
            run_probe(prompt, args.model, args.max_turns, args.headless, task_name)
        )
    )


if __name__ == "__main__":
    main()
