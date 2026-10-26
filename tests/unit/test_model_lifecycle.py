import asyncio
import time
from unittest.mock import patch

import pytest

from src.core.intelligence import model_lifecycle as ml
from src.core.intelligence.model_lifecycle import (aux_models_for_canonical,
                                                    aux_models_for_ui_mode, main_base_url,
                                                    main_model_id)


class _FakeTC:
    """Bare stand-in for TurnCoordinator — only `default_adapter` is read."""
    def __init__(self, adapter=None):
        self.default_adapter = adapter


class _StubAdapter:
    def __init__(self, model_name=None, base_url=None):
        self.model_name = model_name
        self.base_url = base_url


def test_main_model_id_and_base_url_never_mix_adapter_and_config_fallback():
    """Regression for the bug caught live by the eval dry-run harness: SpyAdapter has a
    `model_name` ("eval-spy-adapter") but no `base_url` attribute at all. Before the fix,
    main_model_id() returned the adapter's fake id (its own condition was satisfied) while
    main_base_url() fell through to the REAL LM Studio default — so warm_models() asked the
    real local server to load a model that doesn't exist. Both must come from the adapter
    together or from config together, never one from each."""
    tc = _FakeTC(_StubAdapter(model_name="eval-spy-adapter", base_url=None))
    with patch("src.shared.config_manager.ConfigManager") as _cfg:
        _cfg.get_local_model_config.return_value = {"name": "google/gemma-4-26b-a4b-qat",
                                                     "base_url": "http://localhost:1234/v1"}
        model_id = main_model_id(tc)
        base_url = main_base_url(tc)
    assert model_id != "eval-spy-adapter", "must not use the adapter's id without its base_url"
    assert model_id == "google/gemma-4-26b-a4b-qat"
    assert base_url == "http://localhost:1234/v1"


def test_main_model_id_and_base_url_use_the_adapter_together_when_both_present():
    tc = _FakeTC(_StubAdapter(model_name="qwen/qwen3-14b", base_url="http://localhost:1234/v1"))
    assert main_model_id(tc) == "qwen/qwen3-14b"
    assert main_base_url(tc) == "http://localhost:1234/v1"


def test_main_model_id_falls_back_cleanly_with_no_adapter_at_all():
    with patch("src.shared.config_manager.ConfigManager") as _cfg:
        _cfg.get_local_model_config.return_value = {"name": "google/gemma-4-26b-a4b-qat",
                                                     "base_url": "http://localhost:1234/v1"}
        assert main_model_id(_FakeTC(None)) == "google/gemma-4-26b-a4b-qat"
        assert main_base_url(_FakeTC(None)) == "http://localhost:1234/v1"


@patch("src.core.intelligence.model_lifecycle.selector_model_id", return_value="qwen/qwen3.5-9b")
@patch("src.core.intelligence.model_lifecycle.router_model_id", return_value="google/gemma-3-1b")
def test_aux_models_for_ui_mode(_router, _selector):
    assert aux_models_for_ui_mode("chat") == []
    assert aux_models_for_ui_mode("agent") == ["qwen/qwen3.5-9b"]
    assert aux_models_for_ui_mode("plan") == ["qwen/qwen3.5-9b"]
    assert aux_models_for_ui_mode("auto") == ["google/gemma-3-1b"]


@patch("src.core.intelligence.model_lifecycle.selector_model_id", return_value="qwen/qwen3.5-9b")
def test_aux_models_after_auto_classify(_selector):
    assert aux_models_for_canonical("chat") == []
    assert aux_models_for_canonical("agent") == ["qwen/qwen3.5-9b"]
    assert aux_models_for_canonical("plan") == ["qwen/qwen3.5-9b"]
    # Legacy router label still maps to chat lane (no selector)
    assert aux_models_for_canonical("conversation") == []


