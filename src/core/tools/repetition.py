"""
repetition.py — the live non-progress guard, on every tool call.

Sits at `ToolRegistry.execute_tool`, the one choke point every lane goes through
(the LangGraph tool node, subagents, voice, fast-track). That placement is the
whole point: `LoopDetector` has had good pattern detection since the OpenClaw
port, but it lives behind `ToolCoordinator`, which the LangGraph lane bypasses —
so on the main path nothing generic was watching for loops. Only the sandbox and
`browser_*` families had ad-hoc blockers of their own.

WHAT IT WATCHES FOR
-------------------
Three distinct shapes of "not making progress", each with its own response:

1. `identical_failing_repeat` -> BLOCK. Same tool, same arguments, already failed.
   Futile by definition, so refusing costs nothing. This generalizes the
   sandbox/browser blockers to every tool.
2. `redundant_success` -> HINT. Same tool, same arguments, already SUCCEEDED.
   Not an error — just wasted runway, and the answer is already in context.
3. `consecutive` / `ping_pong` -> HINT. The model is cycling (A,A,A or A,B,A,B)
   without converging, even though individual calls may be succeeding.

Only (1) blocks. (2) and (3) are advisory because they can be legitimate — three
`read_file` calls in a row is normal work, and so is search-then-open-then-search.
A hint costs a line of context; a wrong block destroys real work. See
`limits.py` for why HINT is the default posture.

TURN SCOPING
------------
State is keyed on `turn_id_var` and dropped when the turn changes. The existing
sandbox/browser counters are instance state on a process-lifetime singleton and
are never reset, so a call that failed once is blocked for the life of the
process, across unrelated conversations. This one cannot do that.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from src.core.tools import failure, limits
from src.core.tools.loop_detector import LoopDetector
from src.shared.logger import setup_logger

logger = setup_logger("RepetitionGuard")

# How many entries of call history one turn keeps. Bounded so a very long
# autonomous run can't grow this without limit.
_HISTORY_CAP = 40

# Observations depend on mutable desktop state, not just their argument list.
# Re-reading after a click/window transition must never reuse a prior screen.
_FRESH_OBSERVATION_TOOLS = frozenset({
    "browser_snapshot", "browser_scroll", "screenshot_control", "Screenshot", "Snapshot",
    "ui_find", "ui_read",
})


def _is_fresh_observation(tool_name: str, params: Dict[str, Any]) -> bool:
    return tool_name in _FRESH_OBSERVATION_TOOLS or (
        tool_name == "window_management" and params.get("action") in {
            "list", "find", "get_state", "wait_for", "wait_for_state",
        }
    )

# Families whose own blocker does strictly MORE than refuse the call, and so must
# be allowed to run instead of being pre-empted here (this guard sits earlier, in
# the registry; theirs sits inside the executor):
#   browser_*  — runs vision-assist to ground the failing target before blocking
#   sandbox    — latches an auth failure across ALL sandbox tools, not just this
#                signature, so one 401 stops the whole family
# Their outcomes are still RECORDED here, so the advisory pattern hints below
# still apply to them; only the block verdict is deferred.
_DOMAIN_GUARDED_PREFIXES = ("browser_",)
_DOMAIN_GUARDED_NAMES = frozenset({
    "run_sandboxed_bash", "run_sandboxed_python", "install_package",
    "download_sandbox_file",
})


def _has_domain_blocker(tool_name: str) -> bool:
    return tool_name in _DOMAIN_GUARDED_NAMES or tool_name.startswith(
        _DOMAIN_GUARDED_PREFIXES
    )


def _signature(tool_name: str, params: Dict[str, Any]) -> str:
    """Identity of 'this exact call'. Internal plumbing args are excluded — they
    vary between otherwise-identical calls and would defeat the match."""
    scrubbed = {
        k: v for k, v in (params or {}).items()
        if not k.startswith("_") and k != "tool_registry"
    }
    blob = json.dumps(scrubbed, sort_keys=True, default=str)
    return f"{tool_name}:{hashlib.md5(blob.encode()).hexdigest()[:12]}"


class RepetitionGuard:
    """One turn's worth of call history and the verdicts drawn from it."""

    def __init__(self) -> None:
        self._outcomes: Dict[str, Dict[str, int]] = {}
        self._sequence: List[str] = []
        # Reuse the existing detector for consecutive/ping-pong shapes rather than
        # re-deriving them. Its poll-pattern window is deliberately widened below.
        self._detector = LoopDetector(
            max_consecutive=3,
            # The stock 5s window assumes cloud latency. A local model's calls are
            # often minutes apart, so a 5s window could never fire — which is part
            # of why this detection never earned its keep before.
            max_poll_window=600.0,
            max_poll_count=3,
            max_ping_pong_cycles=2,
        )

    # -- before execution -------------------------------------------------

    def check(self, tool_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a failure envelope if this call must be refused, else None."""
        if _has_domain_blocker(tool_name) or _is_fresh_observation(tool_name, params):
            return None

        sig = _signature(tool_name, params)
        failures = self._outcomes.get(sig, {}).get("failure", 0)
        block_after = int(limits.get("repeat_failure_block_after"))

        if failures >= block_after:
            logger.warning(
                "[ REPETITION ] Blocking %s — identical args already failed %d time(s).",
                tool_name, failures,
            )
            return failure.failure(
                f"'{tool_name}' with these exact arguments has already failed "
                f"{failures} time(s) in this turn.",
                failure_class=failure.BLOCKED,
                tool_name=tool_name,
                next_actions=[
                    "This exact call is refused — an identical repeat cannot succeed.",
                    "Change the arguments, use a different tool, or take a different "
                    "route to the goal.",
                    "If you have no other route, say so and deliver what you have.",
                ],
            )

        successes = self._outcomes.get(sig, {}).get("success", 0)
        success_block_after = int(limits.get("repeat_success_block_after"))
        if successes >= success_block_after:
            logger.warning(
                "[ REPETITION ] Blocking %s — identical args already succeeded %d time(s).",
                tool_name, successes,
            )
            return failure.failure(
                f"'{tool_name}' with these exact arguments has already succeeded "
                f"{successes} time(s) in this conversation. The results are already available above.",
                failure_class=failure.BLOCKED,
                tool_name=tool_name,
                next_actions=[
                    "Do NOT call this tool with these arguments again.",
                    "Reuse the existing result from prior turns/steps.",
                    "Move on to the next step of the plan or formulate the final deliverable.",
                ],
            )
        return None

    # -- after execution --------------------------------------------------

    def note(self, tool_name: str, params: Dict[str, Any], success: bool) -> Optional[str]:
        """Record the outcome; return an advisory hint for the model, if any."""
        sig = _signature(tool_name, params)
        bucket = self._outcomes.setdefault(sig, {"success": 0, "failure": 0})
        bucket["success" if success else "failure"] += 1

        hint = self._pattern_hint(tool_name, params, sig, bucket, success)

        self._detector.record_execution(tool_name, params)
        self._sequence.append(tool_name)
        if len(self._sequence) > _HISTORY_CAP:
            self._sequence.pop(0)
        return hint

    def _pattern_hint(
        self,
        tool_name: str,
        params: Dict[str, Any],
        sig: str,
        bucket: Dict[str, int],
        success: bool,
    ) -> Optional[str]:
        # Re-running a call that already worked: the answer is in context above.
        if success and bucket["success"] >= 2 and not _is_fresh_observation(tool_name, params):
            return (
                f"HARNESS NOTE: you have now called {tool_name} with these exact "
                f"arguments {bucket['success']} times and it succeeded every time. "
                f"The result is already in this conversation — reuse it instead of "
                f"calling again, and move on to the next part of the task."
            )

        # Cycling shapes. `check_loop` reports against history NOT including the
        # call just recorded, which is what we want: it answers "was this call a
        # continuation of a pattern?".
        is_loop, description = self._detector.check_loop(tool_name, params)
        if is_loop:
            return (
                f"HARNESS NOTE: {description} You are cycling rather than "
                f"converging. Change approach: use what you already have, try a "
                f"different tool, or move to the next step."
            )
        return None


# ── Turn-scoped access ───────────────────────────────────────────────────────
# Keyed on turn_id so state cannot leak between turns or conversations.

_active_turn: Optional[str] = None
_active_guard: Optional[RepetitionGuard] = None


def current_guard() -> RepetitionGuard:
    """The guard for the active turn, recreated whenever the turn changes."""
    global _active_turn, _active_guard

    try:
        from src.core.orchestration.context import turn_id_var

        turn_id = turn_id_var.get()
    except Exception:
        turn_id = None

    if _active_guard is None or turn_id != _active_turn:
        _active_turn = turn_id
        _active_guard = RepetitionGuard()
    return _active_guard


def reset() -> None:
    """Drop the active guard. Tests and explicit turn boundaries."""
    global _active_turn, _active_guard
    _active_turn = None
    _active_guard = None
