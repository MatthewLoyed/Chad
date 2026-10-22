"""
LM Studio model residency — the single coordinator every load/unload decision must go
through.

Historically almost every subsystem that needed a model resident (tool selector, intent
router, model router warmup, subagent handoff, scout's secondary-model gather, browser
vision-assist) talked to LM Studio directly with its own private "list loaded -> decide ->
load/unload" implementation. None of those shared a lock, a registry of what's currently
wanted, or a VRAM budget, so two lanes could race the same decision (LM Studio's own
documented bug: concurrent JIT load requests for the same model can spin up *duplicate*
instances and OOM the box) or evict a model another lane still needed.

This module is now that coordination point:

- ``model_swap.py`` is the low-level primitive layer (list/load/unload) — now a thin façade
  that dispatches to a per-backend strategy under ``src/core/intelligence/providers/``
  (LM Studio, Ollama, static llama.cpp/vLLM), so this coordinator is provider-agnostic (#450).
- Every load/unload decision here is serialized per base_url behind one
  ``asyncio.Condition`` running on a single dedicated background event loop, so sync callers
  (``llm_selector.py``, ``intent_mode_router.py``, ``model_router.py``) and async callers
  (``turn_coordinator.py``, ``handoffs.py``, ``scout_node.py``, ``executor_strategy.py``) all
  serialize against the *same* state instead of racing each other.
- A "wanted" registry (model_id -> set of owner tags) means ``release()`` only actually
  unloads a model once every owner that asked for it is done with it.
- A best-effort VRAM budget (``local_models.lifecycle.vram_budget_gb`` +
  ``model_size_estimates_gb``) decides whether an incoming model can coexist with what's
  already resident, evicting unwanted (zero-owner) models first.
- A lightweight priority queue lets background/autonomous callers (heartbeat, cron) wait for
  an active interactive turn to go idle instead of fighting it for VRAM.

Convention: **new code must never call ``model_swap.py`` directly or hand-roll another
load/unload implementation** — always go through ``ensure_resident``/``release`` (or their
``*_sync`` counterparts) here. See ``docs/features/MODEL_LIFECYCLE.md``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from src.core.intelligence.model_swap import get_loaded_model_ids, load_model, unload_model
from src.shared.turn_mode import canonical_from_ui, normalize_turn_mode

if TYPE_CHECKING:
    from src.core.orchestration.turn_coordinator import TurnCoordinator

logger = logging.getLogger("ModelLifecycle")

# Shared owner tag for "the supervisor's main chat model". Every call site that needs to
# explicitly claim or force-release main-model residency (turn preload, subagent swap,
# scout's secondary-model gather) uses this SAME tag so they coordinate through one
# wanted-set entry instead of guessing at each other's intent.
OWNER_MAIN_MODEL = "supervisor_main"

# Shared owner tag for "the tool selector's model" (refs #592). ``llm_selector.py``'s own
# claim/release AND ``warm_models``'s "keep it hot at rest" claim must use this SAME tag —
# a claim under any other tag (e.g. the generic "warm") would never get released by
# anything, permanently blocking the selector's own post-use unload regardless of what
# it releases under its own tag.
OWNER_TOOL_SELECTOR = "tool_selector"

DEFAULT_LOAD_TIMEOUT = 180.0
DEFAULT_BACKGROUND_QUEUE_TIMEOUT = 120.0
DEFAULT_INTERACTIVE_ACTIVE_WINDOW_S = 20.0
_UNKNOWN_MODEL_SIZE_GB = 8.0


def current_priority() -> str:
    """"background" for an autonomous/heartbeat/cron turn, else "interactive". Every
    model-lifecycle call site in the turn pipeline (tool selector, intent router, main-model
    preload, subagent swap) reads this at call time.

    Backed by ``turn_priority_var`` (NOT ``turn_category_var``) — ``turn_category_var`` gets
    overwritten by mode resolution partway through a turn (the raw "autonomous" category is
    replaced with whatever chat/agent/plan lane the intent router resolves to), which would
    silently lose the autonomous marker before any tool-selection or model-preload call ever
    reads it. ``turn_priority_var`` is set once from the RAW incoming category at the very top
    of ``turn_coordinator._execute_turn_internal_wrapped`` and never touched again.

    Call this at the point of the ensure_resident/release call (same thread/task as the caller)
    rather than trying to read it from inside the coordinator's background loop — ContextVars
    don't cross the ``run_coroutine_threadsafe`` boundary into that loop's own thread.
    """
    try:
        from src.core.orchestration.context import turn_priority_var
        return turn_priority_var.get()
    except Exception:
        return "interactive"


def _clean_model_id(model_id: Any) -> str:
    if isinstance(model_id, dict):
        model_id = model_id.get("name") or model_id.get("model_name") or ""
    if not isinstance(model_id, str):
        model_id = str(model_id or "")
    known_prefixes = ("ollama/", "openai/", "lm_studio/", "huggingface/", "vllm/", "gemini/")
    for prefix in known_prefixes:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def _same_model(a: str, b: str) -> bool:
    a_clean = _clean_model_id(a).split(":")[0]
    b_clean = _clean_model_id(b).split(":")[0]
    return a_clean == b_clean


# ---------------------------------------------------------------------------
# Per-base-url coordination state
# ---------------------------------------------------------------------------


@dataclass
class _BaseUrlState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    # model_id -> owner tags currently claiming it resident.
    wanted: Dict[str, Set[str]] = field(default_factory=dict)
    # monotonic timestamp a model was last touched (registered or reconfirmed) — LRU order.
    last_touch: Dict[str, float] = field(default_factory=dict)
    # monotonic timestamp of the most recent interactive-priority claim. Self-decaying
    # "is a live turn using this LM Studio instance right now" signal — see
    # DEFAULT_INTERACTIVE_ACTIVE_WINDOW_S. Deliberately NOT a persistent
    # begin/end-of-turn flag: owners like OWNER_MAIN_MODEL are claimed once and often never
    # explicitly released, so a sticky boolean would starve background callers forever.
    last_interactive_touch: float = 0.0
    # model_id (cleaned base name) -> count of in-flight requests currently being served BY
    # that model. Distinct from `wanted` (which just means "should stay resident") — a model
    # can be wanted but idle between requests, and eviction is fine then. `active` means an
    # HTTP request is on the wire right now; see `mark_active`/`mark_inactive`. Eviction must
    # never target an active model — that's what caused the "Model is unloaded" abort
    # mid-request (see llm_selector.py's HTTPError-retry fix and this module's `_make_room`).
    active: Dict[str, int] = field(default_factory=dict)


_states: Dict[str, _BaseUrlState] = {}
_states_lock = threading.Lock()

_bg_loop: Optional[asyncio.AbstractEventLoop] = None
_bg_thread: Optional[threading.Thread] = None
_bg_loop_lock = threading.Lock()


def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    """One dedicated event loop owns every ``_BaseUrlState`` (and its ``asyncio.Condition``,
    which binds to whichever loop first awaits it). Sync and async callers alike schedule
    onto this loop via ``run_coroutine_threadsafe`` so all coordination state lives on a
    single loop regardless of which thread/loop the caller is on."""
    global _bg_loop, _bg_thread
    with _bg_loop_lock:
        if _bg_loop is None:
            _bg_loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(_bg_loop)
                _bg_loop.run_forever()

            _bg_thread = threading.Thread(target=_run, daemon=True, name="model-lifecycle-loop")
            _bg_thread.start()
        return _bg_loop


def _state_for(base_url: str) -> _BaseUrlState:
    with _states_lock:
        state = _states.get(base_url)
        if state is None:
            state = _BaseUrlState()
            _states[base_url] = state
        return state


# ---------------------------------------------------------------------------
# VRAM budget config
# ---------------------------------------------------------------------------


def _vram_budget_gb() -> float:
    from src.shared.config_manager import ConfigManager
    return float(ConfigManager.get("local_models.lifecycle.vram_budget_gb", 0) or 0)


def _model_size_table() -> Dict[str, float]:
    from src.shared.config_manager import ConfigManager
    table = ConfigManager.get("local_models.lifecycle.model_size_estimates_gb") or {}
    return {str(k): float(v) for k, v in table.items()}


def _background_queue_timeout() -> float:
    from src.shared.config_manager import ConfigManager
    return float(
        ConfigManager.get("local_models.lifecycle.background_queue_timeout_s", DEFAULT_BACKGROUND_QUEUE_TIMEOUT)
    )


def _interactive_active_window() -> float:
    from src.shared.config_manager import ConfigManager
    return float(
        ConfigManager.get(
            "local_models.lifecycle.interactive_active_window_s", DEFAULT_INTERACTIVE_ACTIVE_WINDOW_S
        )
    )


def _pinned_patterns() -> List[str]:
    from src.shared.config_manager import ConfigManager
    raw = ConfigManager.get("local_models.lifecycle.pinned_models") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(p) for p in raw if str(p).strip()]


def is_pinned(model_id: str) -> bool:
    """Is ``model_id`` in the always-resident set (``local_models.lifecycle.pinned_models``)?

    Substring-matched case-insensitively against the cleaned id, exactly like
    ``model_size_estimates_gb`` — so ``"gemma-4-26b"`` covers every quant/prefix variant.

    Pinning exists because on a single-GPU box the routine supervisor<->selector swap costs
    far more than co-residency does. The two models a normal turn needs (main chat model +
    9B tool selector) don't fit in VRAM together, so budget enforcement evicted one to load
    the other on EVERY turn — a ~15.6GB reload per message, which stalls the whole desktop
    while it streams off disk. Pinning both makes that swap disappear; LM Studio spills
    whatever doesn't fit onto CPU RAM rather than failing (verified in #592), so the trade is
    a somewhat slower selector for zero thrash.

    Deliberately NOT absolute: see ``_make_room``. A pinned model still gets evicted for a
    genuinely large NON-pinned model (coder-35b, browser VL-8B), because those really don't
    fit alongside it and refusing to evict would just OOM the box.
    """
    key = _clean_model_id(model_id).lower()
    return any(p.lower() in key for p in _pinned_patterns())


def _estimate_size_gb(model_id: str, size_table: Dict[str, float]) -> float:
    key = _clean_model_id(model_id).lower()
    for pattern, gb in size_table.items():
        if pattern.lower() in key:
            return gb
    return _UNKNOWN_MODEL_SIZE_GB


def _eval_allows_load(model_id: str, owner: str) -> bool:
    """Under ``CHAD_TESTING`` (dry-run / unit tests), only the tool-selector model may load.

    Production residency choreography (post-turn ``schedule_warm`` of the main chat model,
    subagent ``_swap_back`` reclaiming Gemma 26B, intent-router gemma-1b, browser_agent pin)
    must not touch LM Studio during a SpyAdapter eval turn — that fight is exactly #632.
    """
    import os
    if os.getenv("CHAD_TESTING") != "true":
        return True
    try:
        from src.shared.config_manager import ConfigManager
        allowed = ConfigManager.get_llm_selector_model()
    except Exception:
        allowed = "qwen2.5-3b-instruct"
    if allowed and _same_model(model_id, allowed):
        return True
    import traceback
    stack = "".join(traceback.format_stack(limit=16))
    logger.warning(
        f"[ MODEL_LIFECYCLE ] EVAL HARD GATE refused load of '{model_id}' "
        f"(owner={owner}); only selector '{allowed}' is allowed under CHAD_TESTING.\n{stack}"
    )
    return False


def _has_known_size(model_id: str, size_table: Dict[str, float]) -> bool:
    key = _clean_model_id(model_id).lower()
    return any(pattern.lower() in key for pattern in size_table)


def would_coexist(model_ids: List[str]) -> bool:
    """Best-effort check: could all of ``model_ids`` be concurrently resident within the
    configured VRAM budget? Used by parallel-delegate fan-out (#513) to decide whether a batch
    of specialist handoffs is safe to run concurrently, or must fall back to serial.

    Reuses the exact same size table + budget ``_make_room`` already uses for eviction — this
    is the same question ("do these fit together"), just asked ahead of time instead of after
    a load. Distinct model ids only count once (matches how N `parallel_research` workers
    already share one resident model without needing N times the budget).

    Conservative by design: a model with no size-table match (e.g. today's `qwen3-coder-30b`,
    which has no `"30b"` entry) is treated as NOT eligible for concurrent batching rather than
    guessed at — an unknown-size model could be huge, and the whole point of this gate is to
    keep the big coder model off the concurrent path until it's deliberately sized.
    """
    budget_gb = _vram_budget_gb()
    if budget_gb <= 0:
        # No budget configured — mirrors _make_room's own no-op in this case (falls back to
        # LM Studio's own auto-evict, which this module already treats as "unconstrained").
        return True

    size_table = _model_size_table()
    unique_ids: List[str] = []
    for m in model_ids:
        if not any(_same_model(m, u) for u in unique_ids):
            unique_ids.append(m)

    total = 0.0
    for m in unique_ids:
        if not _has_known_size(m, size_table):
            return False
        total += _estimate_size_gb(m, size_table)
    return total <= budget_gb


async def _make_room(
    base_url: str,
    state: "_BaseUrlState",
    incoming_model_id: str,
    loaded_ids: List[str],
) -> None:
    """Best-effort VRAM budget enforcement before loading ``incoming_model_id``.

    Evicts zero-owner ("unwanted") resident models first, LRU order. Only if that still
    isn't enough does it fall back to evicting a wanted model (logged as a warning) — the
    incoming load always wins rather than blocking, matching the codebase's existing
    "let JIT own the reload" philosophy (see scout_node._maybe_unload_main_model). No-ops
    entirely when no budget is configured (``vram_budget_gb`` <= 0).

    A model with an in-flight request (``state.active`` > 0, see ``mark_active``) is NEVER
    evicted, wanted or not — unloading a model mid-request is what produces LM Studio's
    "Model is unloaded" 400 and aborts whatever was using it. If every resident model is
    active, the budget is exceeded and the incoming load proceeds anyway (best-effort; see
    module docstring — this already only ever "evicts", never blocks).
    """
    budget_gb = _vram_budget_gb()
    if budget_gb <= 0:
        return

    size_table = _model_size_table()
    incoming_size = _estimate_size_gb(incoming_model_id, size_table)
    resident_others = [m for m in loaded_ids if not _same_model(m, incoming_model_id)]
    projected = incoming_size + sum(_estimate_size_gb(m, size_table) for m in resident_others)
    if projected <= budget_gb:
        return

    def _touch(model_id: str) -> float:
        return state.last_touch.get(_clean_model_id(model_id).split(":")[0], 0.0)

    def _is_wanted(model_id: str) -> bool:
        return any(_same_model(model_id, m) and owners for m, owners in state.wanted.items())

    def _is_active(model_id: str) -> bool:
        return state.active.get(_clean_model_id(model_id).split(":")[0], 0) > 0

    evictable = [m for m in resident_others if not _is_active(m)]
    skipped_active = [m for m in resident_others if _is_active(m)]
    if skipped_active:
        logger.debug(
            f"[ MODEL_LIFECYCLE ] Never evicting active model(s) {skipped_active} "
            f"while loading '{incoming_model_id}'."
        )

    # Pinned models never evict EACH OTHER (see `is_pinned`). This is the whole point of the
    # pin: the main chat model and the 9B tool selector are both needed by every turn and
    # don't fit in VRAM together, so budget enforcement used to swap them on every message.
    # A non-pinned incoming model (coder-35b, browser VL-8B) still evicts pinned ones
    # normally — those genuinely don't fit, and protecting the pin there would OOM the box.
    if is_pinned(incoming_model_id):
        skipped_pinned = [m for m in evictable if is_pinned(m)]
        if skipped_pinned:
            logger.debug(
                f"[ MODEL_LIFECYCLE ] Keeping pinned model(s) {skipped_pinned} resident "
                f"alongside pinned '{incoming_model_id}' (over budget by design — LM Studio "
                f"spills the overflow to CPU rather than failing)."
            )
            evictable = [m for m in evictable if not is_pinned(m)]

    unwanted = sorted((m for m in evictable if not _is_wanted(m)), key=_touch)
    for model_id in unwanted:
        if projected <= budget_gb:
            break
        logger.info(
            f"[ MODEL_LIFECYCLE ] Evicting unwanted '{model_id}' to fit '{incoming_model_id}' "
            f"within {budget_gb}GB budget."
        )
        await unload_model(base_url, model_id)
        projected -= _estimate_size_gb(model_id, size_table)

    if projected > budget_gb:
        wanted_others = sorted(evictable, key=_touch)
        for model_id in wanted_others:
            if projected <= budget_gb:
                break
            logger.warning(
                f"[ MODEL_LIFECYCLE ] VRAM budget ({budget_gb}GB) still exceeded — evicting "
                f"wanted '{model_id}' to fit '{incoming_model_id}'. It will JIT-reload next "
                f"time its owner needs it."
            )
            await unload_model(base_url, model_id)
            projected -= _estimate_size_gb(model_id, size_table)


# ---------------------------------------------------------------------------
# Core coordinator implementation (always runs on the shared background loop)
# ---------------------------------------------------------------------------


async def _ensure_resident_impl(
    base_url: str,
    model_id: str,
    owner: str,
    priority: str,
    timeout: float,
    queue_timeout: Optional[float],
    context_length: Optional[int] = None,
    enforce_budget: bool = True,
) -> bool:
    # Eval/dry-run hard gate (#632): CHAD_TESTING means SpyAdapter turns — the only LM Studio
    # model that may load is the 9B tool selector. Anything else (main chat Gemma 26B via
    # schedule_warm / _swap_back / browser_agent pin, intent-router gemma-1b, …) is refused
    # with a stack so the caller is obvious in the log.
    if not _eval_allows_load(model_id, owner):
        return False

    state = _state_for(base_url)

    async with state.condition:
        if priority == "background":
            deadline = time.monotonic() + (queue_timeout or _background_queue_timeout())
            window = _interactive_active_window()
            while True:
                age = time.monotonic() - state.last_interactive_touch
                if age >= window:
                    # Interactive activity has genuinely gone quiet — proceed now instead of
                    # sleeping for the rest of the queue deadline (the bug: a single stale
                    # last_interactive_touch from e.g. a connect-preload used to make an
                    # isolated background load wait out the *entire* queue_timeout, since
                    # nothing else was around to notify() it awake).
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        f"[ MODEL_LIFECYCLE ] Background load of '{model_id}' (owner={owner}) "
                        f"timed out waiting for the active interactive turn — proceeding anyway."
                    )
                    break
                # Wake at the earlier of "window has elapsed" or "queue deadline hit" — not
                # the full remaining deadline — so a quiet LM Studio is detected within
                # `window` seconds instead of only when something else happens to notify().
                try:
                    await asyncio.wait_for(state.condition.wait(), timeout=min(window - age, remaining))
                except asyncio.TimeoutError:
                    pass
        else:
            state.last_interactive_touch = time.monotonic()

        state.wanted.setdefault(model_id, set()).add(owner)
        state.last_touch[_clean_model_id(model_id).split(":")[0]] = time.monotonic()

        try:
            loaded = await get_loaded_model_ids(base_url)
            if any(_same_model(m, model_id) for m in loaded):
                # Already resident: no new VRAM is being requested, so `_make_room` has
                # nothing useful to do here. Reclaiming a co-resident model that pushes us
                # over budget is `release`'s job (its owner finished), not this path's —
                # doing it here makes a mere claim evict models out from under other owners.
                return True

            if enforce_budget:
                await _make_room(base_url, state, model_id, loaded)
            # else: deliberately co-resident (refs #592) — used only by warm_models's
            # "keep both hot at rest" path. LM Studio auto-splits GPU/CPU for whatever
            # doesn't fit rather than failing, so this is safe; it's just slower for
            # whichever model doesn't fully fit until the real turn's budget-enforced
            # calls (this flag defaults True everywhere else) evict it properly.
            ok = await load_model(base_url, model_id, timeout=timeout, context_length=context_length)
            if ok:
                logger.info(f"[ MODEL_LIFECYCLE ] '{model_id}' resident (owner={owner}).")
            return ok
        finally:
            state.condition.notify_all()


async def _release_impl(base_url: str, model_id: str, owner: str) -> None:
    state = _state_for(base_url)
    should_unload = False
    async with state.condition:
        owners = state.wanted.get(model_id)
        if owners:
            owners.discard(owner)
        if owners is not None and not owners:
            state.wanted.pop(model_id, None)
            if state.active.get(_clean_model_id(model_id).split(":")[0], 0) > 0:
                # Still serving an in-flight request — never unload out from under it.
                # It's now zero-owner ("unwanted"), so a future _make_room pass will
                # reclaim it once it goes idle, same as any other unwanted model.
                logger.debug(
                    f"[ MODEL_LIFECYCLE ] '{model_id}' has no remaining owners but is "
                    "active — deferring unload until the in-flight request finishes."
                )
            else:
                logger.info(f"[ MODEL_LIFECYCLE ] '{model_id}' has no remaining owners — releasing.")
                should_unload = True
        state.condition.notify_all()
    if should_unload:
        await unload_model(base_url, model_id)


async def _mark_active_impl(base_url: str, model_id: str, delta: int) -> None:
    state = _state_for(base_url)
    async with state.condition:
        key = _clean_model_id(model_id).split(":")[0]
        state.active[key] = max(0, state.active.get(key, 0) + delta)
        if state.active[key] == 0:
            state.active.pop(key, None)
        state.condition.notify_all()


async def _mark_active_if_resident_impl(base_url: str, model_id: str) -> bool:
    """Check residency and claim in-flight status ATOMICALLY, under the state lock."""
    state = _state_for(base_url)
    async with state.condition:
        # Taking the lock also means we cannot observe a half-finished load: whoever is
        # loading holds this same lock for the duration, so we either run before it starts
        # or after it completes — never in the window where a JIT request would race it.
        loaded = await get_loaded_model_ids(base_url)
        if not any(_same_model(m, model_id) for m in loaded):
            return False
        key = _clean_model_id(model_id).split(":")[0]
        state.active[key] = state.active.get(key, 0) + 1
        state.condition.notify_all()
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ensure_resident(
    base_url: str,
    model_id: str,
    owner: str,
    *,
    priority: str = "interactive",
    timeout: Optional[float] = None,
    queue_timeout: Optional[float] = None,
    context_length: Optional[int] = None,
    enforce_budget: bool = True,
) -> bool:
    """Claim ``model_id`` resident on behalf of ``owner``, loading it if needed.

    ``priority="background"`` (heartbeat/cron/autonomous callers) waits for the active
    interactive turn to go idle before evicting/loading, instead of fighting a live chat
    for VRAM. Idempotent per owner — calling this again for the same owner/model just
    refreshes its claim, it never double-loads.

    ``enforce_budget=False`` (refs #592) skips the VRAM-budget eviction that would
    otherwise free room by evicting something else — for ``warm_models``'s "keep
    everything hot at rest" calls, where evicting the OTHER model we just warmed would
    defeat the entire point. Every turn-path caller keeps the default (True): the real
    swap during a turn must still evict whatever the active step doesn't need.
    """
    if not model_id or not base_url:
        return False
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(
        _ensure_resident_impl(
            base_url, model_id, owner, priority, timeout or DEFAULT_LOAD_TIMEOUT, queue_timeout,
            context_length, enforce_budget,
        ),
        loop,
    )
    return await asyncio.wrap_future(fut)


def ensure_resident_sync(
    base_url: str,
    model_id: str,
    owner: str,
    *,
    priority: str = "interactive",
    timeout: Optional[float] = None,
    queue_timeout: Optional[float] = None,
    context_length: Optional[int] = None,
) -> bool:
    """Sync bridge for callers that aren't in an async context (e.g. ``llm_selector.py``)."""
    if not model_id or not base_url:
        return False
    load_timeout = timeout or DEFAULT_LOAD_TIMEOUT
    wait_timeout = load_timeout + (queue_timeout or _background_queue_timeout()) + 10.0
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(
        _ensure_resident_impl(
            base_url, model_id, owner, priority, load_timeout, queue_timeout, context_length
        ),
        loop,
    )
    try:
        return fut.result(timeout=wait_timeout)
    except Exception as e:
        logger.debug(f"[ MODEL_LIFECYCLE ] ensure_resident_sync('{model_id}') failed/timed out: {e}")
        return False


async def release(base_url: str, model_id: str, owner: str) -> None:
    """Drop ``owner``'s claim on ``model_id``; unloads only once no owner wants it."""
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_release_impl(base_url, model_id, owner), loop)
    await asyncio.wrap_future(fut)


def release_sync(base_url: str, model_id: str, owner: str, timeout: float = 30.0) -> None:
    """Sync bridge for ``release`` — fire-and-forget-safe; failures are logged, not raised."""
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_release_impl(base_url, model_id, owner), loop)
    try:
        fut.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"[ MODEL_LIFECYCLE ] release_sync('{model_id}') failed/timed out: {e}")


