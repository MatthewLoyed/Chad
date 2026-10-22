"""
Startup Timing Collector
Lightweight global registry of named phase checkpoints recorded during Brain boot.
Used to print a structured min/max/mean-able timing summary at the end of startup,
bracketing the REAL boot path (server.py -> RemoteFactory -> SystemFactory) exactly
as it executes for users. Not a simulated/synthetic benchmark.

Usage:
    from src.shared.startup_timing import mark, checkpoint, dump_summary

    mark("process_start")              # record an absolute timestamp checkpoint
    with checkpoint("tool_discovery"):  # record a duration for a phase
        ...

dump_summary() prints a structured table (phase -> duration ms) and writes the
same data as a JSON line to data/startup_timings.jsonl for later aggregation
across multiple runs (used by the benchmark harness).
"""

import json
import os
import time
import contextlib
from pathlib import Path
from typing import Dict, List, Tuple

# Process-wide singleton state (one server process = one boot = one timing record)
_T0 = time.perf_counter()
_marks: Dict[str, float] = {}
_durations: List[Tuple[str, float]] = []  # (phase_name, duration_seconds), in completion order


def mark(name: str) -> float:
    """Record an absolute elapsed-since-process-start checkpoint (seconds). Returns the elapsed value."""
    elapsed = time.perf_counter() - _T0
    _marks[name] = elapsed
    return elapsed


@contextlib.contextmanager
def checkpoint(name: str):
    """Context manager that records the wall-clock duration of the wrapped block."""
    start = time.perf_counter()
    try:
        yield
    finally:
        dur = time.perf_counter() - start
        _durations.append((name, dur))


def record_duration(name: str, duration_seconds: float) -> None:
    """Manually record a duration for a phase that can't use the context manager
    (e.g. it's measured inside asyncio.gather with externally-computed timing)."""
    _durations.append((name, duration_seconds))


# Free-form flags for boolean/path facts discovered during boot (cache hit/miss,
# which backend a fallback-capable subsystem actually used, etc.) — surfaced
# verbatim in the dump_summary() "extra"/flags section so the benchmark report
# can say e.g. "tool cache: HIT" or "skill loader: Supabase" definitively.
_flags: Dict[str, str] = {}


def set_flag(name: str, value: str) -> None:
    _flags[name] = value


def get_flags() -> Dict[str, str]:
    return dict(_flags)


def elapsed_since_start() -> float:
    """Seconds elapsed since this process imported this module (proxy for process start)."""
    return time.perf_counter() - _T0


def get_marks() -> Dict[str, float]:
    return dict(_marks)


def get_durations() -> List[Tuple[str, float]]:
    return list(_durations)


def dump_summary(logger=None, extra: Dict = None) -> Dict:
    """
    Build a structured summary dict, log it (if a logger is given) as a readable
    table, and append a JSON line to data/startup_timings.jsonl for cross-run
    aggregation. Returns the summary dict.
    """
    total_s = elapsed_since_start()

    summary = {
        "timestamp": time.time(),
        "total_startup_s": round(total_s, 4),
        "marks_s": {k: round(v, 4) for k, v in _marks.items()},
        "durations_ms": [(name, round(dur * 1000, 1)) for name, dur in _durations],
        "flags": dict(_flags),
    }
    if extra:
        summary["extra"] = extra

    lines = ["", "=" * 70, "STARTUP TIMING SUMMARY (real boot path)", "=" * 70]
    lines.append(f"{'Phase':<45}{'Duration (ms)':>15}")
    lines.append("-" * 70)
    for name, dur in _durations:
        lines.append(f"{name:<45}{dur * 1000:>15.1f}")
    lines.append("-" * 70)
    lines.append(f"{'Checkpoints (elapsed since process start)':<45}")
    for name, t in _marks.items():
        lines.append(f"  {name:<43}{t * 1000:>15.1f}")
    lines.append("-" * 70)
    lines.append(f"{'TOTAL WALL CLOCK (import -> ready)':<45}{total_s * 1000:>15.1f}")
    if _flags:
        lines.append("-" * 70)
        lines.append("Flags (path taken / cache state):")
        for k, v in _flags.items():
            lines.append(f"  {k}: {v}")
    lines.append("=" * 70)
    text = "\n".join(lines)

    if logger:
        logger.warning(text)
    else:
        print(text)

    try:
        out_path = Path("data/startup_timings.jsonl")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
    except Exception:
        pass

    return summary
