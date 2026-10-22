"""Live post-wake voice routing report — inject transcripts, no mic.

Mimics what Limb sends after STT (category=voice) and prints what the Brain
did: lane/stop_reason, escalate, tools, response, duration.

  python -m evals.voice_live_mimic_report
  python -m evals.voice_live_mimic_report --quick-only   # tier-2 9B only (+ reasoning)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock


CASES: List[Dict[str, Any]] = [
    {"id": "open_plain", "prompt": "open youtube"},
    {"id": "open_polite", "prompt": "can you open youtube"},
    {"id": "open_please", "prompt": "open youtube please"},
    {"id": "open_gmail", "prompt": "go to gmail"},
    {"id": "search", "prompt": "search for the weather tomorrow"},
    {"id": "look_up", "prompt": "look up who won the game last night"},
    {"id": "simple_qa", "prompt": "what time is it"},
    {"id": "lights", "prompt": "turn on the living room lights"},
    {"id": "email", "prompt": "check my email for the lease"},
    {"id": "multi", "prompt": "close spotify and open chrome"},
]


def _load_main_env() -> None:
    """Worktree shells often lack secrets; pull from main .env if present."""
    main_env = Path(os.environ.get("CHAD_MAIN_ROOT", "")) / ".env"
    if not main_env.is_file():
        return
    for line in main_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Prefer .env values for Hubspace — secrets.yaml placeholders otherwise win.
        if key and (key not in os.environ or key.startswith("HUBSPACE_")):
            os.environ[key] = val


def _stub_side_effects(container: Any) -> None:
    registry = container.tool_registry
    stubs = {}
    # Only stub side-effecting open tools. Do NOT stub hubspace_control /
    # escalate_to_agent — those get bound into the live voice_node schema and a
    # MagicMock is not JSON-serializable (poisons the LLM call).
    for name in ("open_browser", "open_app"):
        tool = MagicMock()
        tool.name = name
        tool.ainvoke = AsyncMock(return_value={"ok": True, "stubbed": True})
        stubs[name] = tool
    real_get = registry.get_langchain_tool_by_name

    def get_langchain_tool_by_name(name: str):
        if name in stubs:
            return stubs[name]
        return real_get(name)

    registry.get_langchain_tool_by_name = get_langchain_tool_by_name


async def _run_quick_only(container: Any) -> int:
    """Hit tier-2 directly — true 9B-only path with reasoning capture."""
    from src.core.orchestration.nodes.voice_quick_tool import (
        get_last_trace,
        try_quick_tool_call,
    )
    from src.shared.config_manager import ConfigManager

    local_models = ConfigManager.get("local_models", {}) or {}
    model = local_models.get("category_models", {}).get("voice") or "qwen/qwen3.5-9b"
    base_url = local_models.get("providers", {}).get("lmstudio", {}).get(
        "base_url", "http://localhost:1234/v1"
    )
    registry = container.tool_registry

    print(f"\n=== 9B QUICK-ONLY (model={model}) ===\n")
    rows = []
    for case in CASES:
        prompt = case["prompt"]
        t0 = time.monotonic()
        ack = await try_quick_tool_call(
            prompt,
            registry,
            model=model,
            base_url=base_url,
            include_reasoning=True,
        )
        duration_s = round(time.monotonic() - t0, 2)
        trace = get_last_trace()
        hit = ack is not None
        rows.append({
            "id": case["id"],
            "hit": hit,
            "tool": trace.get("tool"),
            "duration_s": duration_s,
            "ack": (ack or "")[:120],
            "error": trace.get("error"),
        })
        print(f"=== {case['id']}: {prompt!r} ===")
        print(f"  hit:       {hit}")
        print(f"  tool:      {trace.get('tool')} {trace.get('args')}")
        print(f"  content:   {(trace.get('content') or '')[:200]!r}")
        reasoning = trace.get("reasoning") or ""
        if reasoning:
            print(f"  reasoning: {reasoning[:500]}")
        else:
            print("  reasoning: (none)")
        synth_r = trace.get("synth_reasoning") or ""
        if synth_r:
            print(f"  synth_think:{synth_r[:400]}")
        print(f"  error:     {trace.get('error')}")
        print(f"  duration:  {duration_s}s (decide+tool+synth ms={trace.get('ms')})")
        print(f"  ack:       {(ack or '(fell through -> would be tier3)')[:220]}")
        print()

    print("======== SUMMARY ========")
    print(f"{'id':<14} {'hit':<6} {'tool':<18} {'s':>5}  error")
    for r in rows:
        print(
            f"{r['id']:<14} {str(r['hit']):<6} {str(r['tool'] or '-'):<18} "
            f"{r['duration_s']:>5}  {r['error'] or ''}"
        )
    hits = sum(1 for r in rows if r["hit"])
    print(f"\n9B handled {hits}/{len(rows)} without tier3.")
    return 0


async def _run_full_pipeline(container: Any) -> int:
    from src.core.orchestration.nodes.voice_quick_tool import get_last_trace
    from src.core.orchestration.turn import pipeline as turn_pipeline
    from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor
    from src.shared.context import RequestContext

    escalate_hits: List[str] = []
    orig = turn_pipeline.TurnPipeline._maybe_escalate_voice

    async def _tracking_escalate(self, final_state):
        escalate, reason = TurnResponsePostProcessor.check_voice_escalate(
            final_state, self.category
        )
        if escalate:
            escalate_hits.append(str(reason or ""))
        return await orig(self, final_state)

    turn_pipeline.TurnPipeline._maybe_escalate_voice = _tracking_escalate

    rows = []
    for case in CASES:
        escalate_hits.clear()
        prompt = case["prompt"]
        ctx = RequestContext(platform="voice", session_id=f"mimic-{case['id']}")
        t0 = time.monotonic()
        err = None
        response = None
        try:
            ret = await container.text_mode_client.send_message(
                text=prompt,
                conversation_id=ctx.session_id,
                category="voice",
                context=ctx,
            )
            response = ret.get("response") if isinstance(ret, dict) else str(ret)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        duration_s = round(time.monotonic() - t0, 2)

        tc = container.turn_coordinator
        metrics = dict(getattr(tc, "last_turn_metrics", {}) or {})
        events = list(getattr(tc, "last_turn_thinking_events", []) or [])
        tools = [e.get("name") for e in events if e.get("type") == "tool_start"]
        stop = metrics.get("stop_reason")
        escalated = bool(escalate_hits)
        qtrace = get_last_trace()

        if stop == "fast_lane":
            lane = "tier1_fast_lane"
        elif stop == "quick_tool_lane":
            lane = "tier2_quick_tool"
        elif escalated:
            lane = "tier3_escalate->gemma"
        else:
            lane = "tier3_voice_9b"

        rows.append(
            {
                "id": case["id"],
                "prompt": prompt,
                "lane": lane,
                "stop": stop,
                "escalate": escalated,
                "tools": tools,
                "duration_s": duration_s,
                "response": (response or err or "")[:180],
                "ok": err is None and bool(response),
            }
        )

        print(f"\n=== {case['id']}: {prompt!r} ===")
        print(f"  lane:      {lane}")
        print(f"  stop:      {stop!r}")
        print(f"  escalate:  {escalated} {escalate_hits!r}")
        print(f"  tools:     {tools}")
        if qtrace.get("tool") or qtrace.get("error"):
            print(f"  t2_tool:   {qtrace.get('tool')} {qtrace.get('args')} err={qtrace.get('error')}")
            if qtrace.get("reasoning"):
                print(f"  t2_reason: {str(qtrace.get('reasoning'))[:300]}")
        print(f"  duration:  {duration_s}s")
        print(f"  response:  {(response or err or '')[:200]}")

    print("\n======== SUMMARY ========")
    print(f"{'id':<14} {'lane':<24} {'stop':<16} {'esc':<5} {'s':>5}  tools")
    for r in rows:
        print(
            f"{r['id']:<14} {r['lane']:<24} {str(r['stop']):<16} "
            f"{str(r['escalate']):<5} {r['duration_s']:>5}  {r['tools']}"
        )

    bad_opens = [
        r for r in rows
        if r["id"].startswith("open") and (
            r["escalate"]
            or r["lane"] == "tier3_escalate->gemma"
            or r["stop"] not in ("fast_lane", "quick_tool_lane")
        )
    ]
    if bad_opens:
        print("\n[!] Opens that left the quick path:")
        for r in bad_opens:
            print(f"  - {r['id']}: lane={r['lane']} stop={r['stop']} esc={r['escalate']}")
    else:
        print("\n[OK] All open-* cases stayed on tier1/tier2 with no Gemma escalate.")

    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick-only",
        action="store_true",
        help="Call tier-2 9B directly (no tier1/tier3). Captures reasoning.",
    )
    args = parser.parse_args()

    os.environ.setdefault("CHAD_DAEMONS_ENABLED", "false")
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    _load_main_env()

    # Env must be set before ConfigManager first loads (Hubspace injects from env).
    from src.shared.config_manager import ConfigManager
    ConfigManager._loaded = False

    from evals.inprocess import build_inprocess_session

    print("Building in-process session (category=voice)...")
    container, warmup_err = await build_inprocess_session()
    if warmup_err:
        print(f"  warmup note: {warmup_err}")
    _stub_side_effects(container)

    if args.quick_only:
        return await _run_quick_only(container)
    return await _run_full_pipeline(container)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
