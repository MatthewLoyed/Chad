r"""mine_coder_tasks.py — draft candidate coder_bench tasks from this repo's own git history.

Refs #514. Scans `git log` for commits that touch both `tests/` and `src/` in the same commit
(a bug-fix-plus-regression-test shape) and drafts candidate `evals.coder_bench.CoderTask` lines
to a gitignored staging file. Mirrors `evals/tools/mine_prod_cases.py`'s shape: mine broadly,
write only to staging, require a human review pass before promotion to a committed dataset.

⚠️ CONTAMINATION WARNING (read before promoting anything): the candidate `prompt` this script
drafts is the FIX commit's message — which describes the SOLUTION, not the bug. Handing that to
code_agent as-is would leak the answer and make the eval meaningless. Before promoting a
candidate into `evals/datasets/coder_tasks.jsonl` you MUST replace `prompt` with the actual
PRE-FIX issue description (`gh issue view <issue_ref>` — the bug report, not the fix), the same
way real SWE-bench tasks use the GitHub issue text, never the merged PR's own commit message.

Usage:
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tools.mine_coder_tasks --limit 200
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGING_DIR = Path(__file__).resolve().parents[1] / "datasets" / "_staging"
STAGING_FILE = STAGING_DIR / "coder_mined.jsonl"

_ISSUE_REF_RE = re.compile(r"#(\d+)")
_SEP = "\x1f"  # unit separator — won't collide with commit message text


def _git(args: List[str]) -> str:
    r = subprocess.run(["git", *args], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    return r.stdout


def _log_entries(limit: int) -> List[Dict[str, str]]:
    """One entry per commit: sha, parent, subject. `git log` order = newest first."""
    fmt = f"%H{_SEP}%P{_SEP}%s"
    raw = _git(["log", f"-n{limit}", f"--pretty=format:{fmt}"])
    entries = []
    for line in raw.splitlines():
        parts = line.split(_SEP)
        if len(parts) != 3:
            continue
        sha, parents, subject = parts
        parent = parents.split(" ")[0] if parents else ""
        if not parent:
            continue  # skip root commit — no meaningful base_commit
        entries.append({"sha": sha, "parent": parent, "subject": subject})
    return entries


def _changed_files(sha: str) -> List[str]:
    raw = _git(["show", "--name-only", "--pretty=format:", sha])
    return [f.strip() for f in raw.splitlines() if f.strip()]


def _issue_ref(subject: str) -> Optional[str]:
    m = _ISSUE_REF_RE.search(subject)
    return f"#{m.group(1)}" if m else None


def _to_task(entry: Dict[str, str], test_files: List[str], src_files: List[str]) -> Dict[str, Any]:
    issue_ref = _issue_ref(entry["subject"]) or ""
    slug = re.sub(r"[^a-z0-9]+", "_", entry["subject"].lower()).strip("_")[:40]
    return {
        "id": f"mined_{entry['sha'][:8]}_{slug}",
        "issue_ref": issue_ref,
        "base_commit": entry["parent"],
        # PLACEHOLDER — see module docstring's contamination warning. This is the FIX commit's
        # own message, which leaks the solution. MUST be replaced with the real pre-fix issue
        # text before this candidate is promoted anywhere.
        "prompt": f"[REPLACE WITH REAL PRE-FIX ISSUE TEXT — see {issue_ref or 'commit ' + entry['sha'][:8]}] "
                  f"{entry['subject']}",
        "fail_to_pass": [f"pytest {f}" for f in test_files],
        "pass_to_pass": [],  # TODO human: pick a broader regression subset (e.g. the same test module minus the new test)
        "timeout_s": 300,
        "_mined_from_commit": entry["sha"],
        "_changed_src_files": src_files,
    }


def mine(limit: int) -> None:
    entries = _log_entries(limit)
    candidates: List[Dict[str, Any]] = []
    for entry in entries:
        files = _changed_files(entry["sha"])
        test_files = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
        src_files = [f for f in files if f.startswith("src/") and f.endswith(".py")]
        if not test_files or not src_files:
            continue
        candidates.append(_to_task(entry, test_files, src_files))

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    gitignore = STAGING_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    with STAGING_FILE.open("w", encoding="utf-8") as f:
        f.write(
            "// coder_mined.jsonl — AUTO-GENERATED staging candidates, gitignored. Every "
            "`prompt` is a PLACEHOLDER (the fix commit's own message) and MUST be replaced "
            "with the real pre-fix issue text before promotion. See evals/REFERENCE.md 'Coder "
            "harness bench' and the contamination warning in this script's docstring.\n"
        )
        for c in candidates:
            f.write(json.dumps(c) + "\n")

    print(f"[mine_coder_tasks] scanned {len(entries)} commits")
    print(f"[mine_coder_tasks] found {len(candidates)} candidates (touch both tests/ and src/) -> {STAGING_FILE}")
    print(
        "\n[mine_coder_tasks] NEXT STEPS before promoting any candidate to "
        "evals/datasets/coder_tasks.jsonl:\n"
        "  1. Open the staging file and pick real, well-scoped candidates.\n"
        "  2. Replace `prompt` with the ACTUAL pre-fix issue text (`gh issue view <issue_ref>`) —\n"
        "     never the fix commit's own message, which leaks the solution.\n"
        "  3. Check out `base_commit` and CONFIRM fail_to_pass tests actually fail there, and\n"
        "     pass after the real fix commit — a candidate that doesn't reproduce is worthless.\n"
        "  4. Fill in `pass_to_pass` with a real regression subset (not left empty).\n"
        "  5. Drop the `_mined_from_commit`/`_changed_src_files` scratch keys before committing."
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mine this repo's git history into candidate coder_bench tasks")
    parser.add_argument("--limit", type=int, default=200, help="how many recent commits to scan (default 200)")
    args = parser.parse_args(argv)
    mine(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