# ---------------------------------------------------------------------------
# Coordinator: locking, refcounted release, VRAM budget, priority queue
#
# Mocks LM Studio's HTTP layer (the get_loaded_model_ids/load_model/unload_model names the
# coordinator imports from model_swap.py) so these run with no server, no LLM, no VRAM cost.
# Each test uses its own fake base_url to avoid state bleed between tests (the coordinator's
# per-base_url state is module-level and persists for the process lifetime).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    """Default config: no VRAM budget, short queue/window timings for fast tests."""
    config = {
        "local_models.lifecycle.vram_budget_gb": 0,
        "local_models.lifecycle.model_size_estimates_gb": {},
        "local_models.lifecycle.background_queue_timeout_s": 1.0,
        "local_models.lifecycle.interactive_active_window_s": 0.2,
    }

    def _get(key, default=None):
        return config.get(key, default)

    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(_get))
    return config


def _mock_lm_studio(monkeypatch, *, loaded=None):
    """Patch the three model_swap primitives the coordinator calls, tracking invocations."""
    state = {"loaded": set(loaded or []), "load_calls": [], "unload_calls": []}

    async def _get_loaded_model_ids(base_url):
        return list(state["loaded"])

    async def _load_model(base_url, model_id, timeout=180.0, context_length=None):
        state["load_calls"].append(model_id)
        await asyncio.sleep(0)  # yield, so concurrent callers actually interleave
        state["loaded"].add(model_id)
        return True

    async def _unload_model(base_url, model_id):
        state["unload_calls"].append(model_id)
        state["loaded"].discard(model_id)
        return True

    monkeypatch.setattr(ml, "get_loaded_model_ids", _get_loaded_model_ids)
    monkeypatch.setattr(ml, "load_model", _load_model)
    monkeypatch.setattr(ml, "unload_model", _unload_model)
    return state


@pytest.mark.asyncio
async def test_picker_unloads_previous_before_loading_selection(monkeypatch):
    url = "http://test-picker.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["old"])
    await ml.ensure_resident(url, "old", ml.OWNER_MAIN_MODEL)
    assert await ml.switch_main_model({"name": "old", "base_url": url},
                                      {"name": "new", "base_url": url})
    assert state["unload_calls"] == ["old"]
    assert state["load_calls"] == ["new"]
    assert state["loaded"] == {"new"}
    assert "old" not in ml.get_diagnostics(url)["wanted"]


@pytest.mark.asyncio
async def test_picker_rejects_switch_during_active_request(monkeypatch):
    url = "http://test-picker-active.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["old"])
    async with ml.active_use(url, "old"):
        assert not await ml.switch_main_model({"name": "old", "base_url": url},
                                              {"name": "new", "base_url": url})
    assert not state["unload_calls"]
    assert not state["load_calls"]


@pytest.mark.asyncio
async def test_picker_reselects_resident_model_without_unload(monkeypatch):
    url = "http://test-picker-same.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["same"])
    selected = {"name": "same", "base_url": url}
    assert await ml.switch_main_model(selected, selected)
    assert not state["unload_calls"]
    assert not state["load_calls"]


@pytest.mark.asyncio
async def test_cloud_selection_unloads_local_supervisor(monkeypatch):
    url = "http://test-picker-cloud.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["old"])
    assert await ml.switch_main_model({"name": "old", "base_url": url}, {})
    assert state["unload_calls"] == ["old"]
    assert not state["load_calls"]


@pytest.mark.asyncio
async def test_picker_restores_previous_if_load_fails(monkeypatch):
    url = "http://test-picker-failure.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["old"])
    original = ml.load_model

    async def load(base_url, model_id, **kwargs):
        if model_id == "new":
            return False
        return await original(base_url, model_id, **kwargs)

    monkeypatch.setattr(ml, "load_model", load)
    assert not await ml.switch_main_model({"name": "old", "base_url": url},
                                          {"name": "new", "base_url": url})
    assert state["loaded"] == {"old"}


@pytest.mark.asyncio
async def test_concurrent_ensure_resident_dedups_to_one_load(monkeypatch):
    base_url = "http://test-dedup.local/v1"
    state = _mock_lm_studio(monkeypatch)

    results = await asyncio.gather(
        ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner="a"),
        ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner="b"),
        ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner="c"),
    )

    assert all(results)
    assert state["load_calls"] == ["qwen/qwen3.5-9b"], "concurrent claims must dedupe to one load"


