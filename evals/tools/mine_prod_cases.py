r"""
mine_prod_cases.py — turn real historical prod turns into candidate eval cases.

Refs #305/#314. Reads `tool_outcome_log` (populated by every real tool call via
`src/infrastructure/tools/event_wrapper.py` — see `query_text` == the raw user
prompt for that turn, `turn_id` groups every tool call within one turn), groups
by `turn_id` to recover "prompt -> tools actually executed", and emits candidate
`.jsonl` lines in the `evals.core.Case` schema.

PRIVACY: this reads real historical prompts, which may contain personal names,
emails, health info, etc. (see docs/guides/PRIVATE_DATA.md). This script:
  1. Only ever writes to the gitignored staging path (`evals/datasets/_staging/`)
     — never emits directly into a suite `runner.py` loads or a tracked file.
  2. Applies a best-effort automatic redaction pass (emails, phone-like digit
     runs) before writing candidates to disk.
  3. NEVER prints prompt text to stdout — only aggregate counts (tool-name
     frequency, category guesses) so running this script does not itself leak
     personal content into a terminal/session transcript.
  4. Candidates in the staging file still REQUIRE human review before any case
     is hand-copied (and further genericized) into a committed dataset such as
     `evals/datasets/prod_sampled.jsonl` — a historical turn may have used the
     wrong tool (a bug), which would otherwise get enshrined as a "correct"
     regression expectation.

Usage:
  $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.tools.mine_prod_cases --sample-n 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

STAGING_DIR = Path(__file__).resolve().parents[1] / "datasets" / "_staging"
STAGING_FILE = STAGING_DIR / "prod_mined.jsonl"

# Conservative auto-redaction — catches the common structured-PII shapes.
# Does NOT attempt personal-name detection (too unreliable to trust
# unsupervised); that gap is exactly why human review is mandatory before
# any candidate is promoted to a committed dataset.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_LONG_DIGIT_RE = re.compile(r"\b\d{6,}\b")  # account/order/SSN-like runs

# Prompts this short/generic add no coverage over existing hand-authored
# smoke cases ("hi", "thanks") — skip them so mined cases add real signal.
_MIN_PROMPT_CHARS = 12


def _looks_like_pii(text: str) -> bool:
    """Checked against the RAW prompt, before any redaction. Emails and
    phone numbers are high-confidence structured PII with real names/contact
    info nearby in the same sentence — safer to drop the whole turn than to
    trust a regex substitution to have caught everything adjacent to it."""
    return bool(_EMAIL_RE.search(text) or _PHONE_RE.search(text))


def _redact(text: str) -> str:
    """Defense-in-depth pass for turns that already cleared `_looks_like_pii`
    (so no email/phone present) — masks lower-sensitivity digit runs
    (order/account numbers) that don't warrant dropping the whole turn."""
    return _LONG_DIGIT_RE.sub("[number]", text)


async def _fetch_turns(limit: int) -> Dict[str, Dict[str, Any]]:
    """Group tool_outcome_log rows by turn_id -> {prompt, tools: set}."""
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    supabase = get_supabase_manager()
    if not supabase or not supabase.enabled:
        raise RuntimeError(
            "Supabase not configured (SUPABASE_URL/SUPABASE_ANON_KEY missing) — "
            "this script requires the same live credentials the running Brain uses."
        )

    rows = await supabase.async_query(
        "tool_outcome_log",
        select="turn_id,tool_name,query_text,outcome,created_at",
        filters={"outcome": "eq.success"},
        order="created_at.desc",
        limit=limit,
    )

    turns: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        turn_id = row.get("turn_id")
        query_text = row.get("query_text")
        tool_name = row.get("tool_name")
        if not turn_id or not query_text or not tool_name:
            continue
        entry = turns.setdefault(turn_id, {"prompt": query_text, "tools": set()})
        entry["tools"].add(tool_name)
    return turns


def _to_case(turn_id: str, prompt: str, tools: List[str]) -> Dict[str, Any]:
    return {
        "id": f"prod_mined_{turn_id[:8]}",
        "prompt": prompt,
        "category": "agent",
        "expect": {"tools_any": sorted(tools), "max_tools": len(tools) + 8},
        "judge": ["tool_appropriateness"],
        "note": (
            "AUTO-MINED FROM PROD (tool_outcome_log) — unreviewed. Before promoting "
            "to a committed suite: verify these were actually the RIGHT tools for "
            "this prompt (a historical bug should not become an enforced "
            "expectation), and hand-scrub any remaining personal specifics beyond "
            "the automatic email/phone/digit-run redaction already applied."
        ),
    }


async def mine(sample_n: int, fetch_limit: int) -> None:
    turns = await _fetch_turns(fetch_limit)

    eligible: List[Dict[str, Any]] = []
    dropped_pii = 0
    dropped_short = 0
    for turn_id, entry in turns.items():
        raw_prompt = entry["prompt"]
        if len(raw_prompt) < _MIN_PROMPT_CHARS:
            dropped_short += 1
            continue
        if _looks_like_pii(raw_prompt):
            dropped_pii += 1
            continue
        prompt = _redact(raw_prompt)
        eligible.append({"turn_id": turn_id, "prompt": prompt, "tools": sorted(entry["tools"])})

    random.shuffle(eligible)
    sample = eligible[:sample_n]

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    gitignore = STAGING_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    with STAGING_FILE.open("w", encoding="utf-8") as f:
        f.write(
            "// prod_mined.jsonl — AUTO-GENERATED staging candidates, gitignored. "
            "Review each case, then hand-copy (further genericized) into "
            "evals/datasets/prod_sampled.jsonl. See evals/REFERENCE.md 'Mining "
            "real-usage cases'.\n"
        )
        for item in sample:
            case = _to_case(item["turn_id"], item["prompt"], item["tools"])
            f.write(json.dumps(case) + "\n")

    tool_freq = Counter(t for item in sample for t in item["tools"])

    # Aggregate-only report — never print prompt text (see module docstring).
    print(f"[mine_prod_cases] fetched {len(turns)} distinct turns from tool_outcome_log")
    print(f"[mine_prod_cases] dropped {dropped_short} (too short) + {dropped_pii} (PII match) turns")
    print(f"[mine_prod_cases] wrote {len(sample)} candidates -> {STAGING_FILE}")
    print("[mine_prod_cases] tool frequency in sample:")
    for tool, count in tool_freq.most_common(20):
        print(f"    {tool}: {count}")
    print(
        "\n[mine_prod_cases] NEXT STEP: open the staging file yourself and review each "
        "candidate — this script never prints prompt text so it cannot leak personal "
        "content into a terminal/session transcript."
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine real prod turns into staged candidate eval cases")
    parser.add_argument("--sample-n", type=int, default=30, help="How many candidates to sample (default 30)")
    parser.add_argument(
        "--fetch-limit", type=int, default=1000,
        help="How many tool_outcome_log rows to scan before grouping/sampling (default 1000)",
    )
    args = parser.parse_args(argv)
    asyncio.run(mine(args.sample_n, args.fetch_limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