async def mark_active(base_url: str, model_id: str) -> None:
    """Record an in-flight request against ``model_id`` — while this count is > 0, the
    coordinator will never evict it (wanted or not), no matter how tight the VRAM budget
    gets. Callers MUST pair this with ``mark_inactive`` in a ``finally`` (or use the
    ``active_use``/``active_use_sync`` context managers below, which do this for you).
    """
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_mark_active_impl(base_url, model_id, 1), loop)
    await asyncio.wrap_future(fut)


async def mark_inactive(base_url: str, model_id: str) -> None:
    """Drop one in-flight-request count previously registered by ``mark_active``."""
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_mark_active_impl(base_url, model_id, -1), loop)
    await asyncio.wrap_future(fut)


def mark_active_sync(base_url: str, model_id: str, timeout: float = 10.0) -> None:
    """Sync bridge for ``mark_active`` (e.g. ``llm_selector.py``, ``intent_mode_router.py``,
    which talk to LM Studio over plain ``urllib`` rather than an async client)."""
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_mark_active_impl(base_url, model_id, 1), loop)
    try:
        fut.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"[ MODEL_LIFECYCLE ] mark_active_sync('{model_id}') failed/timed out: {e}")


def mark_inactive_sync(base_url: str, model_id: str, timeout: float = 10.0) -> None:
    """Sync bridge for ``mark_inactive``."""
    if not model_id or not base_url:
        return
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(_mark_active_impl(base_url, model_id, -1), loop)
    try:
        fut.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"[ MODEL_LIFECYCLE ] mark_inactive_sync('{model_id}') failed/timed out: {e}")


