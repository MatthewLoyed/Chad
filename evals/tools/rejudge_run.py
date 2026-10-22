"""Re-judge an existing workflow/live transcript dir with a new judge model.

Does NOT re-run Brain turns — prefers raw ``*.json`` sidecars under
``evals/runs/transcripts/run_<ts>/`` (full events/tool bodies), falling back to
parsing ``*.md`` for older runs. Used for the HITL quality loop when the first
judge pass failed (VRAM) or we swap judge models (e.g. Gemma-4-26b).

Usage:
  python -m evals.tools.rejudge_run --run 20260724_194302 \\
      --suite wedding_quality_hitl --judge-model google/gemma-4-26b-a4b-qat
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from evals.core import RUNS_DIR, _render_events_chronological, load_suite
from evals.runner import _run_judges


def _section(md: str, heading: str) -> str:
    """Return body text under ``## heading`` until the next ``## ``."""
    pat = re.compile(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.M | re.S)
    m = pat.search(md)
    return (m.group(1).strip() if m else "")


def _parse_transcript(md: str) -> Dict[str, Any]:
    prompt_block = _section(md, "Prompt")
    prompt = "\n".join(
        line.lstrip("> ").rstrip()
        for line in prompt_block.splitlines()
        if line.strip().startswith(">")
    ).strip() or prompt_block.strip()

    tools_block = _section(md, "Tools executed")
    tool_names: List[str] = []
    for line in tools_block.splitlines():
        m = re.match(r"^- `([^`]+)`", line.strip())
        if m:
            tool_names.append(m.group(1))

    tool_results = _section(md, "Tool results")
    if tool_results.startswith("```"):
        tool_results = re.sub(r"^```\w*\n?", "", tool_results)
        tool_results = re.sub(r"\n?```$", "", tool_results).strip()

    traj = _section(md, "Trajectory (chronological)")
    if traj and not tool_results:
        tool_results = traj

    response = _section(md, "Final response")
    response = re.split(r"\n## ", response, maxsplit=1)[0].strip()

    plan = None
    plan_block = _section(md, "Plan proposed")
    if plan_block:
        body = plan_block
        if body.startswith("```"):
            body = re.sub(r"^```\w*\n?", "", body)
            body = re.sub(r"\n?```$", "", body).strip()
        try:
            plan = json.loads(body)
        except json.JSONDecodeError:
            plan = [{"raw": body[:2000]}] if body else None

    completed = True
    for line in md.splitlines()[:25]:
        m = re.search(r"completed=(True|False|true|false)", line)
        if m:
            completed = m.group(1).lower() == "true"
            break

    return {
        "prompt": prompt,
        "tool_names": tool_names,
        "tool_results": tool_results,
        "response": response,
        "plan": plan,
        "completed": completed,
        "live": True,
    }


def _artifacts_from_raw_json(data: Dict[str, Any]) -> Dict[str, Any]:
    t = data.get("transcript") or {}
    events = t.get("events") or []
    if events:
        # Use the same chronological render as transcripts: coalesces legacy one-token
        # thinking dumps so Gemma doesn't OOM / return empty (HITL gmail rejudge).
        tool_results = "\n".join(_render_events_chronological(events))
    else:
        tool_results = t.get("tool_results") or ""
    return {
        "tool_names": t.get("tool_names") or [],
        "tool_results": tool_results,
        "response": t.get("response") or "",
        "plan": t.get("plan"),
        "completed": bool(t.get("completed", True)),
        "stop_reason": t.get("stop_reason") or data.get("metrics", {}).get("stop_reason"),
        "live": True,
        "events": events,
        "gather_brief": t.get("gather_brief") or "",
    }


def _patch_transcript_judge_section(md: str, judge_results: List[dict], judge_model: str) -> str:
    block_lines = [
        "## Judge verdicts",
        f"_Re-judged with `{judge_model}` (offline rejudge; Brain turn not re-run)._",
        "",
    ]
    for j in judge_results:
        score = j.get("score")
        score_s = "None" if score is None else str(score)
        if j.get("needs_rejudge"):
            flag = "NEEDS_REJUDGE"
        else:
            flag = "PASS" if j.get("passed") else "FAIL"
        block_lines.append(f"- **{j.get('rubric')}** [{flag}] score={score_s}")
        for reason in j.get("reasons") or []:
            block_lines.append(f"    - {reason}")
        diag = j.get("diag") or {}
        if diag.get("likely_cause"):
            block_lines.append(
                f"    - likely_cause=`{diag['likely_cause']}` "
                f"(user_chars={diag.get('user_chars')}, num_ctx={diag.get('num_ctx')})"
            )
    block = "\n".join(block_lines) + "\n"

    if re.search(r"^## Judge verdicts\s*$", md, re.M):
        return re.sub(
            r"^## Judge verdicts\s*\n.*?(?=^## |\Z)",
            block + "\n",
            md,
            count=1,
            flags=re.M | re.S,
        )
    if re.search(r"^## Metrics\s*$", md, re.M):
        return re.sub(r"(^## Metrics\s*$)", block + r"\n\1", md, count=1, flags=re.M)
    return md.rstrip() + "\n\n" + block


