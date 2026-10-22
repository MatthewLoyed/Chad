r"""session_isolation_probe.py — LIVE regression guard for the #269 session-isolation incident.

Issue #342 (refs #269 / #305 / #316). `docs/architecture/SESSION_ISOLATION.md` documents session
isolation as a HARD contract with a real production incident: a clarifying-question / approval card
raised in chat A leaked onto chat B because the approval message shipped WITHOUT a `conversation_id`
(the unscoped-global-store half was fixed client-side; the keystone is the Brain stamping the id).
There was zero eval coverage for it — this probe closes that gap with a genuine two-conversation
live check, not a smoke test.

What it does (over ONE websocket — the faithful production model, where the web Limb multiplexes
every chat over a single socket; see `evals/live.py::probe_concurrent`):

  1. Drive conversation A with a PLANNING-lane prompt, which deterministically pauses for approval
     (stop_reason=awaiting_approval) before executing anything. We do NOT approve it, so A is left
     with live, in-flight paused turn state on the server. (Plan approval is one of the two pause
     kinds the #269 contract covers; the other, the ask_user_question clarifying-question CARD, is
     model-dependent to elicit live but is exercised deterministically by `--self-test` below — so
     the exact incident artifact is still guarded, just offline where it can be made reliable.)
  2. WHILE A is paused, drive conversation B (a different conversation_id) with an unrelated plain
     turn that completes normally.
  3. Assert (see `_detect_isolation_leaks`): B never receives any event tagged for A, A's paused
     ai_response (and any approval card it raised) are concretely tagged with A's conversation_id
     (an untagged pause payload is the #269 bug), and neither conversation observes the other's
     turn state.

This is a real regression test: run against a server whose approval/pause message dropped the
`conversation_id` stamp (the pre-#269 code) and step 3 fails loudly. `--self-test` proves the
detector isn't a no-op offline in <1s (feeds it synthetic leaky traces — including the EXACT
untagged-card bug — and asserts it flags them), so CI can gate the guard's own correctness without
a live server.

Run (server must be up; point at YOUR dev server, not prod :8767, if testing an edit):
    $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.session_isolation_probe
    $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.session_isolation_probe --self-test   # offline

Exit code: 0 = isolated (or self-test passed), 1 = leak / could-not-verify / error. CI-gateable.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid

# Windows console default codepage (cp1252) can't encode the check/cross marks below and would
# crash the probe right after it prints the real verdict — reconfigure stdout to UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from evals.live import (
    ConcurrentPhase,
    _detect_isolation_leaks,
    probe_concurrent,
    server_is_up,
)

OK = "✅"
NO = "❌"

# Conversation A: a PLANNING-lane prompt. Every planning turn pauses at plan approval
# (stop_reason=awaiting_approval) before executing — a DETERMINISTIC pause we can rely on to leave A
# with in-flight turn state, without depending on the local model choosing to call a specific tool.
# Kept multi-step so the planner clearly proposes a plan (and so we never actually execute it, since
# we never approve).
CLARIFY_PROMPT = (
    "Plan out how you would research and write a short comparison of three note-taking apps. "
    "Just propose the plan and wait for my approval before doing anything."
)
CLARIFY_CATEGORY = "planning"
# Conversation B: a trivial, unambiguous turn that completes in one shot with no pause and no
# ambiguity — so anything approval-shaped showing up in B's stream can only be a leak from A.
PLAIN_PROMPT = "What is 17 times 4? Reply with only the number, nothing else."


async def run_live() -> int:
    if not server_is_up():
        print(f"{NO} session_isolation_probe: no Brain server reachable.\n"
              "  Start one (free local model, from THIS worktree to test an edit):\n"
              "    $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
              ".venv\\Scripts\\python.exe src\\remote\\server.py\n"
              "  (or set CHAD_SERVER_URL to your dev instance).")
        return 1

    conv_a = f"eval_iso_A_{uuid.uuid4().hex[:8]}"
    conv_b = f"eval_iso_B_{uuid.uuid4().hex[:8]}"
    print("session_isolation_probe (LIVE, one socket, interleaved conversations)")
    print(f"  A (clarify/pause): {conv_a}")
    print(f"  B (plain/complete): {conv_b}")
    print("  driving A -> pause -> (leave in-flight) -> driving B -> complete ...\n")

    t0 = time.time()
    trace = await probe_concurrent([
        {"conv": conv_a, "prompt": CLARIFY_PROMPT, "category": CLARIFY_CATEGORY},
        {"conv": conv_b, "prompt": PLAIN_PROMPT, "category": "agent"},
    ])
    dur = time.time() - t0

    phase_a = next((p for p in trace.phases if p.conversation_id == conv_a), None)
    phase_b = next((p for p in trace.phases if p.conversation_id == conv_b), None)

    print(f"=== driven in {dur:.1f}s  error={trace.error} ===")
    for ph in trace.phases:
        card = "yes" if ph.approval else "no"
        stop = (ph.terminal or {}).get("stop_reason")
        print(f"  [{ph.conversation_id}] events={len(ph.events)} approval_card={card} "
              f"paused={ph.paused} stop_reason={stop} timed_out={ph.timed_out}")

    if trace.error:
        print(f"\n{NO} probe error before isolation could be verified: {trace.error}")
        return 1

    # The scenario is only meaningful if A actually paused (left in-flight turn state on the server).
    # A planning turn pauses deterministically; if it somehow didn't, we did NOT exercise the leak
    # vector — report INCONCLUSIVE (non-zero) rather than false-green. Do NOT silently pass.
    if phase_a is None or not phase_a.paused:
        print(f"\n{NO} INCONCLUSIVE: conversation A did not pause (expected a planning-lane "
              "awaiting_approval), so no in-flight state was left to leak. Re-run; if this persists "
              "the server may not be routing the planning lane.")
        return 1
    if phase_b is None or phase_b.paused:
        print(f"\n{NO} INCONCLUSIVE: conversation B did not complete a plain turn "
              "(no clean control conversation to check for A's state leaking in). Re-run.")
        return 1

    card = "raised a tool_approval_request card and " if phase_a.approval else ""
    print("\n================= VERDICT =================")
    if trace.isolated:
        print(f"{OK} ISOLATED: A {card}paused (tagged {conv_a}) and kept its turn state in-flight; "
              f"B ran a full turn without ever seeing A's paused/turn state, and A's paused response "
              f"{'and card ' if phase_a.approval else ''}were correctly tagged to A. "
              f"No cross-conversation leak.")
        return 0
    print(f"{NO} LEAK DETECTED — session isolation (#269 contract) is broken:")
    for leak in trace.leaks:
        print(f"    - {leak}")
    return 1


def run_self_test() -> int:
    """Offline proof the detector actually catches the incident class (would fail vs broken code),
    with zero live cost — so CI can gate the guard's correctness. Feeds synthetic phases modelling
    (a) a clean interleave, (b) the exact #269 bug (untagged approval card), (c) a mis-tagged event
    crossing conversations, and asserts the detector's verdict on each."""
    conv_a, conv_b = "convA", "convB"

    clean = [
        ConcurrentPhase(conv_a, "clarify", "agent",
                        events=[{"type": "tool_approval_request", "conversation_id": conv_a,
                                 "token": "T1", "tool_name": "ask_user_question"},
                                {"type": "ai_response", "conversation_id": conv_a,
                                 "awaiting_input": True, "stop_reason": "awaiting_input"}],
                        approval={"type": "tool_approval_request", "conversation_id": conv_a,
                                  "token": "T1"},
                        terminal={"type": "ai_response", "conversation_id": conv_a,
                                  "awaiting_input": True, "stop_reason": "awaiting_input"}),
        ConcurrentPhase(conv_b, "plain", "agent",
                        events=[{"type": "ai_response", "conversation_id": conv_b,
                                 "stop_reason": "task_completed"}],
                        terminal={"type": "ai_response", "conversation_id": conv_b,
                                  "stop_reason": "task_completed"}),
    ]

    # The exact #269 bug: A's approval card ships WITHOUT a conversation_id.
    untagged_card = [
        ConcurrentPhase(conv_a, "clarify", "agent",
                        events=[{"type": "tool_approval_request", "conversation_id": None,
                                 "token": "T1", "tool_name": "ask_user_question"}],
                        approval={"type": "tool_approval_request", "conversation_id": None,
                                  "token": "T1"}),
    ]

    # Mis-tagged: while driving B, an event tagged for A arrives (client would misattribute it).
    cross_tag = [
        ConcurrentPhase(conv_b, "plain", "agent",
                        events=[{"type": "ai_response", "conversation_id": conv_a,
                                 "stop_reason": "task_completed"}],
                        terminal={"type": "ai_response", "conversation_id": conv_a,
                                  "stop_reason": "task_completed"}),
    ]

    checks = [
        ("clean interleave -> no leaks", _detect_isolation_leaks(clean), False),
        ("untagged approval card (#269 bug) -> leak", _detect_isolation_leaks(untagged_card), True),
        ("cross-tagged event -> leak", _detect_isolation_leaks(cross_tag), True),
    ]
    ok = True
    print("session_isolation_probe --self-test (offline detector correctness)")
    for name, leaks, expect_leak in checks:
        got_leak = bool(leaks)
        passed = got_leak == expect_leak
        ok = ok and passed
        mark = OK if passed else NO
        detail = f" -> {leaks}" if leaks else ""
        print(f"  {mark} {name}{detail}")
    print(f"\n{'PASS' if ok else 'FAIL'}: detector "
          f"{'flags leaks and passes clean traces as expected' if ok else 'gave a wrong verdict'}")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="offline: prove the leak detector catches the incident class (no server).")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(run_self_test())
    sys.exit(asyncio.run(run_live()))


if __name__ == "__main__":
    main()