@pytest.mark.asyncio
async def test_release_only_unloads_after_every_owner_releases(monkeypatch):
    base_url = "http://test-refcount.local/v1"
    state = _mock_lm_studio(monkeypatch)

    await ml.ensure_resident(base_url, "main-model", owner="turn")
    await ml.ensure_resident(base_url, "main-model", owner="scout-caller")
    assert state["load_calls"] == ["main-model"]

    await ml.release(base_url, "main-model", owner="turn")
    assert state["unload_calls"] == [], "another owner still holds a claim — must not unload"

    await ml.release(base_url, "main-model", owner="scout-caller")
    assert state["unload_calls"] == ["main-model"], "last owner released — should unload now"


@pytest.mark.asyncio
async def test_ensure_resident_is_idempotent_per_owner(monkeypatch):
    base_url = "http://test-idempotent.local/v1"
    state = _mock_lm_studio(monkeypatch)

    await ml.ensure_resident(base_url, "main-model", owner="turn")
    await ml.ensure_resident(base_url, "main-model", owner="turn")
    await ml.ensure_resident(base_url, "main-model", owner="turn")

    # Registering the same owner repeatedly must not grow the refcount unboundedly —
    # one release from "turn" should be enough to free it.
    await ml.release(base_url, "main-model", owner="turn")
    assert state["unload_calls"] == ["main-model"]


@pytest.mark.asyncio
async def test_vram_budget_evicts_unwanted_before_wanted(monkeypatch, _reset_config):
    base_url = "http://test-budget.local/v1"
    _reset_config["local_models.lifecycle.vram_budget_gb"] = 20
    _reset_config["local_models.lifecycle.model_size_estimates_gb"] = {
        "big-unwanted": 16,
        "big-wanted": 16,
        "incoming": 16,
    }
    state = _mock_lm_studio(monkeypatch, loaded=["big-unwanted", "big-wanted"])

    # "big-wanted" is claimed by another owner; "big-unwanted" has no claim at all.
    await ml.ensure_resident(base_url, "big-wanted", owner="someone-else")
    state["load_calls"].clear()  # it was already "loaded" per the mock, so this is a no-op load

    ok = await ml.ensure_resident(base_url, "incoming", owner="new-owner")

    assert ok
    assert "big-unwanted" in state["unload_calls"], "unwanted model must be evicted first"
    assert "big-wanted" not in state["unload_calls"], "wanted model must survive the eviction"


@pytest.mark.asyncio
async def test_active_model_survives_last_resort_eviction(monkeypatch, _reset_config):
    """Even when the last-resort "evict a wanted model" path would otherwise fire (budget
    can't be satisfied by evicting unwanted models alone), a model with an in-flight
    request (`mark_active`) must never be evicted — that's what produces LM Studio's
    "Model is unloaded" 400 mid-request."""
    base_url = "http://test-active.local/v1"
    _reset_config["local_models.lifecycle.vram_budget_gb"] = 16
    _reset_config["local_models.lifecycle.model_size_estimates_gb"] = {
        "busy-model": 16,
        "incoming": 6,
    }
    state = _mock_lm_studio(monkeypatch, loaded=["busy-model"])

    await ml.ensure_resident(base_url, "busy-model", owner="main")
    state["load_calls"].clear()
    await ml.mark_active(base_url, "busy-model")

    ok = await ml.ensure_resident(base_url, "incoming", owner="selector")

    assert ok
    assert "busy-model" not in state["unload_calls"], "active model must never be evicted"

    await ml.mark_inactive(base_url, "busy-model")


@pytest.mark.asyncio
async def test_release_defers_unload_while_active(monkeypatch):
    """release() dropping the last owner must not unload a model that's mid-request —
    the unload should wait until mark_inactive."""
    base_url = "http://test-release-active.local/v1"
    state = _mock_lm_studio(monkeypatch)

    await ml.ensure_resident(base_url, "busy-model", owner="turn")
    await ml.mark_active(base_url, "busy-model")

    await ml.release(base_url, "busy-model", owner="turn")
    assert state["unload_calls"] == [], "must not unload a model with an in-flight request"

    await ml.mark_inactive(base_url, "busy-model")


