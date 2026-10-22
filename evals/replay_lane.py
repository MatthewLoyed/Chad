r"""
replay_lane.py — does OATS actually make tool SELECTION better over time?

OATS (Outcome-Aware Tool Selection) rewrites tool embeddings every 20 logged
outcomes (scripts/oats_refine.py): it pulls a tool's vector toward queries that
SUCCEEDED with it and away from queries that FAILED, then the RAG RPC ranks on
`COALESCE(embedding_refined, embedding)`. That loop runs in production with NO
measurement. This lane is the measurement.

What it does (offline, free, no server, no model tokens, no DB writes):
  1. Pull every tool's base `embedding` + the logged outcomes in `tool_outcome_log`
     (each row already carries the query_embedding that drove selection).
  2. K-fold over each tool's outcomes. For each fold, recompute that tool's
     refined vector from the TRAIN rows ONLY (reusing the exact OATS formula),
     then score the HELD-OUT rows: where does the tool rank for its query under
     base vs. refined embeddings?
  3. Report rank + recall@k, base vs. refined, split by success/failure rows.

Why holdout: OATS trains on the very query embeddings in `tool_outcome_log`, so
measuring on those same rows is in-sample and flatters OATS. Holdout measures
whether the refinement GENERALIZES to the next similar query — the thing we
actually care about. This is the guard the self-improvement literature insists on
(popular != correct; beware self-reinforcing drift).

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.replay_lane
    ...\python.exe -m evals.replay_lane --k 10 --folds 5 --min-outcomes 8
Or via the runner:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m evals.runner --replay

Verdict gate (exit code): refined must not REGRESS success-row mean rank.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNS_DIR = PROJECT_ROOT / "evals" / "runs"

# OATS formula constants — keep in lockstep with scripts/oats_refine.py.
ALPHA = 0.3


def _as_vec(raw) -> Optional[np.ndarray]:
    """pgvector comes back from PostgREST as a JSON list OR a '[...]' string."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 768:
        return None
    arr = np.asarray(raw, dtype=np.float32)
    if not np.isfinite(arr).all():
        return None
    return arr


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


@dataclass
class OutcomeRow:
    query_emb: np.ndarray
    outcome: str            # 'success' | 'failure'
    user_rating: Optional[str]  # 'positive' | 'negative' | None

    @property
    def is_positive(self) -> bool:
        """A row the tool SHOULD rank highly for: succeeded, or thumbs-up."""
        if self.user_rating == "positive":
            return True
        if self.user_rating == "negative":
            return False
        return self.outcome == "success"

    @property
    def is_negative(self) -> bool:
        if self.user_rating == "negative":
            return True
        if self.user_rating == "positive":
            return False
        return self.outcome == "failure"


def _refine_vector(base: np.ndarray, train_rows: List[OutcomeRow], alpha: float) -> np.ndarray:
    """Reimplements scripts/oats_refine.py's attraction/repulsion on a train split."""
    succ, succ_w, fail, fail_w = [], [], [], []
    for r in train_rows:
        if r.user_rating == "negative":
            fail.append(r.query_emb); fail_w.append(1.0)
        elif r.user_rating == "positive":
            succ.append(r.query_emb); succ_w.append(1.0)
        elif r.outcome == "success":
            succ.append(r.query_emb); succ_w.append(alpha)
        elif r.outcome == "failure":
            fail.append(r.query_emb); fail_w.append(0.5 * alpha)

    v = (1.0 - alpha) * base
    succ_coef = 1.0 if any(w == 1.0 for w in succ_w) else alpha
    fail_coef = 1.0 if any(w == 1.0 for w in fail_w) else (0.5 * alpha)
    if succ:
        v = v + succ_coef * np.average(succ, axis=0, weights=succ_w)
    if fail:
        v = v - fail_coef * np.average(fail, axis=0, weights=fail_w)
    return _l2(v)


def _rank_of(tool_vec: np.ndarray, catalog: np.ndarray, query: np.ndarray) -> int:
    """1-based rank of `tool_vec` among `catalog` rows by cosine sim to `query`.

    catalog rows are pre-normalized; tool_vec and query normalized here.
    A lower rank is better (1 = top pick).
    """
    q = _l2(query)
    tool_sim = float(_l2(tool_vec) @ q)
    cat_sims = catalog @ q  # catalog already L2-normalized
    # rank = 1 + (# OTHER tools strictly more similar than this tool)
    return 1 + int(np.sum(cat_sims > tool_sim))


