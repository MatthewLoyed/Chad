"""Quick in-process smoke for Telegram contact front desk (no Telegram needed).

Runs two real turns through category=contact:
  1) greeting — expect NO escalate_to_agent
  2) task — expect escalate_to_agent then agent response

Note: after escalate, agent-turn events replace contact tool_start events, so we
hook the escalate path instead of reading last_turn_thinking_events alone.

Usage (from worktree, LM Studio with 9B + Gemma):
  python -m evals.contact_front_desk_probe
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List


CASES: List[Dict[str, Any]] = [
    {
        "id": "greeting",
        "prompt": "heyyy",
        "expect_escalate": False,
    },
    {
        "id": "task_email",
        "prompt": "check my email for the lease",
        "expect_escalate": True,
    },
]


async def main() -> int:
    os.environ.setdefault("CHAD_DAEMONS_ENABLED", "false")
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

    from evals.inprocess import build_inprocess_session
    from src.core.orchestration.turn import pipeline as turn_pipeline
    from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
    from src.shared.context import RequestContext

    print("Building in-process session (category=contact / 9B desk)...")
    container, warmup_err = await build_inprocess_session()
    if warmup_err:
        print(f"  (warmup non-fatal: {warmup_err})")

    session_key = TelegramChatRepository().create_or_update_chat(
        chat_id=8626369319,
        user_id=8626369319,
        username="matthew",
        first_name="Matthew",
        last_name=None,
    )
    print(f"  seeded telegram peer Matthew @ {session_key}")

    # Capture escalate handoffs even after agent events overwrite contact tool_starts.
    escalate_hits: List[str] = []
    orig = turn_pipeline.TurnPipeline._maybe_escalate_voice

    async def _tracking_escalate(self, final_state):
        from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor

        escalate, reason = TurnResponsePostProcessor.check_voice_escalate(
            final_state, self.category
        )
        if escalate:
            escalate_hits.append(str(reason or ""))
        return await orig(self, final_state)

    turn_pipeline.TurnPipeline._maybe_escalate_voice = _tracking_escalate

    results = []
    for case in CASES:
        print(f"\n=== {case['id']}: {case['prompt']!r} ===")
        escalate_hits.clear()
        ctx = RequestContext(platform="telegram", session_id=session_key)
        t0 = time.monotonic()
        err = None
        try:
            ret = await container.text_mode_client.send_message(
                text=case["prompt"],
                conversation_id=session_key,
                category="contact",
                context=ctx,
            )
            response = ret.get("response") if isinstance(ret, dict) else str(ret)
        except Exception as exc:  # noqa: BLE001
            response, err = None, f"{type(exc).__name__}: {exc}"
        duration_s = round(time.monotonic() - t0, 1)

        tc = container.turn_coordinator
        events = list(getattr(tc, "last_turn_thinking_events", []) or [])
        tool_names = [e.get("name") for e in events if e.get("type") == "tool_start"]
        escalated = bool(escalate_hits)
        expect = case["expect_escalate"]
        ok = (escalated == expect) and not err and bool(response)

        print(f"  duration: {duration_s}s")
        print(f"  tools:    {tool_names}")
        print(f"  escalate: {escalated} reason={escalate_hits!r} (expect {expect})")
        print(f"  response: {(response or err or '')[:240]}")
        print(f"  verdict:  {'PASS' if ok else 'FAIL'}")
        results.append(
            {
                "id": case["id"],
                "ok": ok,
                "escalated": escalated,
                "duration_s": duration_s,
                "response": response,
            }
        )

    print("\n=== SUMMARY ===")
    for r in results:
        print(
            f"  {r['id']}: {'PASS' if r['ok'] else 'FAIL'} "
            f"({r['duration_s']}s, escalate={r['escalated']})"
        )
    failed = sum(1 for r in results if not r["ok"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