@pytest.mark.asyncio
async def test_background_priority_waits_then_proceeds_after_timeout(monkeypatch, _reset_config):
    base_url = "http://test-queue.local/v1"
    _reset_config["local_models.lifecycle.background_queue_timeout_s"] = 0.3
    _reset_config["local_models.lifecycle.interactive_active_window_s"] = 5.0  # stays "active"
    state = _mock_lm_studio(monkeypatch)

    # Mark an interactive turn as active.
    await ml.ensure_resident(base_url, "interactive-model", owner="turn")

    t0 = time.monotonic()
    ok = await ml.ensure_resident(base_url, "background-model", owner="heartbeat", priority="background")
    elapsed = time.monotonic() - t0

    assert ok, "background call must proceed (with a warning) once its queue timeout expires"
    assert elapsed >= 0.25, "background call should have waited roughly the queue timeout, not run immediately"


@pytest.mark.asyncio
async def test_background_priority_wakes_at_window_not_full_queue_timeout(monkeypatch, _reset_config):
    """Regression for #510: a background load must notice the interactive window has decayed
    and proceed after ~window seconds, not sleep for the entire queue_timeout when nothing else
    ever notify()s it (e.g. an isolated eval turn behind a connect-preload's stale touch)."""
    base_url = "http://test-queue-decay.local/v1"
    _reset_config["local_models.lifecycle.background_queue_timeout_s"] = 5.0
    _reset_config["local_models.lifecycle.interactive_active_window_s"] = 0.3
    state = _mock_lm_studio(monkeypatch)

    # One-off interactive touch (e.g. connect-preload) — nothing else ever calls
    # ensure_resident/release again, so state.condition is never notify_all()'d.
    await ml.ensure_resident(base_url, "interactive-model", owner="turn")

    t0 = time.monotonic()
    ok = await ml.ensure_resident(base_url, "background-model", owner="heartbeat", priority="background")
    elapsed = time.monotonic() - t0

    assert ok
    assert elapsed < 1.0, (
        f"background call took {elapsed:.2f}s — should wake once the {0.3}s interactive window "
        "decays, not sleep out the 5s queue_timeout"
    )


@pytest.mark.asyncio
async def test_background_priority_does_not_wait_when_no_interactive_turn_active(monkeypatch):
    base_url = "http://test-queue-idle.local/v1"
    _mock_lm_studio(monkeypatch)

    t0 = time.monotonic()
    ok = await ml.ensure_resident(base_url, "background-model", owner="heartbeat", priority="background")
    elapsed = time.monotonic() - t0

    assert ok
    assert elapsed < 0.5, "no interactive turn is active — background call must not wait"


def test_ensure_resident_sync_bridges_to_async(monkeypatch):
    base_url = "http://test-sync.local/v1"
    _mock_lm_studio(monkeypatch)

    ok = ml.ensure_resident_sync(base_url, "some-model", owner="sync-caller", timeout=5.0)
    assert ok


def test_model_router_recovers_instead_of_hard_failing(monkeypatch):
    """model_router._ensure_local_model must attempt a load via the coordinator before
    raising ModelError — a model evicted by another lane a moment ago should self-heal,
    not abort the turn (the exact 'randomly fails' symptom this migration fixes)."""
    import json
    import urllib.request as _req
    from src.core.intelligence.model_router import ModelRouter
    from src.shared.exceptions import ModelError

    router = ModelRouter.__new__(ModelRouter)  # bypass __init__ (no warmup side effects)
    router.models = {}
    router.config = {}
    router.model_configs = {}

    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(
        ConfigManager,
        "get_local_model_config",
        staticmethod(lambda category=None: {
            "name": "some/model",
            "provider": "lmstudio",
            "base_url": "http://test-router.local/v1",
        }),
    )
    monkeypatch.setattr(ConfigManager, "get_persona_instruction", staticmethod(lambda: ""))

    class _FakeResp:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    # GET (validation) reports the model as NOT loaded.
    monkeypatch.setattr(_req, "urlopen", lambda *a, **k: _FakeResp({"data": []}))

    # Recovery path: coordinator successfully loads it.
    monkeypatch.setattr(
        "src.core.intelligence.model_lifecycle.ensure_resident_sync",
        lambda *a, **k: True,
    )

    model = router._ensure_local_model("agent")
    assert model is not None

    # Now simulate the coordinator failing to load it too — must raise, not silently pass.
    router.models = {}
    monkeypatch.setattr(
        "src.core.intelligence.model_lifecycle.ensure_resident_sync",
        lambda *a, **k: False,
    )
    with pytest.raises(ModelError):
        router._ensure_local_model("agent")