async def _amain(args: argparse.Namespace) -> int:
    run_ts = args.run
    tdir = RUNS_DIR / "transcripts" / f"run_{run_ts}"
    if not tdir.is_dir():
        print(f"[rejudge] missing transcripts dir: {tdir}")
        return 2

    cases = {c.id: c for c in load_suite(args.suite)}
    report_lines = [
        f"# Rejudge — run_{run_ts}",
        f"Judge model: `{args.judge_model}`",
        f"Suite: `{args.suite}`",
        "",
    ]
    updated_cases: List[dict] = []

    md_files = sorted(tdir.glob("*.md"))
    if not md_files:
        print(f"[rejudge] no markdown transcripts in {tdir}")
        return 2

    only = set(args.only or [])
    for path in md_files:
        case_id = path.stem
        if only and case_id not in only:
            continue
        case = cases.get(case_id)
        if not case:
            print(f"[rejudge] skip {case_id}: not in suite {args.suite}")
            continue
        raw_path = tdir / f"{case_id}.json"
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            arts = _artifacts_from_raw_json(raw)
            source = "json"
        else:
            md = path.read_text(encoding="utf-8")
            arts = _parse_transcript(md)
            arts.pop("prompt", None)
            source = "md"
        print(
            f"[rejudge] {case_id}: source={source} rubrics={case.judge} "
            f"tools={len(arts.get('tool_names') or [])} "
            f"resp_chars={len(arts.get('response') or '')} "
            f"results_chars={len(arts.get('tool_results') or '')}",
            flush=True,
        )
        judge_results = await _run_judges(case, arts, args.judge_model)
        md = path.read_text(encoding="utf-8")
        path.write_text(
            _patch_transcript_judge_section(md, judge_results, args.judge_model),
            encoding="utf-8",
        )
        if raw_path.is_file():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["judge_results"] = judge_results
            raw_path.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        updated_cases.append({"id": case_id, "judge_results": judge_results})
        report_lines.append(f"## {case_id}")
        for j in judge_results:
            if j.get("needs_rejudge"):
                flag = "NEEDS_REJUDGE"
            else:
                flag = "PASS" if j.get("passed") else "FAIL"
            report_lines.append(f"- **{j.get('rubric')}** [{flag}] score={j.get('score')}/10")
            for reason in j.get("reasons") or []:
                report_lines.append(f"  - {reason}")
            diag = j.get("diag") or {}
            if diag.get("likely_cause"):
                report_lines.append(
                    f"  - likely_cause=`{diag['likely_cause']}` "
                    f"(user_chars={diag.get('user_chars')}, num_ctx={diag.get('num_ctx')})"
                )
        report_lines.append("")

    run_json = RUNS_DIR / f"run_{run_ts}.json"
    if run_json.is_file():
        data = json.loads(run_json.read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in updated_cases}
        for c in data.get("cases") or []:
            if c.get("id") not in by_id:
                continue
            c["judge_results"] = by_id[c["id"]]["judge_results"]
            if any(j.get("needs_rejudge") for j in c["judge_results"]):
                c["passed"] = False
                c["outcome"] = "void_harness"
                c["void_reason"] = "judge_needs_rejudge"
                continue
            scored = [j for j in c["judge_results"] if j.get("score") is not None]
            if not scored:
                continue
            if any(not j.get("passed") for j in scored):
                c["passed"] = False
                c["outcome"] = "fail"
                c["void_reason"] = ""
            elif c.get("outcome") == "void_harness" and c.get("void_reason") == "judge_needs_rejudge":
                # Cleared by a successful rejudge — don't invent a pass if asserts already failed.
                c["void_reason"] = ""
                if c.get("assertion_failures"):
                    c["passed"] = False
                    c["outcome"] = "fail"
                else:
                    c["passed"] = True
                    c["outcome"] = "pass"
        data.setdefault("manifest", {})["judge_model"] = args.judge_model
        data["judged"] = True
        scored_cases = [
            c for c in data["cases"]
            if c.get("outcome") not in ("void_infra", "void_harness")
        ]
        n_pass = sum(1 for c in scored_cases if c.get("passed"))
        data["summary"]["passed"] = n_pass
        data["summary"]["failed"] = len(scored_cases) - n_pass
        data["summary"]["pass_rate"] = (
            round(n_pass / len(scored_cases), 3) if scored_cases else 0.0
        )
        run_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[rejudge] updated {run_json}")

    out = RUNS_DIR / "review_queue" / f"judge_{run_ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"[rejudge] wrote {out}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run timestamp, e.g. 20260724_194302")
    ap.add_argument("--suite", required=True, help="dataset suite name")
    ap.add_argument("--judge-model", default="google/gemma-4-26b-a4b-qat")
    ap.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="optional case id(s) to rejudge (default: all transcripts in the run)",
    )
    raise SystemExit(asyncio.run(_amain(ap.parse_args())))


if __name__ == "__main__":
    main()
