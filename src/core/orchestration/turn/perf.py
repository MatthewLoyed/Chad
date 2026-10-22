"""Per-step wall-clock timing for the pre-model phase of a turn.

The "why is the first token 20s away?" question can't be answered from the WS
event stream alone — status events only bracket coarse phases and collapse
history-fetch, intent classification, embedding shortlist, and the 9B tool pick
into one opaque gap. :class:`TurnPerfTracker` records an exact duration for each
step the :class:`~src.core.orchestration.turn_coordinator.TurnCoordinator` walks
through before the supervisor model is invoked, so the bottleneck is attributable
to a named step rather than guessed.

Overhead is one ``perf_counter()`` read per ``mark`` — negligible on the hot path.
The result is emitted as a single structured ``[ PERF ]`` log line (always, at
WARNING so it survives prod's log level) and surfaced on the turn's metrics /
final ``token_usage`` event so a benchmark probe can capture exact numbers.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


class TurnPerfTracker:
    """Records ordered, named per-step durations for the pre-model turn phase.

    Usage::

        perf = TurnPerfTracker()
        history = await load_history()
        perf.mark("history")
        ...
        perf.mark("tools")
        perf.annotate(tool_9b_ms=..., shortlist_size=...)
        logger.warning(perf.format_line())
        metrics["pre_model_breakdown"] = perf.as_dict()
    """

    def __init__(self, t0: float | None = None) -> None:
        self._t0 = t0 if t0 is not None else time.perf_counter()
        self._last = self._t0
        self._steps: List[Tuple[str, float]] = []
        self._extra: Dict[str, Any] = {}

    def mark(self, label: str) -> float:
        """Record the elapsed time since the previous mark under ``label``.

        Returns the step duration in milliseconds.
        """
        now = time.perf_counter()
        dur_ms = (now - self._last) * 1000.0
        self._steps.append((label, dur_ms))
        self._last = now
        return dur_ms

    def annotate(self, **kv: Any) -> None:
        """Attach extra scalar context (e.g. the isolated 9B latency, shortlist size)."""
        self._extra.update(kv)

    @property
    def total_ms(self) -> float:
        """Total wall-clock ms across every recorded step."""
        return (self._last - self._t0) * 1000.0

    def as_dict(self) -> Dict[str, Any]:
        """Compact, JSON-serialisable summary for metrics / WS transport."""
        return {
            "steps": {label: round(dur, 1) for label, dur in self._steps},
            "total_ms": round(self.total_ms, 1),
            **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in self._extra.items()},
        }

    def format_line(self) -> str:
        """One-line human-readable breakdown, largest step first for quick triage."""
        ranked = sorted(self._steps, key=lambda s: s[1], reverse=True)
        parts = "  ".join(f"{label}={dur:.0f}ms" for label, dur in ranked)
        extra = ""
        if self._extra:
            extra = "  |  " + " ".join(f"{k}={v}" for k, v in self._extra.items())
        return f"[ PERF ] pre_model total={self.total_ms:.0f}ms  ::  {parts}{extra}"
