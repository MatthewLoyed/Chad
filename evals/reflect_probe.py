r"""
reflect_probe.py — validate-first harness for Reflective Lessons.

Before we let Chad write durable lessons into his own persona (see
docs/DESIGN_reflective-lessons.md), we test the ONE thing the whole loop hinges
on: can the judge read a stretch of real conversation history and propose GOOD,
generalizable lessons? This script does exactly that and NOTHING else — it is
strictly read-only. No persona writes, no DB mutations. You eyeball the output.

What it does:
  1. Pull the last N real conversations (messages table) + recent tool outcomes.
  2. Format them into a compact transcript log.
  3. Ask a judge model (default a 16B local — lessons are high-stakes) to spot
     recurring friction / inefficiency / user preferences and propose candidate
     lessons, each routed to a scope:
        - user  → a durable fact/preference about the USER
        - tools → an operational/procedural lesson ("for B prefer A"; environment facts)
     The judge is told to ABSTAIN by default — most logs yield few or no lessons.
  4. Print the candidates (scope, lesson, evidence, confidence) for human review.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.reflect_probe
    ...python.exe -m evals.reflect_probe --convos 15 --model "qwen/qwen3-14b"

This is step 1 of the build order: prove the judge before wiring any writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The reflection core (log shaping + judge rubric + reflect()) now lives in the
# runtime module so there is ONE source of truth; this probe is its read-only
# eval entry point — it proposes lessons and prints them, and writes NOTHING.
from src.core.intelligence.reflection import reflect

RUNS_DIR = PROJECT_ROOT / "evals" / "runs"

# A lesson that persists and compounds is higher-stakes than an eval pass/fail,
# so default to a beefier judge than the eval lanes use. Override with --model.
DEFAULT_JUDGE = "qwen/qwen3-14b"


async def run_probe(convos: int, model: str) -> Dict[str, Any]:
    """Thin read-only wrapper over the runtime reflection core (proposes, prints, writes nothing)."""
    r = await reflect(convos, model)
    if r.error:
        return {"error": r.error, "log_chars": 0, "lessons": []}
    return {"log_chars": r.log_chars, "model": r.model, "lessons": r.lessons,
            "questions": r.questions, "empty_output": r.empty_output, "raw": r.raw}


def render(result: Dict[str, Any]) -> str:
    out = ["# Reflective Lessons — judge validation probe\n",
           "_Read-only. Candidate lessons the judge proposed from real history. "
           "Nothing was written. Judge them: are these lessons you'd want Chad to keep?_\n"]
    if result.get("error"):
        out.append(f"⚠️ {result['error']}")
        return "\n".join(out)
    out.append(f"- judge model: `{result.get('model')}` | log size: {result['log_chars']} chars")
    lessons = result.get("lessons", [])
    questions = result.get("questions", [])
    out.append(f"- candidate lessons: **{len(lessons)}** | open questions for the user: **{len(questions)}**\n")
    if not lessons and not questions:
        if result.get("empty_output"):
            out.append("⚠️ Judge returned EMPTY output (infrastructure/thinking-budget issue, "
                       "NOT a genuine abstention) — even after the roomier retry. Try a different "
                       "judge model or a smaller log window.")
        else:
            out.append("Nothing cleared the bar (this is a valid, expected outcome).")
        if result.get("raw"):
            out.append(f"\n<details><summary>raw judge output</summary>\n\n```\n{result['raw']}\n```\n</details>")
        return "\n".join(out)
    if lessons:
        out.append("### Candidate lessons (grounded facts)\n")
        out.append("| # | scope | confidence | lesson | evidence |")
        out.append("|---|---|---|---|---|")
        for i, l in enumerate(lessons, 1):
            out.append(f"| {i} | `{l.get('scope','?')}` | {l.get('confidence','?')} | "
                       f"{l.get('lesson','')} | {str(l.get('evidence',''))[:120]} |")
        out.append("")
    if questions:
        out.append("### Open questions for the user (active learning — answers become lessons)\n")
        out.append("| # | confidence | question | why |")
        out.append("|---|---|---|---|")
        for i, q in enumerate(questions, 1):
            out.append(f"| {i} | {q.get('confidence','?')} | {q.get('question','')} | "
                       f"{str(q.get('why',''))[:120]} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Validate the reflection judge on real logs (read-only).")
    ap.add_argument("--convos", type=int, default=12, help="how many recent conversations to feed (default 12)")
    ap.add_argument("--model", default=DEFAULT_JUDGE,
                    help=f"LM Studio judge model id (default {DEFAULT_JUDGE} — lessons are high-stakes, "
                         "use a capable loaded model)")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"[reflect] probing judge '{args.model}' on last {args.convos} conversations (read-only)…")
    result = asyncio.run(run_probe(args.convos, args.model))
    report = render(result)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    (RUNS_DIR / f"reflect_probe_{ts}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (RUNS_DIR / "reflect_probe_latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\n[reflect] saved: evals/runs/reflect_probe_{ts}.json | report: reflect_probe_latest.md")


if __name__ == "__main__":
    main()
