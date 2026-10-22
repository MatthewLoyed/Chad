r"""
browser_fara_probe.py — Fara-7B screenshot-native CUA probe (issue #288).

Thin harness matching microsoft/fara FaraAgent:
  multi-turn chat history + last 3 screenshots + official system prompt.
Uses ChadBrowser profile for cookies. No a11y / supervisor / LangGraph.

Run (Fara-7B in LM Studio, context ≥15k, temp 0):
    $env:PYTHONPATH="."; <venv>\python.exe evals\browser_fara_probe.py --task autotrader
    $env:PYTHONPATH="."; <venv>\python.exe evals\browser_fara_probe.py --task supabase
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import AsyncOpenAI
from PIL import Image
from playwright.async_api import BrowserContext, Page, async_playwright

from src.shared.chad_browser_profile import resolve_chad_user_data_dir
from src.shared.config_manager import ConfigManager

# ── Image resize (Qwen2.5-VL smart_resize; matches microsoft/fara) ──

IMAGE_FACTOR = 28
MIN_PIXELS = 4 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28
MAX_RATIO = 200
MLM_PROCESSOR_IM_CFG = {
    "min_pixels": 3136,
    "max_pixels": 12845056,
    "patch_size": 14,
    "merge_size": 2,
}

VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_MODEL = "microsoft_fara-7b"
DEFAULT_BASE_URL = "http://localhost:1234/v1"
MAX_N_IMAGES = 3
USER_FOLLOWUP = "Here is the next screenshot. Think about what to do next."

PRESET_TASKS = {
    "autotrader": (
        "Go to https://autotrader.com/ and find a used Mazda 3 that is model year 2019 or "
        "newer (over 2018) and priced under $20,000. Apply filters or search as needed, "
        "open one matching listing, and report: year, trim if visible, price, and the full "
        "listing URL from the address bar. Do not invent values — read them on screen."
    ),
    "supabase": (
        "Open https://supabase.com/dashboard (session cookies are already in this browser; "
        "do not open /login). From the dashboard, open Usage or Billing and report how much "
        "of my quota I am using and why, with on-screen numbers. Do not invent values."
    ),
    "capability": (
        "Complete ALL of the following. Put every field in your final answer — do not invent values.\n"
        "1) Go to https://news.ycombinator.com. Open the #1 story that links to an external article "
        "(skip 'Ask HN', 'Show HN', and job posts if they are #1). Report: title, points, "
        "comment count, and the external domain (host only).\n"
        "2) Go back to the HN front page. Open the #2 external story and report only its title.\n"
        "3) Visit https://en.wikipedia.org/wiki/Main_Page and report today's Featured Article title.\n"
        "Final answer format exactly:\n"
        "HN1=<title> | points=<n> | comments=<n> | domain=<host>; HN2=<title>; WIKI=<featured title>"
    ),
}

# Official Fara-7B system prompt (model card / microsoft/fara _prompts). Resolution injected.
_FARA_OFFICIAL_SYSTEM = """You are a web automation agent that performs actions on websites to fulfill user requests by calling various tools.

You should stop execution at **Critical Points**. A Critical Point occurs in tasks like:

- Checkout
- Book
- Purchase
- Call
- Email
- Order

A Critical Point requires the user's permission or personal/sensitive information (name, email, credit card, address, payment information, resume, etc.) to complete a transaction (purchase, reservation, sign-up, etc.), or to communicate as a human would (call, email, apply to a job, etc.).

**Guideline:** Solve the task as far as possible **up until a Critical Point**.

**Examples:**

- If the task is to "call a restaurant to make a reservation," do **not** actually make the call. Instead, navigate to the restaurant's page and find the phone number.
- If the task is to "order new size 12 running shoes," do **not** place the order. Instead, search for the right shoes that meet the criteria and add them to the cart.

Some tasks, like answering questions, may not encounter a Critical Point at all.

You are provided with function signatures within XML tags:

{{
  "type": "function",
  "function": {{
    "name": "computer_use",
    "description": "Use a mouse and keyboard to interact with a computer, and take screenshots.\\n* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. You must click on desktop icons to start applications.\\n* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn't open, try wait and taking another screenshot.\\n* The screen's resolution is {resized_width}x{resized_height}.\\n* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.\\n* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.\\n* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.\\n* When a separate scrollable container prominently overlays the webpage, if you want to scroll within it, you typically need to mouse_move() over it first and then scroll().\\n* If a popup window appears that you want to close, if left_click() on the 'X' or close button doesn't work, try key(keys=['Escape']) to close it.\\n* On some search bars, when you type(), you may need to press_enter=False and instead separately call left_click() on the search button to submit the search query. This is especially true of search bars that have auto-suggest popups for e.g. locations\\n* For calendar widgets, you usually need to left_click() on arrows to move between months and left_click() on dates to select them; type() is not typically used to input dates there.",
    "parameters": {{
      "properties": {{
        "action": {{
          "description": "The action to perform. The available actions are:\\n* key: Performs key down presses on the arguments passed in order, then performs key releases in reverse order.\\n* type: Type a string of text on the keyboard.\\n* mouse_move: Move the cursor to a specified (x, y) pixel coordinate on the screen.\\n* left_click: Click the left mouse button.\\n* scroll: Performs a scroll of the mouse scroll wheel.\\n* visit_url: Visit a specified URL.\\n* web_search: Perform a web search with a specified query.\\n* history_back: Go back to the previous page in the browser history.\\n* pause_and_memorize_fact: Pause and memorize a fact for future reference.\\n* wait: Wait specified seconds for the change to happen.\\n* terminate: Terminate the current task and report its completion status.",
          "enum": ["key", "type", "mouse_move", "left_click", "scroll", "visit_url", "web_search", "history_back", "pause_and_memorize_fact", "wait", "terminate"],
          "type": "string"
        }},
        "keys": {{"description": "Required only by action=key.", "type": "array"}},
        "text": {{"description": "Required only by action=type.", "type": "string"}},
        "coordinate": {{"description": "(x, y) coordinates for mouse actions.", "type": "array"}},
        "pixels": {{"description": "Amount of scrolling. Positive = up, Negative = down.", "type": "number"}},
        "url": {{"description": "The URL to visit.", "type": "string"}},
        "query": {{"description": "The query to search for.", "type": "string"}},
        "fact": {{"description": "The fact to remember.", "type": "string"}},
        "time": {{"description": "Seconds to wait.", "type": "number"}},
        "status": {{"description": "Task status.", "type": "string", "enum": ["success", "failure"]}},
        "press_enter": {{"description": "Whether to press Enter after typing.", "type": "boolean"}},
        "delete_existing_text": {{"description": "Whether to clear existing text before typing.", "type": "boolean"}}
      }},
      "required": ["action"],
      "type": "object"
    }}
  }}
}}