@dataclass
class ReplayMetrics:
    n_tools_eval: int = 0
    n_tools_skipped: int = 0
    pos_rows: int = 0
    neg_rows: int = 0
    base_pos_rank: List[int] = field(default_factory=list)
    ref_pos_rank: List[int] = field(default_factory=list)
    base_neg_rank: List[int] = field(default_factory=list)
    ref_neg_rank: List[int] = field(default_factory=list)
    per_tool: List[dict] = field(default_factory=list)
    total_log_rows: int = 0

    def _recall(self, ranks: List[int], k: int) -> float:
        return sum(1 for r in ranks if r <= k) / len(ranks) if ranks else 0.0

    def summary(self, k: int) -> dict:
        def mean(xs): return round(float(np.mean(xs)), 2) if xs else None
        return {
            "tools_evaluated": self.n_tools_eval,
            "tools_skipped_cold_start": self.n_tools_skipped,
            "total_log_rows": self.total_log_rows,
            "positive_rows": self.pos_rows,
            "negative_rows": self.neg_rows,
            "success_mean_rank_base": mean(self.base_pos_rank),
            "success_mean_rank_refined": mean(self.ref_pos_rank),
            "success_recall@k_base": round(self._recall(self.base_pos_rank, k), 3),
            "success_recall@k_refined": round(self._recall(self.ref_pos_rank, k), 3),
            "failure_mean_rank_base": mean(self.base_neg_rank),
            "failure_mean_rank_refined": mean(self.ref_neg_rank),
            "k": k,
        }


def _load_data() -> Tuple[List[str], np.ndarray, Dict[str, OutcomeRow]]:
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    db = get_supabase_manager()
    if not db.enabled:
        raise RuntimeError("Supabase not enabled — check SUPABASE_URL / SUPABASE_ANON_KEY in .env")

    tools = db.query("tools", select="name,embedding")
    names, vecs = [], []
    for t in tools:
        v = _as_vec(t.get("embedding"))
        if v is not None and t.get("name"):
            names.append(t["name"]); vecs.append(_l2(v))
    if not names:
        raise RuntimeError("No usable tool embeddings in `tools`.")
    catalog = np.vstack(vecs)

    logs = db.query("tool_outcome_log",
                    select="tool_name,query_embedding,outcome,user_rating")
    by_tool: Dict[str, List[OutcomeRow]] = {}
    for row in logs:
        emb = _as_vec(row.get("query_embedding"))
        if emb is None or not row.get("tool_name"):
            continue
        by_tool.setdefault(row["tool_name"], []).append(
            OutcomeRow(emb, row.get("outcome") or "", row.get("user_rating")))
    return names, catalog, by_tool, len(logs)


def run_replay(k: int = 10, folds: int = 5, min_outcomes: int = 8,
               alpha: float = ALPHA) -> Tuple[ReplayMetrics, dict]:
    names, catalog, by_tool, total_rows = _load_data()
    name_idx = {n: i for i, n in enumerate(names)}
    m = ReplayMetrics(total_log_rows=total_rows)

    for tool, rows in by_tool.items():
        if tool not in name_idx:
            continue
        if len(rows) < min_outcomes:
            m.n_tools_skipped += 1
            continue
        base_vec = catalog[name_idx[tool]]
        n = len(rows)
        kf = min(folds, n)
        fold_assign = np.arange(n) % kf
        rng = np.random.default_rng(42)
        rng.shuffle(fold_assign)

        t_base_pos, t_ref_pos = [], []
        for f in range(kf):
            test = [rows[i] for i in range(n) if fold_assign[i] == f]
            train = [rows[i] for i in range(n) if fold_assign[i] != f]
            if not test or not train:
                continue
            refined = _refine_vector(base_vec, train, alpha)
            for r in test:
                base_rank = _rank_of(base_vec, catalog, r.query_emb)
                ref_rank = _rank_of(refined, catalog, r.query_emb)
                if r.is_positive:
                    m.pos_rows += 1
                    m.base_pos_rank.append(base_rank); m.ref_pos_rank.append(ref_rank)
                    t_base_pos.append(base_rank); t_ref_pos.append(ref_rank)
                elif r.is_negative:
                    m.neg_rows += 1
                    m.base_neg_rank.append(base_rank); m.ref_neg_rank.append(ref_rank)

        m.n_tools_eval += 1
        if t_base_pos:
            m.per_tool.append({
                "tool": tool, "rows": n,
                "success_rank_base": round(float(np.mean(t_base_pos)), 2),
                "success_rank_refined": round(float(np.mean(t_ref_pos)), 2),
                "delta": round(float(np.mean(t_ref_pos) - np.mean(t_base_pos)), 2),
            })

    return m, m.summary(k)