@asynccontextmanager
async def active_use(base_url: str, model_id: str):
    """``async with active_use(base_url, model_id):`` around the actual inference call —
    guarantees the model can't be evicted for the duration, and always clears the mark
    even if the call raises."""
    await mark_active(base_url, model_id)
    try:
        yield
    finally:
        await mark_inactive(base_url, model_id)


@contextmanager
def active_use_sync(base_url: str, model_id: str):
    """Sync counterpart of ``active_use`` for ``urllib``-based callers."""
    mark_active_sync(base_url, model_id)
    try:
        yield
    finally:
        mark_inactive_sync(base_url, model_id)


@contextmanager
def active_use_if_resident_sync(base_url: str, model_id: str, timeout: float = 30.0):
    """``with active_use_if_resident_sync(...) as ok:`` — yields True only when ``model_id``
    was resident, and holds it unevictable for the block.

    For opportunistic callers that must never CAUSE a load (refs #592). A plain
    "is it loaded?" check followed by a request is a TOCTOU bug: the model can be evicted
    in between, and the request then JIT-loads it — which on LM Studio means a competing
    JIT load can abort it mid-flight, killing whatever real work was waiting on that model.
    Checking and marking under one lock closes that window, and because loads hold the same
    lock this can never observe a partially-loaded model either.
    """
    ok = False
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(
        _mark_active_if_resident_impl(base_url, model_id), loop
    )
    try:
        ok = fut.result(timeout=timeout)
    except Exception as e:  # noqa: BLE001 — opportunistic: on any failure just skip
        logger.debug(f"[ MODEL_LIFECYCLE ] active_use_if_resident_sync('{model_id}') failed: {e}")
    try:
        yield ok
    finally:
        if ok:
            mark_inactive_sync(base_url, model_id)


