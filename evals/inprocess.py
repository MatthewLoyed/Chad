"""In-process turn runner — drive a REAL Chad turn without a websocket server (#358).

The live eval lane opens a websocket to a running Brain. But `TextModeClient.send_message()`
dispatches through the SAME `TurnCoordinator` the server uses — the server is pure transport. So
we can run a full multi-agent turn (supervisor -> delegate -> tools) directly in this process,
against WHATEVER checkout this code is imported from. Run it with `cwd` = a coder's edited clone
and it measures that clone's prompts/code — which is how the self-improvement loop tests a fix
without booting a throwaway server.

Trace capture reuses the coordinator's OWN mechanism: after `send_message`, the consolidated
event stream (reasoning per agent, tool_start with params, tool_end with results, execution plan)
lands on `turn_coordinator.last_turn_thinking_events`, and counts/tokens/stop_reason on
`last_turn_metrics`. We just read them — no EventBus wiring, and deliberately NO wall-clock timeout
(a real local-model turn can be minutes; a caller that wants a cap can impose its own).

    from evals.inprocess import run_turn_inprocess
    trace = await run_turn_inprocess("what time is it right now?")
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional


def _reasoning_by_agent(events: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, List[str]] = {}
    for e in events:
        if e.get("type") in ("reasoning", "thought") and e.get("text"):
            out.setdefault(e.get("agent_name") or "Main", []).append(e["text"])
    return {a: "".join(chunks) for a, chunks in out.items()}


def _tool_calls(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"name": e.get("name"), "params": e.get("params") or {}}
            for e in events if e.get("type") == "tool_start"]


def _tool_results(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"name": e.get("name"), "success": e.get("success"),
             "result": str(e.get("result"))[:1500]}
            for e in events if e.get("type") == "tool_end"]


def _plan(events: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    for e in reversed(events):
        if e.get("type") == "execution_plan":
            return e.get("plan")
    return None


async def build_inprocess_session() -> tuple[Any, Optional[str]]:
    """Build and warm the real core ONCE. Returns (container, warmup_error).

    Split out of `run_turn_inprocess` so a multi-case lane can pay this cost once
    instead of per turn. Building is expensive and not just in time: warmup spawns
    the full MCP fleet (a node subprocess per server) and the coordinator preloads
    the supervisor model, which LM Studio then evicts to fit the 9B selector. A
    15-case lane that rebuilt per turn spawned ~180 subprocesses and thrashed VRAM
    the whole way through — measured while building the #660 lane, which is why
    this exists.
    """
    # Evals must never spawn background daemons — SystemFactory.warmup_async_infrastructure
    # unconditionally fires a real, unbounded, synchronous Gmail/Drive/Chrome ingestion sync
    # (src/core/intelligence/ingestion/) on every process boot when daemons.ingestion_sync is
    # enabled in preferences.yaml. That sync makes blocking Google API calls with no timeout
    # and no asyncio.to_thread wrapping, so it freezes the ENTIRE event loop — including this
    # eval turn — for as long as the real sync takes (issue #639: looked like a network hang,
    # was actually a live Gmail/Drive sync eval scripts have no business triggering).
    # setdefault, not direct assignment: not present in .env, so this sticks even though
    # ConfigManager.load_config() below reloads .env with override=True.
    from evals.runtime_bootstrap import bootstrap_path
    bootstrap_path()
    os.environ.setdefault("CHAD_DAEMONS_ENABLED", "false")

    from src.shared.config_manager import ConfigManager
    from src.application.events import EventBus
    from src.application.system_factory import SystemFactory

    ConfigManager.load_config()
    # .env explicitly sets these, and load_config() just reloaded it with override=True — so
    # override AFTER, not before. Evals shouldn't burn the account's LangSmith trace quota.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    config = ConfigManager.get_config()
    bus = EventBus()

    container = SystemFactory.build_core_engine(config, event_bus=bus)
    # Warmup wires the tool selector, prompt registry, MCP, and coordinator. It can fail partially
    # in a dev box (e.g. Supabase/MCP offline); that's non-fatal — the turn still runs.
    try:
        await SystemFactory.warmup_async_infrastructure(container, config)
    except Exception as exc:  # noqa: BLE001
        return container, f"{type(exc).__name__}: {exc}"
    return container, None


async def shutdown_inprocess_session(container: Any) -> None:
    """Tear down a session built by :func:`build_inprocess_session`. Call it in a ``finally``.

    Warmup spawns a node subprocess per MCP server and nothing ever disconnected them
    (#675) — so every eval run leaked its whole fleet, and consecutive runs stacked fleets
    until the box had 42 stray node processes and new runs died almost immediately.

    This is the *graceful* half. The crash-proof half is
    ``src/shared/child_reaper``'s kill-on-job-close job object, which the MCP registry
    registers each child with at connect time; that one fires even when this never runs.
    Best-effort and silent on failure: teardown must not turn a finished run into a
    traceback that loses the results.
    """
    registry = getattr(container, "mcp_registry", None)
    if registry is not None:
        try:
            await registry.disconnect_all()
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] mcp disconnect failed (non-fatal): {exc}", flush=True)
    try:
        from src.shared.child_reaper import reap_all
        reap_all("eval teardown")
    except Exception:  # noqa: BLE001
        pass


async def run_turn_with(container: Any, prompt: str, *, category: str = "agent",
                        session_id: Optional[str] = None,
                        warmup_error: Optional[str] = None) -> Dict[str, Any]:
    """Dispatch one turn through an ALREADY-BUILT container and read its trace."""
    from src.shared.context import RequestContext

    conv = session_id or f"inproc_{uuid.uuid4().hex[:8]}"
    ctx = RequestContext(platform="eval", session_id=conv)  # background priority in the coordinator
    t0 = time.monotonic()
    err = None
    try:
        ret = await container.text_mode_client.send_message(
            text=prompt, conversation_id=conv, category=category, context=ctx)
        response = ret.get("response") if isinstance(ret, dict) else str(ret)
    except Exception as exc:  # noqa: BLE001
        response, err = None, f"{type(exc).__name__}: {exc}"
    duration_s = round(time.monotonic() - t0, 1)

    tc = container.turn_coordinator
    events = list(getattr(tc, "last_turn_thinking_events", []) or [])
    metrics = dict(getattr(tc, "last_turn_metrics", {}) or {})

    return {
        "prompt": prompt,
        "response": response,
        "error": err,
        "warmup_error": warmup_error,
        "duration_s": duration_s,
        "reasoning_by_agent": _reasoning_by_agent(events),
        "tool_calls": _tool_calls(events),
        "tool_results": _tool_results(events),
        "plan": _plan(events),
        "metrics": metrics,
        "n_events": len(events),
    }


async def run_turn_inprocess(prompt: str, *, category: str = "agent",
                             session_id: Optional[str] = None,
                             build_timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Run ONE real Chad turn in this process and return a structured trace.

    No wall-clock cap on the turn itself. Builds the real core (real LiteLLM adapter, real
    TurnCoordinator) via SystemFactory — the same path the server boots from — warms it, dispatches
    the prompt, and reads the trace off the coordinator.

    Build-once-then-loop callers should use `build_inprocess_session` +
    `run_turn_with` instead; this wrapper rebuilds everything per call.
    """
    container, warmup_err = await build_inprocess_session()
    try:
        return await run_turn_with(
            container, prompt, category=category, session_id=session_id, warmup_error=warmup_err,
        )
    finally:
        await shutdown_inprocess_session(container)

