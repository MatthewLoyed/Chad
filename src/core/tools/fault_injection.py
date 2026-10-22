"""
fault_injection.py — deterministically force a chosen tool failure, for measurement.

WHY THIS EXISTS
---------------
The whole failure/limits/nudge stack is unfalsifiable without it. We can say a
`retryable: false` hint *should* stop a retry loop, but until a failure can be
summoned on demand nobody can show that it does. This makes recovery behaviour a
number: force `auth` on the 1st `web_search` call, run the turn, score what the
model did next.

That measurement is nearly free here. Local inference has no per-token bill, so
Repair@1/3/5 — the standard way this is reported — costs wall-clock only. It also
gives the Chad-on-Chad loop the built-in verification signal CHAD_CODE.md names
as its key missing piece: "did this prompt edit improve recovery?" becomes a
before/after run rather than an opinion.

SAFETY POSTURE
--------------
OFF unless explicitly armed, and it can only be armed two ways:

  * `CHAD_FAULT_INJECTION` in the environment (JSON), or
  * the `injecting(...)` context manager, in-process.

There is no config.yaml key on purpose — a stray committed `true` in a shared
config file must not be able to make a production Brain start failing real tool
calls. Arming is always an explicit act by the process that wants it, and the
context manager always disarms on exit.

Every injected failure is logged at WARNING with an `[ INJECTED ]` marker, so a
trace can never be mistaken for a genuine outage.

USAGE
-----
    from src.core.tools.fault_injection import injecting, Fault

    with injecting(Fault(tool="web_search", on_call=1, failure_class="auth")):
        trace = await run_turn_inprocess("find me three caterers")

Or from a shell, for a whole process:

    CHAD_FAULT_INJECTION='[{"tool":"web_search","on_call":1,"failure_class":"timeout"}]'
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.tools import failure
from src.shared.logger import setup_logger

logger = setup_logger("FaultInjection")

_ENV_KEY = "CHAD_FAULT_INJECTION"


@dataclass
class Fault:
    """Force `failure_class` when `tool` is called for the nth time.

    on_call:  1-based call index to hit. `0` means every call.
    times:    how many consecutive calls to fail once triggered (default 1).
    detail:   the raw error text the model sees; a sensible default per class.
    """

    tool: str
    failure_class: str = failure.RUNTIME
    on_call: int = 1
    times: int = 1
    detail: Optional[str] = None
    _seen: int = field(default=0, repr=False)
    _fired: int = field(default=0, repr=False)

    def matches(self, tool_name: str) -> bool:
        return self.tool == tool_name or self.tool == "*"

    def should_fire(self) -> bool:
        self._seen += 1
        if self.on_call and self._seen < self.on_call:
            return False
        if self._fired >= self.times:
            return False
        self._fired += 1
        return True


_DEFAULT_DETAIL = {
    failure.AUTH: "401 Unauthorized — invalid api key (injected)",
    failure.PERMISSION: "403 Forbidden — access denied (injected)",
    failure.CONFIG: "Missing SOME_API_KEY — not configured (injected)",
    failure.NOT_FOUND: "404 Not Found — no such resource (injected)",
    failure.BAD_INPUT: "400 invalid parameter 'query' (injected)",
    failure.RATE_LIMIT: "429 rate limit exceeded (injected)",
    failure.TIMEOUT: "Read timed out after 120s (injected)",
    failure.UNAVAILABLE: "503 Service Unavailable (injected)",
    failure.EXPIRED: "Session expired (injected)",
    failure.RUNTIME: "Tool raised an unexpected error (injected)",
}


class _State(threading.local):
    faults: List[Fault]

    def __init__(self) -> None:
        self.faults = []


_state = _State()
_env_loaded = False


def _load_env_faults() -> None:
    """Parse CHAD_FAULT_INJECTION once per process. Malformed spec disarms rather
    than crashing — a broken eval config must not take a Brain down."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True

    raw = os.environ.get(_ENV_KEY)
    if not raw:
        return
    try:
        spec = json.loads(raw)
        faults = [Fault(**item) for item in spec]
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ INJECTED ] Ignoring malformed {_ENV_KEY}: {exc}")
        return
    _state.faults = faults
    logger.warning(
        f"[ INJECTED ] Fault injection ARMED from {_ENV_KEY}: "
        f"{[(f.tool, f.failure_class, f.on_call) for f in faults]}"
    )


def armed() -> bool:
    _load_env_faults()
    return bool(_state.faults)


@contextmanager
def injecting(*faults: Fault):
    """Arm the given faults for the duration of the block, then disarm."""
    previous = _state.faults
    _state.faults = list(faults)
    logger.warning(
        f"[ INJECTED ] Fault injection ARMED: "
        f"{[(f.tool, f.failure_class, f.on_call) for f in faults]}"
    )
    try:
        yield
    finally:
        _state.faults = previous
        logger.warning("[ INJECTED ] Fault injection disarmed.")


def maybe_fail(tool_name: str) -> Optional[Dict[str, Any]]:
    """Return a failure envelope if a fault is armed for this call, else None."""
    if not armed():
        return None

    for fault in _state.faults:
        if not fault.matches(tool_name) or not fault.should_fire():
            continue
        detail = fault.detail or _DEFAULT_DETAIL.get(
            fault.failure_class, "Injected failure"
        )
        logger.warning(
            f"[ INJECTED ] Forcing {fault.failure_class} on {tool_name} "
            f"(call #{fault._seen})"
        )
        envelope = failure.failure(
            detail, failure_class=fault.failure_class, tool_name=tool_name
        )
        # Marked so a trace reader (and any human) can tell a synthetic failure
        # from a real one without cross-referencing the run config.
        envelope["injected"] = True
        return envelope
    return None


def reset() -> None:
    """Disarm and clear counters. Tests."""
    global _env_loaded
    _state.faults = []
    _env_loaded = False