# ---------------------------------------------------------------------------
# Regression: current_priority() must survive mode resolution
#
# turn_category_var is overwritten mid-turn by TurnModeResolver (the raw "autonomous"
# category gets replaced with whatever chat/agent/plan lane the intent router resolves
# to) — current_priority() must be backed by turn_priority_var, which is set once from
# the RAW incoming category and never touched again, or the whole background-priority
# admission queue silently never engages for real heartbeat/cron turns.
# ---------------------------------------------------------------------------


def test_current_priority_survives_turn_category_var_being_overwritten():
    from src.core.orchestration.context import turn_category_var, turn_priority_var

    tok_priority = turn_priority_var.set("background")
    tok_category = turn_category_var.set("autonomous")
    try:
        assert ml.current_priority() == "background"

        # Simulate what TurnModeResolver does mid-turn: overwrite turn_category_var with
        # the resolved lane, WITHOUT touching turn_priority_var.
        turn_category_var.set("agent")
        assert ml.current_priority() == "background", (
            "current_priority() must not regress when turn_category_var is overwritten "
            "by mode resolution — it must read turn_priority_var instead"
        )
    finally:
        turn_priority_var.reset(tok_priority)
        turn_category_var.reset(tok_category)


def test_current_priority_defaults_to_interactive():
    from src.core.orchestration.context import turn_priority_var

    # No turn in flight — default is "interactive" (a manual/live turn never queues).
    assert turn_priority_var.get() == "interactive"
    assert ml.current_priority() == "interactive"


# ---------------------------------------------------------------------------
# Regression: eval harness priority + exclusivity
#
# platform="eval" is interactive (not background) and eval_exclusivity blocks
# non-eval turns while any eval is in flight — absolute precedence in development.
# ---------------------------------------------------------------------------


def test_is_background_turn_excludes_eval():
    from src.core.orchestration.turn_coordinator import _is_background_turn

    assert _is_background_turn("autonomous", "gui") is True
    assert _is_background_turn("agent", "system") is True
    assert _is_background_turn("agent", "background") is True
    # Eval is exclusive-interactive — must NOT queue behind the interactive window.
    assert _is_background_turn("agent", "eval") is False
    # Real user turns (GUI, browser, PWA) must never accidentally queue behind themselves.
    assert _is_background_turn("agent", "gui") is False
    assert _is_background_turn("chat", "pc") is False
    assert _is_background_turn(None, "gui") is False


@pytest.mark.asyncio
async def test_eval_exclusivity_blocks_non_eval_until_eval_done():
    from src.core.orchestration import eval_exclusivity as ee

    ee.reset_for_tests()
    order: list[str] = []

    async def eval_turn():
        async with ee.eval_turn_guard("eval"):
            order.append("eval_enter")
            await asyncio.sleep(0.05)
            order.append("eval_exit")

    async def desktop_turn():
        await asyncio.sleep(0.01)  # start slightly after eval
        async with ee.eval_turn_guard("desktop"):
            order.append("desktop_enter")

    await asyncio.gather(eval_turn(), desktop_turn())
    assert order == ["eval_enter", "eval_exit", "desktop_enter"]
    assert ee.eval_turns_active() == 0


@pytest.mark.asyncio
async def test_concurrent_eval_turns_do_not_block_each_other():
    from src.core.orchestration import eval_exclusivity as ee

    ee.reset_for_tests()
    concurrent = 0
    peak = 0

    async def eval_turn():
        nonlocal concurrent, peak
        async with ee.eval_turn_guard("eval"):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.03)
            concurrent -= 1

    await asyncio.gather(eval_turn(), eval_turn(), eval_turn())
    assert peak >= 2
    assert ee.eval_turns_active() == 0