def list_loaded_sync(base_url: str, timeout: float = 5.0) -> List[str]:
    """Provider-correct list of *resident/serving* model ids for ``base_url`` (sync bridge).

    Delegates to the resolved :class:`ProviderLifecycle` for ``base_url`` so callers get the
    right answer for every backend without knowing which one they're on: LM Studio and static
    single-model endpoints list via OpenAI-compat ``/v1/models`` (which returns *loaded*
    models), while Ollama overrides it to hit ``/api/ps`` (``/v1/models`` on Ollama lists
    *pulled*, not *resident*, models — a distinction sync ``urllib`` callers used to get wrong).
    Non-fatal — returns ``[]`` on any error or unreachable server.
    """
    if not base_url:
        return []
    from src.core.intelligence.providers import get_provider_for

    provider = get_provider_for(base_url)
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(provider.list_loaded(timeout=timeout), loop)
    try:
        return fut.result(timeout=timeout + 2.0)
    except Exception as e:  # noqa: BLE001 — liveness probe, never fatal
        logger.debug(f"[ MODEL_LIFECYCLE ] list_loaded_sync('{base_url}') failed: {e}")
        return []


def get_diagnostics(base_url: str) -> Dict[str, Any]:
    """Snapshot of coordinator state for a base_url — safe to log."""
    state = _states.get(base_url)
    if not state:
        return {"wanted": {}, "interactive_active": False}
    return {
        "wanted": {m: sorted(owners) for m, owners in state.wanted.items() if owners},
        "active": dict(state.active),
        "interactive_active": (time.monotonic() - state.last_interactive_touch) < _interactive_active_window(),
        "last_interactive_touch_age_s": (
            round(time.monotonic() - state.last_interactive_touch, 1) if state.last_interactive_touch else None
        ),
    }


