"""Regenerate the module-cohesion baseline.

    python scripts/dev/regen_cohesion_baseline.py

Rewrites ``tests/unit/module_cohesion_baseline.json`` from the current tree, using the
thresholds defined in ``tests/unit/test_module_cohesion.py``.

Run this after you SHRINK something oversized, to lock the improvement in. Running it
to silence a failure on code you just grew defeats the ratchet — the diff makes that
obvious to a reviewer, which is the point. The script prints what moved so the change
is legible in review.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

# `tests/` is not a package, so load the check module by path — this keeps the
# thresholds defined in exactly one place instead of drifting between the two files.
_spec = importlib.util.spec_from_file_location(
    "_module_cohesion", REPO_ROOT / "tests" / "unit" / "test_module_cohesion.py"
)
_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check)

BASELINE_PATH = _check.BASELINE_PATH
EXCLUDED_PARTS = _check.EXCLUDED_PARTS
MAX_CLASS_LINES = _check.MAX_CLASS_LINES
MAX_FUNCTION_LINES = _check.MAX_FUNCTION_LINES


def measure() -> dict:
    found: dict = {}
    for path in sorted(SRC.rglob("*.py")):
        if EXCLUDED_PARTS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            end = getattr(node, "end_lineno", None)
            if end is None:
                continue
            span = end - node.lineno + 1
            if isinstance(node, ast.ClassDef) and span > MAX_CLASS_LINES:
                found[f"class:{rel}:{node.name}"] = span
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and span > MAX_FUNCTION_LINES
            ):
                found[f"func:{rel}:{node.name}"] = span
    return dict(sorted(found.items(), key=lambda kv: (-kv[1], kv[0])))


def main() -> int:
    old = {}
    if BASELINE_PATH.exists():
        old = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    new = measure()

    for key in sorted(set(old) - set(new)):
        print(f"  resolved  {key} (was {old[key]})")
    for key in sorted(set(new) - set(old)):
        print(f"  ADDED     {key} ({new[key]})")
    for key in sorted(set(new) & set(old)):
        if new[key] != old[key]:
            verb = "improved" if new[key] < old[key] else "GREW"
            print(f"  {verb:9} {key}: {old[key]} -> {new[key]}")

    BASELINE_PATH.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} ({len(new)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
