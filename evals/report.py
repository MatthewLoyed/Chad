"""
report.py — scoreboard + cross-run failure clustering over ALL run history.

Per-run `triage.md` answers "what failed THIS run". This answers "what fails MOST,
across every run so far" — the artifact you read after an unattended overnight loop:
the biggest failure points, clustered, with per-case stability.

    python -m evals.report                # aggregate every evals/runs/run_*.json → scoreboard.md
    python -m evals.report --since 50     # only the last 50 runs

Reads run JSONs only (no model calls). Reuses the triage signature→area map.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Windows consoles default to cp1252 — force UTF-8 so report glyphs (→, 🎉) don't crash printing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.core import RUNS_DIR
from evals.triage import _classify


def _load_runs(since: int = 0):
    files = sorted(RUNS_DIR.glob("run_*.json"), key=lambda p: p.name)
    if since:
        files = files[-since:]
    runs = []
    for f in files:
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return runs


def _case_failure_signals(case: dict):
    """Failure strings for one case-run (errors, assertions, failed judges)."""
    sig = []
    if case.get("error"):
        sig.append(f"error: {case['error']}")
    sig.extend(case.get("assertion_failures") or [])
    for j in case.get("judge_results") or []:
        if not j.get("passed", True):
            reasons = "; ".join(j.get("reasons", [])) or "(no reason)"
            sig.append(f"judge[{j.get('rubric')}] {reasons}")
    return sig


def build_scoreboard(runs) -> str:
    if not runs:
        return "# HARNESS SCOREBOARD\n\nNo runs found in evals/runs/. Run the suite first."

    # Per-case aggregation across all runs.
    attempts = defaultdict(int)
    passes = defaultdict(int)
    case_prompt = {}
    case_lane = {}
    # Cluster aggregation across all runs.
    cluster_count = defaultdict(int)          # key -> failed case-runs
    cluster_meta = {}                          # key -> (label, area, files)
    cluster_cases = defaultdict(set)           # key -> case_ids
    # Judge score aggregation.
    judge_scores = defaultdict(list)

    total_caseruns = 0
    void_caseruns = 0
    void_by_reason = defaultdict(set)
    for run in runs:
        for c in run.get("cases", []):
            cid = c["id"]
            case_lane[cid] = run.get("lane", "?")
            # A VOID case-run is not an attempt. Counting it as one would drag the case's
            # stability percentage down for reasons that have nothing to do with the model
            # — the exact misattribution the outcome taxonomy exists to prevent.
            if str(c.get("outcome", "")).startswith("void"):
                void_caseruns += 1
                void_by_reason[c.get("void_reason") or "unknown"].add(cid)
                continue
            attempts[cid] += 1
            total_caseruns += 1
            if c.get("passed"):
                passes[cid] += 1
            for j in c.get("judge_results") or []:
                if isinstance(j.get("score"), (int, float)):
                    judge_scores[j.get("rubric")].append(j["score"])
            for sig in _case_failure_signals(c):
                if sig.startswith("error:"):
                    key, label, area, files = ("infra", "Infrastructure error / crash",
                                               "harness/server/probe", "server, WS probe, model availability")
                else:
                    key, label, area, files = _classify(sig)
                cluster_count[key] += 1
                cluster_cases[key].add(cid)
                cluster_meta[key] = (label, area, files)

    first_ts = runs[0].get("timestamp", "?")
    last_ts = runs[-1].get("timestamp", "?")
    suites = sorted({r.get("suite", "?") for r in runs})

    L = [
        "# HARNESS SCOREBOARD",
        f"Runs aggregated : {len(runs)}  ({first_ts} → {last_ts})",
        f"Suites          : {', '.join(suites)}",
        f"Scored case-runs: {total_caseruns}",
    ]
    if void_caseruns:
        L += [
            f"VOID case-runs  : {void_caseruns} (excluded — environment/harness broke, the "
            f"model was not fairly tested)",
            "",
            "### Voided by cause — fix these before reading anything below",
            "",
        ]
        for reason, cids in sorted(void_by_reason.items(), key=lambda kv: -len(kv[1])):
            L.append(f"- **{reason}** — {len(cids)} case(s): {', '.join(sorted(cids))}")
    L += [
        "",
        "## Biggest failure points (clustered across ALL runs)",
        "",
    ]
    if cluster_count:
        for key in sorted(cluster_count, key=lambda k: cluster_count[k], reverse=True):
            label, area, files = cluster_meta[key]
            cases = ", ".join(sorted(cluster_cases[key]))
            L.append(f"### {label} — {cluster_count[key]} failed case-runs")
            L.append(f"- **Area:** {area}")
            L.append(f"- **Where to look:** {files}")
            L.append(f"- **Cases:** {cases}")
            L.append("")
    else:
        L.append("None — every case-run passed across all runs. 🎉\n")

    # Per-case stability, worst first.
    L += ["## Per-case stability (worst pass-rate first)", "",
          "| Case | Lane | Pass-rate | Runs | Flaky? |", "|---|---|---|---|---|"]
    rows = []
    for cid in attempts:
        n, p = attempts[cid], passes[cid]
        rate = p / n if n else 0.0
        flaky = "FLAKY" if 0 < p < n else ("fail" if p == 0 else "")
        rows.append((rate, cid, case_lane.get(cid, "?"), p, n, flaky))
    for rate, cid, lane, p, n, flaky in sorted(rows):
        L.append(f"| {cid} | {lane} | {rate:.0%} | {p}/{n} | {flaky} |")
    L.append("")

    # Judge means.
    if judge_scores:
        L += ["## Judge rubric means (across all runs)", "",
              "| Rubric | Mean score | Samples |", "|---|---|---|"]
        for rubric in sorted(judge_scores):
            vals = judge_scores[rubric]
            L.append(f"| {rubric} | {sum(vals)/len(vals):.2f} | {len(vals)} |")
        L.append("")

    L += ["## How to read this",
          "- **Biggest failure points** = where to invest. A cluster with many failed case-runs "
          "and consistently-failing cases is a real bug; lots of FLAKY cases = non-determinism.",
          "- Open `runs/transcripts/` for any case to see its trajectory; `runs/triage.md` for the latest run."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Aggregate eval run history into a scoreboard")
    ap.add_argument("--since", type=int, default=0, metavar="N", help="only the last N runs")
    args = ap.parse_args()
    runs = _load_runs(args.since)
    board = build_scoreboard(runs)
    out = RUNS_DIR / "scoreboard.md"
    out.write_text(board, encoding="utf-8")
    print(board)
    print(f"\n[evals] scoreboard → {out}")


if __name__ == "__main__":
    main()