async def ensure_model_loaded(
    base_url: str, model_id: str, enforce_budget: bool = True, owner: str = "warm"
) -> bool:
    """Thin wrapper over ``ensure_resident`` for the connect/mode-change warm path.

    ``owner`` defaults to ``"warm"`` — a tag nothing ever releases, matching the original
    "make sure it's loaded, permanently" intent. ``warm_models`` overrides this to
    ``OWNER_TOOL_SELECTOR`` for the selector specifically (refs #592): that owner tag is
    the SAME one ``llm_selector``'s own claim and its post-use ``release()`` use, so the
    real turn's own release actually empties the claim set and unloads it — a warm claim
    left under the generic "warm" tag would sit in ``state.wanted`` forever (nothing
    releases "warm"), permanently blocking that unload regardless of what the selector
    itself released.
    """
    return await ensure_resident(
        base_url, model_id, owner=owner, priority="interactive", enforce_budget=enforce_budget
    )


# ---------------------------------------------------------------------------
# Turn-mode residency policy (unchanged from before — now backed by the coordinator)
# ---------------------------------------------------------------------------


def selector_model_id(turn_coordinator: Optional["TurnCoordinator"] = None) -> Optional[str]:
    tc = turn_coordinator
    if tc:
        registry = getattr(tc, "tool_registry", None)
        sel = getattr(registry, "_llm_selector", None) if registry else None
        if sel:
            return getattr(sel, "_model_id", None)
    from src.shared.config_manager import ConfigManager
    return ConfigManager.get_llm_selector_model()


