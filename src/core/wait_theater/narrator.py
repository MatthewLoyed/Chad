"""
Wait-theater narrator — Phase C (GUI / text chat only).

Generates a few short, query-tailored status lines shown as on-screen text in the
chat thinking widget while the Brain prepares a reply. Runs in parallel with tool
selection. This is NOT voice mode: no TTS, no spoken filler, no voice-lane model.

No-lifecycle policy (strict):
- Raw HTTP ``/chat/completions`` only — same shape as ``dynamic_filler.py``.
- Never calls ``ensure_resident``, load, unload, or ``release`` — it claims no
  residency and can never cause a model to be loaded or evicted.
- Uses the tool-selector 9B only when it is ALREADY loaded in VRAM (often warm
  from the prior turn). Skips otherwise — Phase A fallback carousel still runs.
- It DOES gate its request on ``active_use_if_resident_sync``, which is not a residency
  claim (it grants nothing and never loads) but an atomic "is it resident? then pin it
  in flight" under the coordinator's own lock. That is required for correctness here
  (refs #592):

  A "resident?" check followed by a request is inherently TOCTOU. If the model is evicted
  in between — which the coordinator does routinely, since it swaps to stay inside the
  VRAM budget — this cosmetic call JIT-triggers a *reload*, then abandons it when its own
  8s timeout fires. LM Studio treats that client disconnect as "abort engine startup", so
  the load dies ("Engine protocol startup was aborted") and takes the tool selector's real
  request down with it ("Model is unloaded"), costing ~25-30s while everything reloads.
  A status line must never be able to do that, so if the model is gone we simply skip.

  Note the first attempt at this bracketed the request in plain ``active_use_sync``. That
  was worse than useless: it stops the coordinator EVICTING the model but does nothing to
  stop this code LOADING one, and because it blocks on the same lock a load holds, it
  widened the check→request gap from ~0 to ~10s. Atomicity is the point, not the marking.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from typing import Any, List, Optional, Tuple

from src.core.intelligence.model_swap import _same_model, get_loaded_model_ids
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("WaitNarrator")

_SYSTEM_PROMPT = (
    "You generate 3 short status lines for an AI assistant UI while it prepares a reply. "
    "Each line is 6-12 words, honest about working (not claiming results yet). "
    "Tailor lightly to the user's request topic — no tool names, no fake progress. "
    'Return ONLY a JSON array of exactly 3 strings, e.g. '
    '["Checking your calendar…", "Pulling recent threads…", "Almost ready…"]. '
    "No markdown fences, no explanation."
)

_DEFAULT_TIMEOUT_S = 8.0
_DEFAULT_MAX_TOKENS = 120
_MAX_LINES = 4
_MIN_LINE_LEN = 4


def _is_enabled() -> bool:
    return bool(ConfigManager.get("tools.wait_narrator_enabled", True))


def _is_model_loaded(loaded_ids: List[str], model_id: str) -> bool:
    return any(_same_model(m, model_id) for m in loaded_ids)


async def resolve_narrator_target(
    tool_registry: Any = None,
) -> Optional[Tuple[str, str]]:
    """Return ``(model_id, base_url)`` when the tool-selector 9B is already resident."""
    if not _is_enabled():
        return None

    selector = getattr(tool_registry, "_llm_selector", None) if tool_registry else None
    if not selector:
        return None

    selector_model = (
        getattr(selector, "_resolved_model_id", None)
        or getattr(selector, "_model_id", None)
    )
    if not selector_model:
        return None

    lm_cfg = ConfigManager.get_local_provider_config("lmstudio") or {}
    # rstrip("/") to match llm_selector.py: model_lifecycle keys its per-backend state by
    # the raw base_url STRING, so ".../v1" and ".../v1/" are two different states. Marking
    # active on the wrong key would make the guard below a silent no-op — the exact class
    # of failure #592 was.
    base_url = lm_cfg.get("base_url", "http://localhost:1234/v1").rstrip("/")
    loaded = await get_loaded_model_ids(base_url)
    if not loaded or not _is_model_loaded(loaded, selector_model):
        return None

    return selector_model, base_url


def _parse_lines(raw: str) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []

    # Strip optional markdown fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            lines = [str(x).strip().strip('"').strip("'") for x in parsed if str(x).strip()]
            return _normalize_lines(lines)
    except json.JSONDecodeError:
        pass

    lines = [ln.strip().strip('"').strip("'") for ln in text.splitlines() if ln.strip()]
    return _normalize_lines(lines)


def _normalize_lines(lines: List[str]) -> List[str]:
    out: List[str] = []
    for line in lines:
        cleaned = re.sub(r"\s+", " ", line).strip()
        if len(cleaned) < _MIN_LINE_LEN:
            continue
        if cleaned not in out:
            out.append(cleaned)
        if len(out) >= _MAX_LINES:
            break
    return out


async def generate_wait_narrator_lines(
    text: str,
    model: str,
    base_url: str = "http://localhost:1234/v1",
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> Optional[List[str]]:
    """Ask a resident fast local model for wait-theater lines tailored to ``text``."""
    try:
        return await asyncio.to_thread(
            _generate_sync, text, model, base_url, timeout,
        )
    except Exception as e:
        logger.debug(f"Wait narrator call failed: {e}")
        return None


def _generate_sync(
    text: str,
    model: str,
    base_url: str,
    timeout: float,
) -> Optional[List[str]]:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "max_tokens": _DEFAULT_MAX_TOKENS,
    }
    if "qwen3" in model.lower():
        payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # See the module docstring. The residency check in `resolve_narrator_target` is only
    # advisory — by the time we get here the model may already have been swapped out, and
    # issuing the request anyway would JIT-load it, where a competing load aborts it. So
    # re-check and pin atomically, and simply skip if it is no longer resident.
    from src.core.intelligence.model_lifecycle import active_use_if_resident_sync
    try:
        with active_use_if_resident_sync(base_url, model) as resident:
            if not resident:
                logger.debug(f"Wait narrator skipped: '{model}' no longer resident.")
                return None
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug(f"Wait narrator unreachable: {e}")
        return None

    try:
        raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, AttributeError):
        return None

    lines = _parse_lines(raw)
    return lines or None


async def try_generate_narrator_lines(
    text: str,
    tool_registry: Any = None,
) -> Optional[List[str]]:
    """Resident-only entry point — returns None when no suitable model is loaded."""
    target = await resolve_narrator_target(tool_registry)
    if not target:
        return None
    model, base_url = target
    return await generate_wait_narrator_lines(text, model=model, base_url=base_url)
