"""
Shared per-tool execution timeouts.

Used on both sides of the Brain/Limb split so the Brain's wait for a
TOOL_RESPONSE and the Limb's actual execution agree on how long a given
tool is allowed to run. Keeping a single source of truth avoids the Limb
giving up before the Brain (or vice versa), which otherwise causes
mismatched/zombie responses.

The DEFAULT lives in src/core/tools/limits.py (`tool_timeout_default_s`) with
every other harness boundary; only genuine per-tool exceptions belong below.

Why the default moved 60s -> 120s: 60 was set for cloud-API latencies and is
simply wrong for local inference. Anything that fans out, renders, or waits on a
local model routinely needs more, and a timeout is one of the most expensive
failures we can hand a model — it burns the wall-clock, produces no result, and
(before the shared failure envelope) told it nothing about whether retrying was
worth it. Being slightly generous costs wall-clock on a genuinely-hung tool;
being too tight kills work that was about to succeed.
"""

from src.core.tools import limits

# Per-tool overrides (seconds). Add one only when a tool is legitimately slower
# than the default — not to paper over a hang.
TOOL_TIMEOUT_OVERRIDES = {
    "organize_folder": 360.0,  # 6 minutes - includes user approval wait time
    "propose_file_plan": 360.0,  # 6 minutes - user needs time to review plan
    "build_html_file": 600.0,  # 10 minutes for slow local models or complex HTML
    "generate_presentation": 360.0,  # 6 minutes — Presenton LLM deck generation + download
    "refresh_google_auth": 300.0,  # 5 minutes - interactive browser OAuth flow
    # Web fetch/search hit third-party latency plus our own tiered fallbacks
    # (SearXNG -> Brave -> Playwright); the tail is much longer than a local call.
    "web_search": 180.0,
    "fetch_url": 180.0,
    # Sandbox execution has its own EXECUTION_TIMEOUT (300s) internally; the outer
    # bound must exceed it or we kill the call before the tool can report its own
    # timeout as a classified failure.
    "run_sandboxed_bash": 330.0,
    "run_sandboxed_python": 330.0,
}


def get_tool_timeout(tool_name: str) -> float:
    """Wall-clock budget for one call to `tool_name`."""
    return float(TOOL_TIMEOUT_OVERRIDES.get(tool_name, limits.get("tool_timeout_default_s")))


def default_tool_timeout() -> float:
    return float(limits.get("tool_timeout_default_s"))