def router_model_id() -> Optional[str]:
    from src.shared.config_manager import ConfigManager
    ir = ConfigManager.get("intent_router") or {}
    if not ir.get("enabled", True):
        return None
    return ir.get("model") or "google/gemma-3-1b"


def _main_adapter_target(turn_coordinator: Optional["TurnCoordinator"]) -> tuple:
    """(base_url, model_id) from the adapter, or (None, None) if either is missing.

    Both-or-neither, mirroring `TurnModelPreloader.resolve_local_target`. The two
    values must come from the SAME source: a cloud adapter routed through LiteLLM (or the
    eval harness's SpyAdapter) can have a truthy `model_name` with no `base_url` at all, and
    pairing that model id with a config-fallback base_url produces a real request to load a
    model that doesn't exist on a server that has nothing to do with it — which is exactly
    what happened when this was two independent fallback chains (`main_model_id` reporting
    the adapter's id while `main_base_url` fell through to LM Studio's default).
    """
    adapter = getattr(turn_coordinator, "default_adapter", None) if turn_coordinator else None
    base_url = getattr(adapter, "base_url", None) if adapter else None
    model_id = getattr(adapter, "model_name", None) if adapter else None
    if base_url and model_id:
        return base_url, model_id
    return None, None


def main_model_id(turn_coordinator: Optional["TurnCoordinator"] = None) -> Optional[str]:
    _, model_id = _main_adapter_target(turn_coordinator)
    if model_id:
        return model_id
    from src.shared.config_manager import ConfigManager
    cfg = ConfigManager.get_local_model_config()
    return cfg.get("name") if cfg else None