def test_dry_run_harness_tags_turns_as_eval_platform(monkeypatch):
    """evals/dry_run.py must pass platform="eval" so exclusivity + _is_eval_turn apply."""
    import asyncio
    from evals.dry_run import DryRunHarness

    captured = {}

    class _FakeTurnCoordinator:
        pass

    class _FakeClient:
        async def send_message(self, text, conversation_id=None, category=None, context=None, **kwargs):
            captured["context"] = context
            return {"success": True, "response": "ok"}

    harness = DryRunHarness.__new__(DryRunHarness)
    harness._client = _FakeClient()
    harness._spy = type("Spy", (), {"captured_messages": [], "captured_tools": []})()
    harness._counter = None
    harness.prompts_offline = False

    from src.core.utils.token_counter import TokenCounter
    harness._counter = TokenCounter

    # Not asyncio.get_event_loop(): once other pytest-asyncio (auto-mode) tests
    # have run and closed the main thread's loop, get_event_loop() raises
    # RuntimeError on Python 3.12 instead of lazily creating one (#216).
    asyncio.run(harness.capture("hello"))

    assert captured["context"] is not None
    assert captured["context"].platform == "eval"


@pytest.mark.asyncio
async def test_active_use_if_resident_refuses_when_not_loaded(monkeypatch, _reset_config):
    """Opportunistic callers must never CAUSE a load (refs #592).

    The wait-narrator may only use a model that is already resident. A plain check-then-use
    is TOCTOU: if the model is evicted in between, the request JIT-loads it, and a competing
    JIT load then aborts that load mid-flight — killing the real work waiting on it. So the
    gate reports False rather than letting the caller proceed.
    """
    base_url = "http://test-gate.local/v1"
    _mock_lm_studio(monkeypatch, loaded=["something-else"])

    assert await ml._mark_active_if_resident_impl(base_url, "qwen/qwen3.5-9b") is False
    # Nothing was pinned, so nothing is protected from eviction either.
    assert not ml._state_for(base_url).active


@pytest.mark.asyncio
async def test_active_use_if_resident_pins_when_loaded(monkeypatch, _reset_config):
    """When the model IS resident the gate pins it, so _make_room won't evict mid-request."""
    base_url = "http://test-gate2.local/v1"
    _mock_lm_studio(monkeypatch, loaded=["qwen/qwen3.5-9b"])

    assert await ml._mark_active_if_resident_impl(base_url, "qwen/qwen3.5-9b") is True
    assert ml._state_for(base_url).active.get("qwen/qwen3.5-9b") == 1


@pytest.mark.asyncio
async def test_ensure_resident_enforce_budget_false_skips_eviction(monkeypatch, _reset_config):
    """warm_models's co-residence path must NOT evict the other warmed model (refs #592).

    With budget enforcement on (the default, and what every turn-path caller uses), loading
    a second model when combined size exceeds budget evicts the first. That's correct for
    an actual turn's swap, but it defeats the entire point of "keep both hot at rest" if the
    warm path's own back-to-back loads also evict each other — which is exactly what was
    observed before this flag existed (aux loads, then main's load evicts aux right back).
    """
    base_url = "http://test-coexist.local/v1"
    _reset_config["local_models.lifecycle.vram_budget_gb"] = 16
    _reset_config["local_models.lifecycle.model_size_estimates_gb"] = {"big": 16, "small": 6}
    state = _mock_lm_studio(monkeypatch, loaded=["big"])

    ok = await ml.ensure_resident(base_url, "small", owner="warm", enforce_budget=False)

    assert ok
    assert state["unload_calls"] == [], "co-resident warm load must not evict the other model"
    assert set(state["loaded"]) == {"big", "small"}


@pytest.mark.asyncio
async def test_selector_release_unloads_despite_warm_claim(monkeypatch, _reset_config):
    """The selector's post-use release must actually unload it, not be blocked by warm's claim.

    warm_models tags its "keep the selector hot at rest" claim with OWNER_TOOL_SELECTOR
    specifically so it SHARES the owner set with llm_selector's own claim/release (refs
    #592) — a claim left under a different tag (e.g. the generic "warm", which nothing
    ever releases) would sit in `wanted` forever and permanently block this unload.
    """
    base_url = "http://test-shared-owner.local/v1"
    state = _mock_lm_studio(monkeypatch, loaded=["qwen/qwen3.5-9b"])

    # warm_models's claim (at connect / end of turn)
    await ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner=ml.OWNER_TOOL_SELECTOR, enforce_budget=False)
    # the real turn's own selector claim (same tag)
    await ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner=ml.OWNER_TOOL_SELECTOR)
    # the real turn's own selector release, after it answers
    await ml.release(base_url, "qwen/qwen3.5-9b", owner=ml.OWNER_TOOL_SELECTOR)

    assert state["unload_calls"] == ["qwen/qwen3.5-9b"], "shared owner tag must let release() actually unload"


