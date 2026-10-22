r"""
model.py - preflight's data types and the check-status vocabulary.

Deliberately dependency-free (stdlib only) so both the check implementations and the
orchestration layer can import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Status vocabulary. Only "fail" blocks the run; "warn" is printed loudly and recorded
# in the run manifest so a later regression can be attributed to it.
OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

_STATUS_ICON = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", SKIP: "[SKIP]"}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    remedy: str = ""
    elapsed_s: float = 0.0
    # Machine-readable facts folded into the run manifest, so a run's environment is
    # reconstructable months later ("was the token fresh when this ran?").
    facts: Dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status == FAIL


@dataclass
class PreflightContext:
    """Everything a check needs to know about the run it is gating."""
    judge_model: Optional[str] = None
    lanes: List[str] = field(default_factory=list)
    suites: List[str] = field(default_factory=list)
    cases: List[Any] = field(default_factory=list)
    # Best-effort estimate of how long the run will take, used by the OAuth
    # expiry-horizon check to answer "will this credential outlive the run?".
    estimated_run_s: float = 0.0
    server_health_url: Optional[str] = None
    # {suite_name: error} for suites that failed to parse. A suite that won't load is a
    # blocking data problem, not a warning to scroll past - `--all` would otherwise just
    # skip it and silently under-report coverage.
    load_errors: Dict[str, str] = field(default_factory=dict)