def main_base_url(turn_coordinator: Optional["TurnCoordinator"] = None) -> str:
    base_url, _ = _main_adapter_target(turn_coordinator)
    if base_url:
        return base_url
    from src.shared.config_manager import ConfigManager
    cfg = ConfigManager.get_local_model_config()
    if cfg and cfg.get("base_url"):
        return cfg["base_url"]
    return ConfigManager.get_local_provider_config("lmstudio").get(
        "base_url", "http://localhost:1234/v1"
    )


def aux_models_for_ui_mode(ui_mode: str, turn_coordinator: Optional["TurnCoordinator"] = None) -> List[str]:
    """Auxiliary model IDs to keep hot *in addition to main* for a UI dropdown mode."""
    mode = (ui_mode or "auto").strip().lower()
    if mode == "chat":
        return []
    if mode in ("agent", "plan"):
        sid = selector_model_id(turn_coordinator)
        return [sid] if sid else []
    if mode == "auto":
        rid = router_model_id()
        return [rid] if rid else []
    return []


def aux_models_for_canonical(canonical: str, turn_coordinator: Optional["TurnCoordinator"] = None) -> List[str]:
    """Aux models needed after Auto router resolves to a canonical lane."""
    lane = normalize_turn_mode(canonical)
    if lane == "chat":
        return []
    if lane in ("agent", "plan"):
        sid = selector_model_id(turn_coordinator)
        return [sid] if sid else []
    return []