def render_report(m: ReplayMetrics, s: dict) -> str:
    lines = ["# OATS replay — does refinement improve tool selection?\n"]
    lines.append("_Offline K-fold holdout: refined vectors trained on a TRAIN split, "
                 "scored on HELD-OUT rows. Lower rank = better._\n")

    if s["tools_evaluated"] == 0:
        lines.append("## ⚠️ Insufficient data — cannot evaluate OATS yet\n")
        lines.append(f"- Total `tool_outcome_log` rows: **{s['total_log_rows']}**")
        lines.append(f"- Tools with ≥ min-outcomes: **0** "
                     f"({s['tools_skipped_cold_start']} skipped for cold-start)\n")
        lines.append("OATS needs accumulated outcomes before its effect is measurable. "
                     "Keep using Chad (each tool call logs a row), then re-run. "
                     "This is itself the finding: **OATS is live but unproven — "
                     "not enough signal has accumulated.**")
        return "\n".join(lines)

    base_r = s["success_mean_rank_base"]
    ref_r = s["success_mean_rank_refined"]
    delta = round(ref_r - base_r, 2) if (base_r is not None and ref_r is not None) else None
    verdict = "—"
    if delta is not None:
        if delta < -0.05:
            verdict = f"✅ OATS HELPS — success rank improves by {abs(delta)} on held-out queries"
        elif delta > 0.05:
            verdict = f"❌ OATS REGRESSES — success rank WORSENS by {delta} (consider rollback/retune)"
        else:
            verdict = "➖ No meaningful effect — OATS neither helps nor hurts at current data volume"

    lines.append(f"## Verdict: {verdict}\n")
    lines.append("| metric | base | refined | Δ |")
    lines.append("|---|---|---|---|")
    lines.append(f"| success mean rank | {base_r} | {ref_r} | {delta} |")
    lines.append(f"| success recall@{s['k']} | {s['success_recall@k_base']} "
                 f"| {s['success_recall@k_refined']} | "
                 f"{round(s['success_recall@k_refined'] - s['success_recall@k_base'], 3)} |")
    if s["failure_mean_rank_base"] is not None:
        fdelta = round(s["failure_mean_rank_refined"] - s["failure_mean_rank_base"], 2)
        lines.append(f"| failure mean rank (↑ desired) | {s['failure_mean_rank_base']} "
                     f"| {s['failure_mean_rank_refined']} | {fdelta} |")
    lines.append("")
    lines.append(f"- Tools evaluated: **{s['tools_evaluated']}** "
                 f"(skipped cold-start: {s['tools_skipped_cold_start']})")
    lines.append(f"- Held-out rows scored: **{s['positive_rows']}** positive, "
                 f"**{s['negative_rows']}** negative, of {s['total_log_rows']} logged\n")

    movers = sorted(m.per_tool, key=lambda r: r["delta"])
    if movers:
        lines.append("### Per-tool (most-improved first)\n")
        lines.append("| tool | rows | rank base→refined | Δ |")
        lines.append("|---|---|---|---|")
        for r in movers[:15]:
            arrow = f"{r['success_rank_base']}→{r['success_rank_refined']}"
            lines.append(f"| {r['tool']} | {r['rows']} | {arrow} | {r['delta']} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="OATS replay eval — measures whether "
                                             "embedding refinement improves tool selection.")
    ap.add_argument("--k", type=int, default=10, help="recall@k cutoff (default 10)")
    ap.add_argument("--folds", type=int, default=5, help="K-fold holdout splits (default 5)")
    ap.add_argument("--min-outcomes", type=int, default=8,
                    help="min logged outcomes for a tool to be evaluated (default 8)")
    ap.add_argument("--alpha", type=float, default=ALPHA, help="OATS interpolation rate")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    print(f"[replay] OATS holdout eval | k={args.k} folds={args.folds} "
          f"min_outcomes={args.min_outcomes}")
    try:
        m, s = run_replay(args.k, args.folds, args.min_outcomes, args.alpha)
    except Exception as e:
        print(f"[replay] ERROR: {e}")
        sys.exit(2)

    report = render_report(m, s)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    (RUNS_DIR / f"replay_{ts}.json").write_text(
        json.dumps({"summary": s, "per_tool": m.per_tool}, indent=2), encoding="utf-8")
    (RUNS_DIR / "replay_latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\n[replay] saved: evals/runs/replay_{ts}.json | report: replay_latest.md")

    # Gate: fail only on a real regression, not on insufficient data.
    base_r, ref_r = s["success_mean_rank_base"], s["success_mean_rank_refined"]
    if base_r is not None and ref_r is not None and (ref_r - base_r) > 0.05:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