# ---------------------------------------------------------------------------
# Pinned residency (local_models.lifecycle.pinned_models)
#
# On a single-GPU box the main chat model and the 9B tool selector don't fit in VRAM
# together, so budget enforcement swapped them on EVERY turn — a full ~15.6GB main-model
# reload per message. Pinning both makes that swap disappear.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_models_do_not_evict_each_other(monkeypatch, _reset_config):
    """The whole point of the pin: two pinned models stay co-resident even over budget."""
    base_url = "http://test-pin-coexist.local/v1"
    _reset_config["local_models.lifecycle.vram_budget_gb"] = 16
    _reset_config["local_models.lifecycle.model_size_estimates_gb"] = {
        "gemma-4-26b": 16,
        "qwen3.5-9b": 6,
    }
    _reset_config["local_models.lifecycle.pinned_models"] = ["gemma-4-26b", "qwen3.5-9b"]
    state = _mock_lm_studio(monkeypatch, loaded=["google/gemma-4-26b-a4b-qat"])

    await ml.ensure_resident(base_url, "google/gemma-4-26b-a4b-qat", owner="main")
    state["load_calls"].clear()

    # 16 + 6 = 22GB against a 16GB budget: without the pin this evicts the 26B.
    ok = await ml.ensure_resident(base_url, "qwen/qwen3.5-9b", owner=ml.OWNER_TOOL_SELECTOR)

    assert ok
    assert state["unload_calls"] == [], "pinned models must never evict one another"
    assert "google/gemma-4-26b-a4b-qat" in state["loaded"]
    assert "qwen/qwen3.5-9b" in state["loaded"]


@pytest.mark.asyncio
async def test_non_pinned_model_still_evicts_a_pinned_one(monkeypatch, _reset_config):
    """The pin is not absolute — a big non-pinned model genuinely doesn't fit alongside it,
    and refusing to evict would OOM the box rather than protect anything."""
    base_url = "http://test-pin-yields.local/v1"
    _reset_config["local_models.lifecycle.vram_budget_gb"] = 16
    _reset_config["local_models.lifecycle.model_size_estimates_gb"] = {
        "gemma-4-26b": 16,
        "35b": 18,
    }
    _reset_config["local_models.lifecycle.pinned_models"] = ["gemma-4-26b", "qwen3.5-9b"]
    state = _mock_lm_studio(monkeypatch, loaded=["google/gemma-4-26b-a4b-qat"])

    await ml.ensure_resident(base_url, "google/gemma-4-26b-a4b-qat", owner="main")
    state["load_calls"].clear()

    ok = await ml.ensure_resident(base_url, "qwen/qwen3.6-35b-a3b", owner="coder")

    assert ok
    assert state["unload_calls"] == ["google/gemma-4-26b-a4b-qat"], (
        "a non-pinned incoming model must still be able to evict a pinned one"
    )


def test_is_pinned_substring_matches_quant_and_prefix_variants(_reset_config):
    _reset_config["local_models.lifecycle.pinned_models"] = ["gemma-4-26b", "qwen3.5-9b"]

    assert ml.is_pinned("google/gemma-4-26b-a4b-qat")
    assert ml.is_pinned("openai/google/gemma-4-26b-a4b-qat")
    assert ml.is_pinned("qwen/qwen3.5-9b")
    assert not ml.is_pinned("qwen/qwen3.6-35b-a3b")
    assert not ml.is_pinned("google/gemma-3-1b")


def test_is_pinned_is_false_when_unconfigured(_reset_config):
    """Default-empty: pinning must be opt-in, so no existing setup changes behavior."""
    assert not ml.is_pinned("google/gemma-4-26b-a4b-qat")
