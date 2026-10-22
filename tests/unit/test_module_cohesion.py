"""Ratcheting guard against god classes and god functions.

This encodes the module-size convention from ``CLAUDE.md.example``. The thing that
makes code hard to change safely is not a file's length — it's how much you have to
hold in your head at once. So this measures the two things that actually track that:

- the **largest class** in a file (a class is shared mutable state; every method can
  touch every field, so its size is the size of what you must reason about), and
- the **longest function** (a linear span you must read start-to-finish).

It deliberately does NOT measure file length. ``src/remote/endpoints.py`` is ~1100
lines of ~42 small, independent route handlers — adding a 43rd doesn't make the other
42 harder to understand, and splitting it into four files of ten routes would add
imports and indirection while reducing nothing. That file passes here, by design. The
old ~800/1000-line rule flagged it and ``turn_coordinator`` identically, which is why
it got ignored.

**Ratchet, not a cliff.** The codebase has existing debt, frozen in
``module_cohesion_baseline.json`` (95 entries at the time of writing). The suite fails
when a NEW violation appears, or when an existing one gets WORSE. It never fails for
improvement — but improvements must be re-baselined, so ground gained can't be lost
again. Regenerate with::

    python scripts/dev/regen_cohesion_baseline.py

The thresholds below are deliberately honest rather than flattering: raising them until
the baseline looks short would defeat the point. The baseline lives in a data file
precisely so the limits don't have to be negotiated down to keep this file readable.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Dict, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
BASELINE_PATH = pathlib.Path(__file__).with_name("module_cohesion_baseline.json")

# A class beyond this is holding too much shared state to reason about at once.
MAX_CLASS_LINES = 400
# A function beyond this can't be read in one sitting without scrolling back.
MAX_FUNCTION_LINES = 120

# Vendored/generated trees we don't own the shape of.
EXCLUDED_PARTS = {"skills", "node_modules", "__pycache__"}

BASELINE: Dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _measure() -> List[Tuple[str, int]]:
    """``(key, span)`` for every class and function in ``src`` that exceeds its limit."""
    found: List[Tuple[str, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        if EXCLUDED_PARTS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = _rel(path)
        for node in ast.walk(tree):
            end = getattr(node, "end_lineno", None)
            if end is None:
                continue
            span = end - node.lineno + 1
            if isinstance(node, ast.ClassDef):
                if span > MAX_CLASS_LINES:
                    found.append((f"class:{rel}:{node.name}", span))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if span > MAX_FUNCTION_LINES:
                    found.append((f"func:{rel}:{node.name}", span))
    return found


def test_no_new_god_classes_or_functions():
    """New oversized classes/functions fail; known debt is allowed at its frozen size."""
    violations = _measure()

    new_offenders = [(k, n) for k, n in violations if k not in BASELINE]
    regressions = [
        (k, n, BASELINE[k]) for k, n in violations if k in BASELINE and n > BASELINE[k]
    ]

    messages: List[str] = []
    if new_offenders:
        messages.append(
            "New oversized code (limits: class > {c} lines, function > {f} lines).\n"
            "Prefer splitting it. If it is genuinely long-and-flat — a route table,\n"
            "registry, formatter map, or an adapter mirroring an external API — then\n"
            "accept it via `python scripts/dev/regen_cohesion_baseline.py` and say which\n"
            "in the commit message.\n".format(
                c=MAX_CLASS_LINES, f=MAX_FUNCTION_LINES
            )
            + "\n".join(f"    {n:5} lines  {k}" for k, n in sorted(new_offenders, key=lambda x: -x[1]))
        )
    if regressions:
        messages.append(
            "Existing oversized code grew. Don't raise the baseline — this is the\n"
            "ratchet doing its job.\n"
            + "\n".join(
                f"    {k}: {n} lines, baseline allows {allowed}"
                for k, n, allowed in sorted(regressions, key=lambda x: -x[1])
            )
        )

    assert not messages, "\n\n".join(messages)


def test_baseline_has_no_stale_entries():
    """A baseline entry that no longer exists (or no longer exceeds its limit) must be
    removed, so the list keeps reflecting real debt rather than accumulating fiction."""
    current = {k for k, _ in _measure()}
    stale = sorted(set(BASELINE) - current)
    assert not stale, (
        "BASELINE lists entries that are no longer oversized (or no longer exist).\n"
        "Remove them — they were fixed or renamed:\n"
        + "\n".join(f"    {k}" for k in stale)
    )
