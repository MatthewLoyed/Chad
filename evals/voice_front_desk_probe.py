"""Post-wake voice front-desk / fast-command probe (no mic, no wake word).

Injects transcripts as ``category="voice"`` through the real in-process turn
pipeline — the same Brain path Limb hits after STT. Asserts simple opens stay
on tier-1/2 (``fast_lane`` / ``quick_tool_lane``) and never call
``escalate_to_agent`` / Gemma; email-style work is still allowed to escalate.

Side-effecting tools (``open_browser``, ``open_app``) are stubbed so the probe
does not actually launch apps/browsers. ``web_search`` stays real when the case
needs tier-2 synthesis (needs LM Studio + network).

Usage (from worktree, LM Studio with voice 9B; Gemma only needed for escalate):
  python -m evals.voice_front_desk_probe
  python -m evals.voice_front_desk_probe --only vfd_open_youtube,vfd_open_polite
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

DATASET_PATH = Path(__file__).parent / "datasets" / "voice_front_desk.jsonl"


def load_cases(only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    cases = []
    with DATASET_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if only:
        wanted = set(only)
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def _stub_side_effect_tools(container: Any) -> None:
    """Replace open_browser / open_app so the probe does not launch anything."""
    registry = container.tool_registry

    def _stub(name: str) -> MagicMock:
        tool = MagicMock()
        tool.name = name
        tool.ainvoke = AsyncMock(return_value={"ok": True, "stubbed": True})
        return tool

    stubs = {
        "open_browser": _stub("open_browser"),
        "open_app": _stub("open_app"),
    }
    real_get = registry.get_langchain_tool_by_name

    def get_langchain_tool_by_name(name: str):
        if name in stubs:
            return stubs[name]
        return real_get(name)

    registry.get_langchain_tool_by_name = get_langchain_tool_by_name


async def main(only: Optional[List[str]] = None) -> int:
    os.environ.setdefault("CHAD_DAEMONS_ENABLED", "false")
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

    from evals.inprocess import build_inprocess_session
    from src.core.orchestration.turn import pipeline as turn_pipeline
    from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor
    from src.shared.context import RequestContext

    cases = load_cases(only)
    print(f"Loaded {len(cases)} voice front-desk cases from {DATASET_PATH.name}")
    print("Building in-process session (category=voice)...")
    container, warmup_err = await build_inprocess_session()
    if warmup_err:
        print(f"  (warmup non-fatal: {warmup_err})")

    _stub_side_effect_tools(container)

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

    results = []
    for case in cases:
        print(f"\n=== {case['id']}: {case['query']!r} ===")
        escalate_hits.clear()
        ctx = RequestContext(platform="voice", session_id=f"voice-probe-{case['id']}")
        t0 = time.monotonic()
        err = None
        response = None
        try:
            ret = await container.text_mode_client.send_message(
                text=case["query"],
                conversation_id=ctx.session_id,
                category="voice",
                context=ctx,
            )
            response = ret.get("response") if isinstance(ret, dict) else str(ret)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        duration_s = round(time.monotonic() - t0, 1)

        tc = container.turn_coordinator
        metrics = dict(getattr(tc, "last_turn_metrics", {}) or {})
        stop_reason = metrics.get("stop_reason")
        escalated = bool(escalate_hits)
        expect_escalate = bool(case.get("expect_escalate"))
        expect_stops = case.get("expect_stop_reasons")
        substr = case.get("expect_response_substr")

        ok = not err and bool(response)
        if ok and escalated != expect_escalate:
            ok = False
        if ok and expect_stops is not None and stop_reason not in expect_stops:
            ok = False
        if ok and substr and substr.lower() not in (response or "").lower():
            ok = False

        print(f"  duration:    {duration_s}s")
        print(f"  stop_reason: {stop_reason!r}")
        print(f"  escalate:    {escalated} reason={escalate_hits!r} (expect {expect_escalate})")
        print(f"  response:    {(response or err or '')[:240]}")
        print(f"  verdict:     {'PASS' if ok else 'FAIL'}")
        results.append(
            {
                "id": case["id"],
                "ok": ok,
                "escalated": escalated,
                "stop_reason": stop_reason,
                "duration_s": duration_s,
                "response": response,
                "error": err,
            }
        )

    print("\n=== SUMMARY ===")
    for r in results:
        print(
            f"  {r['id']}: {'PASS' if r['ok'] else 'FAIL'} "
            f"({r['duration_s']}s, stop={r['stop_reason']!r}, escalate={r['escalated']})"
        )
    failed = sum(1 for r in results if not r["ok"])
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated case ids to run (default: all).",
    )
    args = parser.parse_args()
    only_ids = [x.strip() for x in args.only.split(",") if x.strip()] or None
    raise SystemExit(asyncio.run(main(only_ids)))
