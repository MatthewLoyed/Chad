"""One-off audit for issue #537: which tool descriptions get truncated by
LLMToolSelector._DESC_CHAR_CAP (240 chars), and does the truncation drop a
disambiguation clause ("use X not Y", "prefer X over Y", etc.)?

Run: .venv/Scripts/python.exe scripts/audit_tool_desc_truncation.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.tools.registry import ToolRegistry
from src.infrastructure.tools.selection.llm_selector import LLMToolSelector

DISAMBIGUATION_RE = re.compile(
    r"\b(use\s+\w+.{0,20}\bnot\b|prefer\b|instead of\b|not\s+\w+.{0,10}\bfor\b|rather than\b)",
    re.IGNORECASE,
)


def main() -> None:
    registry = ToolRegistry()
    registry.discover_all()

    rows = []
    for name in sorted(registry._tools.keys()):
        tool = registry.get_tool(name)
        desc = getattr(tool, "description", "") or ""
        desc = re.sub(r"\s+", " ", str(desc)).strip()
        if not desc:
            continue
        concise = LLMToolSelector._concise_description(desc)
        truncated = len(desc) > LLMToolSelector._DESC_CHAR_CAP
        if not truncated:
            continue
        dropped = desc[len(concise):].strip()
        dropped_has_disambig = bool(DISAMBIGUATION_RE.search(dropped))
        kept_has_disambig = bool(DISAMBIGUATION_RE.search(concise))
        rows.append((name, len(desc), len(concise), dropped_has_disambig, kept_has_disambig, dropped[:160]))

    print(f"{'tool':40s} {'raw':>5s} {'kept':>5s}  drop_disambig  kept_disambig  dropped_preview")
    for name, raw_len, kept_len, dropped_disambig, kept_disambig, preview in rows:
        flag = "*** " if dropped_disambig and not kept_disambig else "    "
        print(f"{flag}{name:36s} {raw_len:5d} {kept_len:5d}  {str(dropped_disambig):>13s}  {str(kept_disambig):>13s}  {preview}")

    print()
    print(f"Total tools: {len(registry._tools)}")
    print(f"Truncated (>{LLMToolSelector._DESC_CHAR_CAP} chars): {len(rows)}")
    at_risk = [r for r in rows if r[3] and not r[4]]
    print(f"AT RISK (disambiguation clause dropped, none survives truncation): {len(at_risk)}")
    for r in at_risk:
        print(f"  - {r[0]}")


if __name__ == "__main__":
    main()
