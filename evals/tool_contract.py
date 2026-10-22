"""Native-tool contract check (free, deterministic — no model, no server).

CLAUDE.md requires every native tool to ship a display formatter
(`format_action` / `@tool_display`) AND a matching entry in
`src/clients/web/src/utils/tool-ui.tsx`. Until issue #247 this was only a
boot-time WARNING, so drift crept in silently.

This lane turns the contract into a CI gate using a *ratchet*: the current
set of pre-existing gaps is grandfathered in `evals/datasets/tool_contract_baseline.json`,
and the check fails only when a tool NOT in the baseline violates the
contract — i.e. on newly-added drift. Fixing a grandfathered tool and
removing it from the baseline tightens the gate.

Run:  python -m evals.runner --tool-contract
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "evals" / "datasets" / "tool_contract_baseline.json"
TOOL_UI_PATH = PROJECT_ROOT / "src" / "clients" / "web" / "src" / "utils" / "tool-ui.tsx"

# TOOL_METADATA record keys: `  some_tool: { title: ... }`
_UI_KEY_RE = re.compile(r"^\s*([a-zA-Z0-9_]+):\s*\{\s*title:", re.M)


def _load_baseline() -> Tuple[Set[str], Set[str]]:
    if not BASELINE_PATH.exists():
        return set(), set()
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return set(data.get("missing_formatter", [])), set(data.get("missing_tool_ui", []))


def _tool_ui_keys() -> Set[str]:
    if not TOOL_UI_PATH.exists():
        return set()
    return set(_UI_KEY_RE.findall(TOOL_UI_PATH.read_text(encoding="utf-8")))


def _scan_native_tools() -> Tuple[Set[str], Set[str], int]:
    """Return (tools_missing_formatter, tools_missing_ui, total_native).

    Hydrates stub tools so every native BaseTool is inspected, not just the
    eagerly-registered handful.
    """
    from src.infrastructure.tools.registry import ToolRegistry
    from src.tools.base_tool import BaseTool

    registry = ToolRegistry()
    registry.discover_all()
    ui_keys = _tool_ui_keys()

    missing_fmt: Set[str] = set()
    missing_ui: Set[str] = set()
    total = 0
    for name in list(registry._tools.keys()):
        if registry._tool_sources.get(name) != "local":
            continue
        instance = registry.get_tool(name)
        if not isinstance(instance, BaseTool):
            continue
        total += 1
        if not type(instance).has_display_formatter():
            missing_fmt.add(name)
        if name not in ui_keys:
            missing_ui.add(name)
    return missing_fmt, missing_ui, total


def run_tool_contract() -> Tuple[bool, str]:
    """Run the contract check. Returns (passed, markdown_report)."""
    base_fmt, base_ui = _load_baseline()
    missing_fmt, missing_ui, total = _scan_native_tools()

    # New violations = current gaps not grandfathered in the baseline.
    new_fmt = sorted(missing_fmt - base_fmt)
    new_ui = sorted(missing_ui - base_ui)

    # Housekeeping signal: baseline entries that are now compliant (ratchet
    # them out) or no longer exist (stale — remove).
    fixed_fmt = sorted(base_fmt - missing_fmt)
    fixed_ui = sorted(base_ui - missing_ui)

    passed = not new_fmt and not new_ui

    lines: List[str] = []
    lines.append("# Native-tool contract check\n")
    lines.append(f"- native tools scanned: **{total}**")
    lines.append(f"- grandfathered (baseline): {len(base_fmt)} missing-formatter, "
                 f"{len(base_ui)} missing-tool-ui")
    lines.append(f"- result: {'✅ PASS' if passed else '❌ FAIL'}\n")

    if new_fmt:
        lines.append(f"## ❌ New tools missing a display formatter ({len(new_fmt)})")
        lines.append("Add `format_action()` or `@tool_display`. See "
                     "`src/infrastructure/tools/README.md`.")
        for n in new_fmt:
            lines.append(f"- `{n}`")
        lines.append("")
    if new_ui:
        lines.append(f"## ❌ New tools missing a `tool-ui.tsx` entry ({len(new_ui)})")
        lines.append("Add a `TOOL_METADATA` entry in "
                     "`src/clients/web/src/utils/tool-ui.tsx`.")
        for n in new_ui:
            lines.append(f"- `{n}`")
        lines.append("")

    if fixed_fmt or fixed_ui:
        lines.append("## 🔧 Baseline can be tightened")
        lines.append("These tools are listed in the baseline but now comply (or no "
                     "longer exist). Remove them from "
                     "`evals/datasets/tool_contract_baseline.json`:")
        for n in fixed_fmt:
            lines.append(f"- formatter ok / gone: `{n}`")
        for n in fixed_ui:
            lines.append(f"- tool-ui ok / gone: `{n}`")
        lines.append("")

    if passed and not (fixed_fmt or fixed_ui):
        lines.append("All native tools satisfy the contract (modulo the grandfathered baseline).")

    return passed, "\n".join(lines)