For each function call, return a JSON object with the function name and arguments within XML tags:
<tool_call>
{{"name": "<function-name>", "arguments": <args-json-object>}}
</tool_call>
"""


def _round_by_factor(number: int, factor: int) -> int:
    return round(number / factor) * factor


def _ceil_by_factor(number: int, factor: int) -> int:
    return math.ceil(number / factor) * factor


def _floor_by_factor(number: int, factor: int) -> int:
    return math.floor(number / factor) * factor


def smart_resize(
    height: int,
    width: int,
    factor: int = IMAGE_FACTOR,
    min_pixels: int = MIN_PIXELS,
    max_pixels: int = MAX_PIXELS,
) -> tuple[int, int]:
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(f"aspect ratio too large: {max(height, width) / min(height, width)}")
    h_bar = max(factor, _round_by_factor(height, factor))
    w_bar = max(factor, _round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = _floor_by_factor(int(height / beta), factor)
        w_bar = _floor_by_factor(int(width / beta), factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = _ceil_by_factor(int(height * beta), factor)
        w_bar = _ceil_by_factor(int(width * beta), factor)
    return h_bar, w_bar


def build_system_prompt(resized_width: int, resized_height: int) -> str:
    return _FARA_OFFICIAL_SYSTEM.format(
        resized_width=resized_width,
        resized_height=resized_height,
    )


def pil_to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return raw
    if raw.startswith(("https://", "http://", "file://", "about:")):
        return raw
    if " " in raw:
        return f"https://www.bing.com/search?q={quote_plus(raw)}&FORM=QBLH"
    return f"https://{raw}"


# ── Parse (tolerate GGUF drift) ──


def _parse_fn_call_text(text: str) -> Optional[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    m = re.match(r"(\w+)\((.*)\)\s*$", text, re.DOTALL)
    if not m:
        return None
    name, inner = m.group(1), m.group(2).strip()
    if name == "visit_url":
        return {"action": "visit_url", "url": inner.strip().strip("'\"")}
    if name == "web_search":
        return {"action": "web_search", "query": inner.strip().strip("'\"")}
    if name in ("left_click", "click", "mouse_move", "hover"):
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", inner)]
        if len(nums) >= 2:
            return {"action": "left_click" if name == "click" else name, "coordinate": nums[:2]}
    if name == "scroll":
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", inner)]
        return {"action": "scroll", "pixels": int(nums[0]) if nums else -500}
    if name == "type":
        strs = re.findall(r"['\"]([^'\"]*)['\"]", inner)
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", inner)]
        args: dict[str, Any] = {"action": "type", "text": strs[0] if strs else inner}
        if len(nums) >= 2:
            args["coordinate"] = nums[:2]
        return args
    if name == "wait":
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", inner)]
        return {"action": "wait", "time": nums[0] if nums else 1}
    if name == "terminate":
        return {"action": "terminate", "status": inner.strip().strip("'\"") or "success"}
    if name == "history_back":
        return {"action": "history_back"}
    return None


def _coerce_tool_call(tool_call: dict[str, Any]) -> Optional[dict[str, Any]]:
    if "action" in tool_call and "name" not in tool_call:
        return tool_call
    name = tool_call.get("name")
    args = tool_call.get("arguments") or tool_call.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return _parse_fn_call_text(args)
    if not isinstance(args, dict):
        return None
    if name in ("computer_use", "computer", None) or "action" in args:
        if "action" in args:
            return args
        if "text" in args:
            return _parse_fn_call_text(str(args["text"]))
    return None


def _extract_balanced_json(text: str, start: int) -> Optional[dict[str, Any]]:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_fara_action(response: str) -> Optional[dict[str, Any]]:
    if not response:
        return None
    start = response.find("<tool_call>")
    if start != -1:
        start += len("<tool_call>")
        end = response.find("</tool_call>", start)
        json_str = response[start : end if end != -1 else None].strip()
        try:
            coerced = _coerce_tool_call(json.loads(json_str))
            if coerced:
                return coerced
        except json.JSONDecodeError:
            fn = _parse_fn_call_text(json_str)
            if fn:
                return fn
    for marker in (
        '{"name": "computer_use"',
        '{"name":"computer_use"',
        '{"name": "computer"',
        '{"name":"computer"',
        '{"action":',
    ):
        brace = response.find(marker)
        if brace == -1:
            continue
        obj = _extract_balanced_json(response, brace)
        if not obj:
            continue
        coerced = _coerce_tool_call(obj)
        if coerced:
            return coerced
    for match in re.finditer(
        r"\b(visit_url|web_search|left_click|click|mouse_move|scroll|type|wait|terminate|history_back)\([^)]*\)",
        response,
    ):
        fn = _parse_fn_call_text(match.group(0))
        if fn:
            return fn
    return None


def thoughts_from_response(response: str) -> str:
    """Official Fara uses pre-tool_call text as the terminate answer."""
    if "<tool_call>" in response:
        return response.split("<tool_call>", 1)[0].strip()
    return (response or "").strip()


# ── Multi-turn history (mirrors microsoft/fara FaraAgent) ──


@dataclass
class ChatMsg:
    role: str  # system | user | assistant
    text: str = ""
    image: Optional[Image.Image] = None
    is_original: bool = False


def prune_history(history: list[ChatMsg], max_images: int = MAX_N_IMAGES) -> list[ChatMsg]:
    """Keep last max_images screenshots; always keep original task text."""
    if max_images <= 0:
        return history
    out: list[ChatMsg] = []
    n_images = 0
    for msg in reversed(history):
        if msg.image is not None:
            if n_images < max_images:
                out.append(msg)
            elif msg.is_original:
                out.append(ChatMsg(role=msg.role, text=msg.text, image=None, is_original=True))
            n_images += 1
        else:
            out.append(msg)
    return list(reversed(out))


def history_to_openai(
    history: list[ChatMsg],
    system_prompt: str,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        if msg.role == "assistant":
            messages.append({"role": "assistant", "content": msg.text})
            continue
        content: list[dict[str, Any]] = []
        if msg.image is not None:
            content.append(
                {"type": "image_url", "image_url": {"url": pil_to_data_url(msg.image)}}
            )
        if msg.text:
            content.append({"type": "text", "text": msg.text})
        if content:
            messages.append({"role": "user", "content": content})
    return messages


@dataclass
class StepRecord:
    turn: int
    url: str
    action: Optional[dict[str, Any]]
    result: str
    model_raw: str
    elapsed_s: float
    im_size: Optional[list[int]] = None


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


class ThinFaraBrowser:
    """Playwright executor with official Magentic-style observation strings."""

    def __init__(self, page: Page, viewport: dict[str, int]):
        self.page = page
        self.viewport = viewport
        self.last_im_size: Optional[tuple[int, int]] = None
        self.facts: list[str] = []

    def scale_coords(self, coords: list[float]) -> tuple[float, float]:
        if not self.last_im_size or len(coords) < 2:
            return float(coords[0]), float(coords[1])
        im_w, im_h = self.last_im_size
        return (
            coords[0] * (self.viewport["width"] / im_w),
            coords[1] * (self.viewport["height"] / im_h),
        )

    async def screenshot_pil(self) -> Image.Image:
        png = await self.page.screenshot(type="png", full_page=False)
        return Image.open(io.BytesIO(png))

    def _on_login_wall(self) -> bool:
        url = (self.page.url or "").lower()
        return any(
            m in url
            for m in ("sign-in", "signin", "login", "oauth", "accounts.google", "github.com/login")
        )

    async def execute(self, action_args: dict[str, Any]) -> tuple[bool, str]:
        """Returns (is_terminate, observation_text). Observation phrasing matches official fara."""
        action = action_args.get("action") or ""
        if action == "click":
            action = "left_click"
        if action == "input_text":
            action = "type"

        # Only hard guard: read-only spike — no credential typing.
        if action == "type" and self._on_login_wall():
            return False, (
                "I cannot type credentials on a sign-in page (read-only). "
                "If the task needs a logged-in session, open the app URL from the task instead."
            )

        try:
            if action == "visit_url":
                url = str(action_args.get("url") or "").strip()
                if not url:
                    return False, "No URL provided."
                target = _normalize_url(url)
                await self.page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                return False, f"I typed '{url}' into the browser address bar."

            if action == "web_search":
                query = str(action_args.get("query") or "")
                target = f"https://www.bing.com/search?q={quote_plus(query)}&FORM=QBLH"
                await self.page.goto(target, wait_until="domcontentloaded", timeout=60_000)
                return False, f"I typed '{query}' into the browser search bar."

            if action == "left_click":
                coord = action_args.get("coordinate") or [0, 0]
                x, y = self.scale_coords(coord)
                await self.page.mouse.click(x, y)
                return False, f"I clicked at coordinates ({x:.1f}, {y:.1f})."

            if action in ("mouse_move", "hover"):
                coord = action_args.get("coordinate") or [0, 0]
                x, y = self.scale_coords(coord)
                await self.page.mouse.move(x, y)
                return False, f"I moved the cursor to ({x:.1f}, {y:.1f})."

            if action == "type":
                text = str(action_args.get("text") or "")
                # Official default press_enter=True
                press_enter = bool(action_args.get("press_enter", True))
                delete_existing = bool(action_args.get("delete_existing_text", False))
                coord = action_args.get("coordinate")
                if coord:
                    x, y = self.scale_coords(coord)
                    await self.page.mouse.click(x, y)
                    await asyncio.sleep(0.2)
                if delete_existing:
                    modifier = "Meta" if sys.platform == "darwin" else "Control"
                    await self.page.keyboard.press(f"{modifier}+A")
                    await self.page.keyboard.press("Backspace")
                if text:
                    await self.page.keyboard.type(text, delay=20)
                if press_enter:
                    await self.page.keyboard.press("Enter")
                return False, f"I typed '{text}'."

            if action == "scroll":
                # Official: page_up / page_down, not pixel wheel.
                pixels = int(action_args.get("pixels") or 0)
                if pixels > 0:
                    await self.page.keyboard.press("PageUp")
                    return False, "I scrolled up one page in the browser."
                await self.page.keyboard.press("PageDown")
                return False, "I scrolled down one page in the browser."

            if action in ("key", "keypress"):
                keys = action_args.get("keys") or []
                if isinstance(keys, str):
                    keys = [keys]
                for key in keys:
                    await self.page.keyboard.press(str(key))
                return False, f"I pressed the following keys: {keys}"

            if action == "history_back":
                await self.page.go_back(wait_until="domcontentloaded", timeout=30_000)
                return False, "I clicked the browser back button."

            if action == "wait":
                secs = float(action_args.get("time") or action_args.get("duration") or 3)
                await asyncio.sleep(secs)
                return False, "I am waiting a short period of time before taking further action."

            if action == "pause_and_memorize_fact":
                fact = str(action_args.get("fact") or "")
                if fact:
                    self.facts.append(fact)
                return False, f"I memorized the following fact: {fact}"

            if action == "terminate":
                status = action_args.get("status", "success")
                answer = (
                    action_args.get("answer")
                    or action_args.get("message")
                    or action_args.get("content")
                    or action_args.get("thoughts")
                    or ""
                )
                if self.facts and not answer:
                    answer = "; ".join(self.facts)
                return True, f"TERMINATE:{status}:{answer}"

            return False, f"Unknown action: {action}"
        except Exception as e:
            return False, f"Action failed: {e}"


async def call_fara(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, Any]],
) -> str:
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=1024,
        stop=["</tool_call>", "<|im_end|>", "<|endoftext|>"],
    )
    return (resp.choices[0].message.content or "").strip()


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

    print("=== browser_fara_probe (thin / official loop) ===")
    print(f"model={model}")
    print(f"prompt={prompt!r}")
    print(f"user_data_dir={user_data}")
    print(f"viewport={VIEWPORT}")
    print(f"max_n_images={MAX_N_IMAGES}")
    print(f"max_turns={'unlimited' if max_turns <= 0 else max_turns}")
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
        models = await client.models.list()
        ids = [m.id for m in models.data]
        print(f"[lmstudio] models: {ids}")
    except Exception as e:
        print(f"[FAIL] LM Studio unreachable at {base_url}: {e}")
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
            print(
                f"[FAIL] Could not launch Chrome with ChadBrowser profile: {e}\n"
                "Close any Chrome using that profile and retry."
            )
            return 1

        page = context.pages[0] if context.pages else await context.new_page()
        await page.set_viewport_size(VIEWPORT)
        browser = ThinFaraBrowser(page, VIEWPORT)
        await page.goto("about:blank")

        history: list[ChatMsg] = []
        turn = 0
        exit_code = 1

        try:
            # Initial screenshot + original task (is_original=True).
            shot = await browser.screenshot_pil()
            cfg = MLM_PROCESSOR_IM_CFG
            factor = cfg["patch_size"] * cfg["merge_size"]
            rh, rw = smart_resize(
                shot.height,
                shot.width,
                factor=factor,
                min_pixels=cfg["min_pixels"],
                max_pixels=cfg["max_pixels"],
            )
            resized = shot.resize((rw, rh))
            browser.last_im_size = (rw, rh)
            history.append(
                ChatMsg(role="user", text=prompt, image=resized, is_original=True)
            )

            while True:
                turn += 1
                if max_turns > 0 and turn > max_turns:
                    traj.final_status = "max_turns"
                    traj.failure_mode = f"hit max_turns={max_turns}"
                    print(f"\n[FAIL] Hit max_turns={max_turns} without terminate.")
                    break

                step_t0 = time.perf_counter()
                url = page.url
                print(f"\n--- turn {turn} ---")
                print(f"[url] {url}")

                # Subsequent turns: append follow-up user message with new screenshot.
                if turn > 1:
                    shot = await browser.screenshot_pil()
                    rh, rw = smart_resize(
                        shot.height,
                        shot.width,
                        factor=factor,
                        min_pixels=cfg["min_pixels"],
                        max_pixels=cfg["max_pixels"],
                    )
                    resized = shot.resize((rw, rh))
                    browser.last_im_size = (rw, rh)
                    trimmed = (url or "")[:100]
                    history.append(
                        ChatMsg(
                            role="user",
                            text=f"Current URL: {trimmed}\n{USER_FOLLOWUP}",
                            image=resized,
                        )
                    )

                # System prompt uses current image resolution (official does this each turn).
                system_prompt = build_system_prompt(rw, rh)
                pruned = prune_history(history, MAX_N_IMAGES)
                messages = history_to_openai(pruned, system_prompt)

                try:
                    raw = await call_fara(client, model, messages)
                except Exception as e:
                    traj.final_status = "model_error"
                    traj.failure_mode = str(e)
                    print(f"[FAIL] Fara call error: {e}")
                    break

                _safe_print(f"[fara] {raw[:600]}{'…' if len(raw) > 600 else ''}")
                history.append(ChatMsg(role="assistant", text=raw))

                action_args = parse_fara_action(raw)
                if not action_args:
                    traj.final_status = "parse_error"
                    traj.failure_mode = "no valid action in response"
                    traj.steps.append(
                        StepRecord(
                            turn=turn,
                            url=url,
                            action=None,
                            result="PARSE_ERROR",
                            model_raw=raw,
                            elapsed_s=time.perf_counter() - step_t0,
                            im_size=[rw, rh],
                        )
                    )
                    print("[FAIL] Could not parse Fara action.")
                    break

                print(f"[action] {json.dumps(action_args)}")
                is_stop, result = await browser.execute(action_args)
                print(f"[result] {result}")

                traj.steps.append(
                    StepRecord(
                        turn=turn,
                        url=url,
                        action=action_args,
                        result=result,
                        model_raw=raw,
                        elapsed_s=time.perf_counter() - step_t0,
                        im_size=[rw, rh],
                    )
                )

                if is_stop or result.startswith("TERMINATE:"):
                    parts = result.split(":", 2)
                    status = parts[1] if len(parts) > 1 else "success"
                    answer = parts[2] if len(parts) > 2 else ""
                    # Official: final answer is the thought text before tool_call.
                    if not answer.strip():
                        answer = thoughts_from_response(raw)
                    if browser.facts and not answer.strip():
                        answer = "; ".join(browser.facts)
                    login_required = "LOGIN REQUIRED" in answer.upper()
                    if login_required:
                        status = "login_required"
                    traj.final_status = status
                    traj.final_answer = answer
                    print(f"\n[final_answer] {answer}")
                    print(f"[status] {status}")
                    exit_code = (
                        0
                        if status == "success" and answer.strip() and not login_required
                        else 1
                    )
                    if login_required:
                        traj.failure_mode = "login wall"
                    elif exit_code != 0 and status == "success" and not answer.strip():
                        traj.failure_mode = "terminate success but empty answer"
                    break

                # Official waits ~3s for page settle.
                await asyncio.sleep(1.5)
        finally:
            traj.wall_s = time.perf_counter() - t0
            await context.close()

    runs_dir = Path(__file__).resolve().parent / "runs"
    runs_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = runs_dir / f"fara_{task_name}_{ts}.json"
    latest_path = runs_dir / "fara_latest.json"
    payload = traj.to_dict()
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== SUMMARY ===")
    print(f"turns: {len(traj.steps)}")
    print(f"wall_s: {traj.wall_s:.1f}")
    print(f"final_status: {traj.final_status}")
    print(f"final_answer: {traj.final_answer!r}")
    print(f"failure_mode: {traj.failure_mode!r}")
    print(f"trajectory: {out_path}")
    actions = [s.action.get("action") if s.action else None for s in traj.steps]
    print(f"actions: {actions}")
    ok = traj.final_status == "success" and bool(traj.final_answer.strip())
    print(f"PASS: {ok}")
    return 0 if ok else (exit_code if exit_code != 0 else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fara-7B thin browser probe (#288)")
    parser.add_argument("--task", choices=sorted(PRESET_TASKS), help="Preset task name")
    parser.add_argument("--prompt", default=None, help="Custom task prompt")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-turns", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if args.prompt:
        prompt = args.prompt
        task_name = "custom"
    elif args.task:
        prompt = PRESET_TASKS[args.task]
        task_name = args.task
    else:
        prompt = PRESET_TASKS["autotrader"]
        task_name = "autotrader"

    code = asyncio.run(
        run_probe(
            prompt=prompt,
            model=args.model,
            max_turns=args.max_turns,
            headless=args.headless,
            task_name=task_name,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
