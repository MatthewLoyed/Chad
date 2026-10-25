"""
limits.py — every harness boundary the model can run into, declared in ONE place.

Before this, a boundary could be a module constant in `routing.py`, a private
`_STEP_TURN_HARD` in `supervisor_node.py`, a dict in `tool_timeouts.py`, or a
`ConfigManager.get` buried three calls deep in `handoffs.py`. Answering "why did
that run stop?" meant grepping for magic numbers. Now: read `describe_all()`.

TWO KINDS OF BOUNDARY — pick deliberately
-----------------------------------------
`HINT`    Advisory. The harness injects guidance and the model keeps working.
          Nothing is cancelled. Use this by default.
`CEILING` Enforced. The action is refused or the loop stops.

The default must be HINT. A ceiling that fires mid-task destroys work the model
already did and, on local inference, saves a resource we are not short of —
there is no per-token bill and no quota, so an extra loop costs wall-clock and
nothing else. What we ARE short of is context and the model's attention, and
those are protected by `failure.py` (compact, deduplicated failure text), not by
killing the run. Reach for a CEILING only when continuing is genuinely
destructive or definitely futile: an unbounded external spend, or an identical
call that has already failed.

Where a ceiling is unavoidable, pair it with a hint threshold BELOW it, so the
model gets told it is close and can land the plane on its own terms. A ceiling
that fires with no warning is a harness failure, not a model failure.

ADDING A BOUNDARY
-----------------
1. Add a `Limit(...)` to `LIMITS` below with a config key and a real default.
2. Read it via its accessor. Never inline the number at the call site.
3. If it's a CEILING, add the matching `_soft` hint limit too.
4. Document the key in `config/config.yaml` under the owning section.

See docs/architecture/FAILURE_AND_LIMITS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Union

HINT = "hint"
CEILING = "ceiling"

Number = Union[int, float]


@dataclass(frozen=True)
class Limit:
    """One boundary: where it's configured, what it defaults to, and what it does."""

    name: str
    key: str          # ConfigManager dot-path
    default: Number
    kind: str         # HINT | CEILING
    what: str         # one line, shown by describe_all()

    def get(self) -> Number:
        from src.shared.config_manager import ConfigManager

        try:
            raw = ConfigManager.get(self.key, self.default)
            if raw is None:
                return self.default
            return type(self.default)(raw)
        except (TypeError, ValueError):
            return self.default


# ── The registry ─────────────────────────────────────────────────────────────
# Existing config keys are reused as-is rather than renamed — moving them would
# silently ignore whatever a user already set in their config.yaml.