def default_ui_turn_mode() -> str:
    from src.shared.config_manager import ConfigManager
    lifecycle = (ConfigManager.get("local_models") or {}).get("lifecycle") or {}
    prefs = ConfigManager.get("ui") or {}
    return (
        prefs.get("default_turn_mode")
        or lifecycle.get("default_ui_turn_mode")
        or "auto"
    )


def resolve_connect_preload_ui_mode(
    client_type: Optional[str] = None,
    requested: Optional[str] = None,
) -> str:
    """UI mode for connect-time ``schedule_warm`` (aux model residency).

    Eval harness WS clients must not inherit the GUI's ``auto`` default — that
    JIT-loads intent-router gemma-1b on every suite connect even though eval
    turns skip classify (``platform=eval`` → ``system_turn_explicit``). Eval
    defaults to ``agent`` (9B selector only); ``plan``/``planner``/``planning``
    when explicitly requested. ``auto`` is rejected for eval and coerced to
    ``agent``.
    """
    allowed = {"auto", "agent", "plan", "chat"}
    is_eval = (client_type or "").strip().lower() == "eval"
    mode = (requested or "").strip().lower()
    if mode in ("planning", "planner"):
        mode = "plan"
    if mode in allowed:
        if is_eval and mode == "auto":
            return "agent"
        return mode
    if is_eval:
        return "agent"
    return default_ui_turn_mode()



async def warm_models(
    *,
    turn_coordinator: Optional["TurnCoordinator"] = None,
    ui_mode: Optional[str] = None,
    canonical_lane: Optional[str] = None,
) -> None:
    """
    Load auxiliary models first, then main — the order a turn actually needs them in.

    Pass ``ui_mode`` for connect / manual dropdown changes (auto/agent/plan/chat).
    Pass ``canonical_lane`` after Auto classification to add selector when agent/plan.

    Aux (the tool selector) goes FIRST (refs #592). A turn needs the selector before it
    ever touches the main model; loading main first meant a cold GUI connect always paid
    for a main-model load that got evicted moments later when the selector claimed the
    (single-slot) VRAM budget for the actual turn — visible as a ~17s load thrown away on
    every first turn. Reversing the order means whichever model a turn needs FIRST is
    also the one connect-warm finishes FIRST.

    Both loads skip budget enforcement (refs #592): the whole point of warming is to have
    BOTH resident and ready before the next message arrives, and with budget enforcement
    on, loading the second would just evict the first — exactly the churn observed when
    this used the default-enforced path (aux loads, then main's load evicts aux again,
    ending with only one model up). The real turn-path calls (tool selector, main-model
    preload) keep budget enforcement on and do the actual swap once inference starts.
    """
    if not _lifecycle_enabled():
        return

    base_url = main_base_url(turn_coordinator)
    main_id = main_model_id(turn_coordinator)
    if not main_id:
        return

    aux: List[str] = []
    if canonical_lane is not None:
        aux = aux_models_for_canonical(canonical_lane, turn_coordinator)
    elif ui_mode is not None:
        aux = aux_models_for_ui_mode(ui_mode, turn_coordinator)

    for model_id in aux:
        if model_id and not _same_model(model_id, main_id):
            await ensure_model_loaded(
                base_url, model_id, enforce_budget=False, owner=OWNER_TOOL_SELECTOR
            )
    await ensure_resident(base_url, main_id, owner=OWNER_MAIN_MODEL, enforce_budget=False)


def _lifecycle_enabled() -> bool:
    from src.shared.config_manager import ConfigManager
    if not ConfigManager.is_local_model_active():
        return False
    lifecycle = (ConfigManager.get("local_models") or {}).get("lifecycle") or {}
    return bool(lifecycle.get("preload_on_connect", False))


def schedule_warm(
    *,
    turn_coordinator: Optional["TurnCoordinator"] = None,
    ui_mode: Optional[str] = None,
    canonical_lane: Optional[str] = None,
) -> None:
    """Fire-and-forget background warm (GUI connect / mode change)."""

    def _bg():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                warm_models(
                    turn_coordinator=turn_coordinator,
                    ui_mode=ui_mode,
                    canonical_lane=canonical_lane,
                )
            )
        except Exception as e:
            logger.debug(f"[ MODEL_LIFECYCLE ] Background warm failed (non-fatal): {e}")
        finally:
            loop.close()

    import threading as _threading
    _threading.Thread(target=_bg, daemon=True, name="model-lifecycle-warm").start()
