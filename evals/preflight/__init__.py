r"""
preflight.py - validate every external dependency BEFORE a single eval case runs.

WHY THIS EXISTS
---------------
Eval runs are the most expensive thing in this repo: an overnight `--live` sweep is
hours of wall-clock on a serialized local-model slot. Historically the biggest waste
was never model capability - it was running for hours against a *broken environment*
and only discovering it afterwards:

  * the Google OAuth refresh token had died (issue #476), so every Gmail/Calendar/Drive
    case either hung ~100s on an interactive browser prompt nobody was there to click,
    or failed outright - contaminating a whole day of #413 sweep results;
  * the search backend was reachable but returning ZERO results, so every research case
    "failed" (see `_search_api_connected` in searxng_sidecar.py, which deliberately only
    checks the API *shape*, not that hits come back);
  * the E2B sandbox was dead, which is 27 of the 28 "the 14B model can't do workflows"
    failures that turned out to be pure infrastructure (see MEMORY: workflow failures
    are infra, not 14B);
  * LM Studio Auto-Evict had unloaded the model mid-run (issue #592).

So: this module derives *which* capabilities the selected cases actually need, proves
each one works with a real round-trip, and refuses to start the run otherwise. A failed
preflight exits 2 (distinct from 1 = evals failed) and prints a remedy per check.

DESIGN RULES
------------
1. Every check does a REAL round-trip. "The port is open" is not a check; "a query came
   back with >0 results" is. Shape-only checks are what let the empty-Brave run happen.
2. No check may ever block on human interaction. In particular the Google check must
   NEVER fall through to `InstalledAppFlow` - that interactive browser prompt IS the
   failure mode we are detecting.
3. Checks are requirement-gated. Running `--suite tool_selection` (free, no network)
   must not demand a healthy Gmail token. Requirements are inferred from the tools the
   selected cases actually expect, so a new case automatically pulls in its own checks.
4. Every check is bounded by a short timeout and reports its own elapsed time, so
   preflight itself can never become the thing that hangs a run.

LAYOUT
------
  model.py         - CheckResult / PreflightContext / status vocabulary (stdlib only)
  capabilities.py  - tool -> external-capability map + requirement inference
  checks.py        - one function per dependency + the CHECKS registry (grows over time)
  gate.py          - orchestration, rendering, CLI

Import from `evals.preflight` (this package); the submodule layout is an implementation
detail.
"""

from evals.preflight.capabilities import (capability_for_tool, requirements_for_cases,
                                              _registered_tool_names, _tool_exists)
from evals.preflight.checks import CHECKS
from evals.preflight.gate import (PreflightReport, estimate_run_seconds, main,
                                      run_preflight)
from evals.preflight.model import (FAIL, OK, SKIP, WARN, CheckResult, PreflightContext)

__all__ = [
    "CHECKS", "CheckResult", "PreflightContext", "PreflightReport",
    "capability_for_tool", "requirements_for_cases", "run_preflight",
    "estimate_run_seconds", "main", "OK", "WARN", "FAIL", "SKIP",
    # Used by evals.scoreboard's coverage report to resolve tool names.
    "_registered_tool_names", "_tool_exists",
]