LIMITS: Dict[str, Limit] = {
    lim.name: lim
    for lim in [
        Limit("setup_bundle_timeout", "setup.bundle_test_timeout_s", 240.0, HINT,
              "Sequential, user-confirmed main/secondary/selector setup checks."),
        Limit("setup_validation_ttl", "setup.validation_ttl_s", 1800.0, HINT,
              "Role verification lifetime before selected routes must be retested."),
        Limit("setup_test_timeout", "setup.test_timeout_s", 60.0, HINT,
              "Explicit onboarding chat test timeout; the user can retry after checking the provider."),
        Limit("setup_test_tokens", "setup.test_max_tokens", 64, HINT,
              "Requested output budget for a user-confirmed onboarding chat test, including hosted providers."),
        Limit(
            "setup_network_timeout", "setup.network_timeout_s", 30.0, HINT,
            "Setup download read/connect timeout; partial files remain resumable on explicit retry.",
        ),
        Limit(
            "setup_download_timeout", "setup.download_timeout_s", 7200.0, HINT,
            "Setup model download session budget; expiry pauses the transfer without discarding progress.",
        ),
        Limit(
            "model_assessment_timeout", "setup.model_assessment_timeout_s", 60.0, HINT,
            "LM Studio estimate or benchmark request timeout; a failed measurement leaves performance unknown.",
        ),
        Limit(
            "model_benchmark_tokens", "setup.model_benchmark_tokens", 256, HINT,
            "Output tokens requested for an opt-in short local generation measurement.",
        ),
        Limit(
            "hardware_probe_timeout", "setup.hardware_probe_timeout_s", 8.0, HINT,
            "Read-only hardware subprocess timeout; failed probes leave fields unknown and permit manual entry.",
        ),
        # --- supervisor loop -------------------------------------------------
        Limit(
            "supervisor_turns_soft", "orchestration.supervisor_turns_soft", 15, HINT,
            "Supervisor tool-turns before the model is told it is running long.",
        ),
        Limit(
            "supervisor_turns_max", "orchestration.supervisor_turns_max", 60, CEILING,
            "Supervisor tool-turns before the turn is ended. Was a silent hard 25.",
        ),
        # --- plan steps (thresholds landed in 8b7aa78d, now configurable) ----
        Limit(
            "step_turns_soft", "planner.step_turns_soft", 10, HINT,
            "Tool-turns on one plan step before a 'you may be looping' nudge.",
        ),
        Limit(
            "step_turns_max", "planner.step_turns_max", 20, HINT,
            "Tool-turns on one plan step before the harness forces a different "
            "approach. Still a HINT: the step stays open, nothing is discarded.",
        ),
        Limit(
            "scout_max_tool_calls", "planner.scout_max_tool_calls", 3, HINT,
            "Scout gathering calls before it is told to report back to the planner.",
        ),
        # --- delegates -------------------------------------------------------
        Limit(
            "delegate_failures_soft", "subagents.delegate_failures_soft", 2, HINT,
            "FAILED delegations to one specialist before the model is warned.",
        ),
        Limit(
            "delegate_failures_max", "subagents.delegate_failures_max", 4, CEILING,
            "FAILED delegations to one specialist before further ones are refused. "
            "Counts failures only — successful delegation is not a strike.",
        ),
        # --- tool execution --------------------------------------------------
        Limit(
            "tool_timeout_default_s", "tools.tool_timeout_default_s", 120.0, CEILING,
            "Default per-tool wall-clock cap. Was 60s, which cut off legitimate "
            "local-model work; per-tool overrides live in tool_timeouts.py.",
        ),
        Limit(
            "repeat_failure_block_after", "tools.repeat_failure_block_after", 1, CEILING,
            "Identical failing calls (same tool, same args) before the harness "
            "refuses. Futile by definition — the one ceiling that costs nothing. "
            "1 (block on the first repeat) is probe-validated: #317 found "
            "first-failure blocking stops browser loops faster than two strikes.",
        ),
        Limit(
            "repeat_success_block_after", "tools.repeat_success_block_after", 3, CEILING,
            "Identical successful calls (same tool, same args) before the harness "
            "refuses. The result is already in context — re-calling identical queries "
            "3+ times is a runaway repetition loop.",
        ),
        # --- context budget ---------------------------------------------------
        Limit(
            "tool_output_max_chars", "tools.tool_output_max_chars", 16000, CEILING,
            "Chars kept from a SUCCESSFUL tool result before truncation.",
        ),
        Limit(
            "browser_snapshot_max_chars", "tools.browser_snapshot_max_chars", 24000, CEILING,
            "Chars kept from browser_snapshot before truncation (preserves large SPA interactive trees).",
        ),
        Limit(
            "failed_output_max_chars", "tools.failed_output_max_chars", 1800, CEILING,
            "Chars kept from a FAILED tool result. Tighter on purpose — a failure "
            "needs its class and next actions, not its stack trace.",
        ),
        # --- bounded compaction context (does not terminate execution) --------
        Limit(
            "resume_context_target_chars", "consolidation.resume_context_target_chars", 1800, HINT,
            "Target characters for task references and rationale in a compaction summary.",
        ),
        Limit(
            "resume_context_max_chars", "consolidation.resume_context_max_chars", 2400, CEILING,
            "Maximum rendered task-context characters in a wake message; bounds data, not task execution.",
        ),
        # --- external spend (real ceilings: these cost money / hit rate limits) --
        Limit(
            "browser_max_actions_per_task", "tools.browser_max_actions_per_task", 40, CEILING,
            "browser_* MCP calls per conversation before browser_agent must stop.",
        ),
        Limit(
            "search_budget_per_task", "subagents.search_budget_per_task", 50, CEILING,
            "Shared web_search results one deep-research task may draw.",
        ),
        # --- generation-level nudges ------------------------------------------
        Limit(
            "reasoning_token_cap", "planner.reasoning_token_cap", 10000, HINT,
            "Estimated reasoning tokens with no tool call before the stream is "
            "cancelled and resent with a corrective nudge. <=0 disables.",
        ),
        Limit(
            "narration_nudge_enabled", "planner.narration_nudge_enabled", 1, HINT,
            "1 = catch 'I will now do X' deferral mid-stream and resend telling "
            "the model to act. 0 = off.",
        ),
    ]
}


def get(name: str) -> Number:
    """Current value of a named limit. Raises on a typo rather than silently defaulting."""
    if name not in LIMITS:
        raise KeyError(f"Unknown limit '{name}'. Known: {', '.join(sorted(LIMITS))}")
    return LIMITS[name].get()


def describe_all() -> List[Dict[str, Any]]:
    """Every boundary with its live value — for diagnostics, evals, and docs."""
    return [
        {
            "name": lim.name,
            "config_key": lim.key,
            "default": lim.default,
            "value": lim.get(),
            "kind": lim.kind,
            "what": lim.what,
        }
        for lim in sorted(LIMITS.values(), key=lambda x: x.name)
    ]


def render_table() -> str:
    """Human-readable dump. `python -m src.core.tools.limits`."""
    rows = describe_all()
    width = max(len(r["name"]) for r in rows)
    lines = [f"{'LIMIT'.ljust(width)}  {'KIND':<8} {'VALUE':>8}  CONFIG KEY"]
    lines.append("-" * (width + 46))
    for r in rows:
        changed = "" if r["value"] == r["default"] else f"  (default {r['default']})"
        lines.append(
            f"{r['name'].ljust(width)}  {r['kind']:<8} {str(r['value']):>8}  "
            f"{r['config_key']}{changed}"
        )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(render_table())
