r"""
compare.py — head-to-head model comparison for live/workflow eval lanes.

Hot-swaps the Brain server's active local model (already-loaded in LM Studio only),
runs the same suite per model, and writes a comparison matrix.

Example (planner quality: Gemma 31B vs Qwen3 14B):
    $env:PYTHONPATH="."
    .\.venv\Scripts\python.exe -m evals.runner --suite planner --live --judge `
        --judge-model "qwen/qwen3.5-9b" `
        --models "google/gemma-4-31b-qat,qwen/qwen3-14b"
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

from evals.core import RUNS_DIR, CaseResult
from evals.live import SERVER_WS, load_auth_token, server_is_up

LMSTUDIO_MODELS_URL = "http://localhost:1234/v1/models"
SWAP_SETTLE_S = 3.0


@dataclass
class ModelRun:
    model_id: str
    results: List[CaseResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def scored(self) -> List[CaseResult]:
        """Cases that actually produced a verdict for this model.

        A head-to-head comparison is the single worst place to let infrastructure noise
        through: if model A's turn happened while the Google token was alive and model B's
        happened after it died, B "loses" for reasons that have nothing to do with B. So
        VOID cases are excluded here exactly as they are in the single-run summary.
        """
        return [r for r in self.results if r.counts_toward_pass_rate]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.overall_passed)

    @property
    def total(self) -> int:
        return len(self.scored)

    @property
    def void(self) -> int:
        return len(self.results) - len(self.scored)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0

    @property
    def comparable(self) -> bool:
        """Two models are only comparable if both were scored on (nearly) the same cases."""
        return bool(self.results) and self.void / len(self.results) <= 0.10

    def mean_duration(self) -> Optional[float]:
        vals = [r.metrics.get("duration_s") for r in self.results if r.metrics.get("duration_s") is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def mean_planner_score(self) -> Optional[float]:
        scores = []
        for r in self.results:
            for j in r.judge_results:
                if j.get("rubric") == "planner_quality" and j.get("score") is not None:
                    scores.append(float(j["score"]))
        return round(sum(scores) / len(scores), 2) if scores else None


def list_loaded_models() -> List[str]:
    """Return model ids currently loaded in LM Studio (empty if unreachable)."""
    try:
        with urllib.request.urlopen(LMSTUDIO_MODELS_URL, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception:
        return []


def gate_check_models(model_ids: List[str]) -> List[str]:
    """Return model ids that are NOT already loaded in LM Studio."""
    loaded = set(list_loaded_models())
    return [m for m in model_ids if m not in loaded]


def get_active_model_from_config() -> Any:
    from src.shared.config_manager import ConfigManager
    ConfigManager.load_config()
    return ConfigManager.get("local_models.active_model")


async def hot_swap_model(model_id: str, auth_token: Optional[str] = None) -> None:
    """Swap the running Brain server to `model_id` via set_model_config WS message."""
    import websockets

    token = auth_token or load_auth_token()
    if not token:
        raise RuntimeError("no auth token (set CHAD_REMOTE_TOKEN or add it to .env)")

    payload = {"local_models": {"use_local": True, "active_model": model_id}}
    headers = {"Origin": "http://localhost:5173"}
    msg = json.dumps({
        "id": str(uuid.uuid4())[:8],
        "type": "set_model_config",
        "payload": payload,
        "auth_token": token,
        "timestamp": time.time(),
    })

    try:
        ws_ctx = websockets.connect(SERVER_WS, additional_headers=headers,
                                    ping_interval=None, open_timeout=30)
    except TypeError:
        ws_ctx = websockets.connect(SERVER_WS, extra_headers=headers,
                                    ping_interval=None, open_timeout=30)

    async with ws_ctx as ws:
        await ws.send(json.dumps({
            "id": str(uuid.uuid4())[:8], "type": "auth",
            "payload": {}, "auth_token": token, "timestamp": time.time(),
        }))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=35.0))
        if reply.get("type") != "auth" or reply.get("payload", {}).get("status") != "success":
            raise RuntimeError(f"auth rejected during model swap: {reply.get('payload')}")
        await ws.send(msg)

    await asyncio.sleep(SWAP_SETTLE_S)


def render_comparison_matrix(suite: str, lane: str, runs: List[ModelRun],
                             judge_model: str) -> str:
    lines = [
        f"# Model comparison — `{suite}` ({lane})",
        "",
        f"Judge (fixed): `{judge_model}`",
        "",
        "## Summary",
        "",
        "Pass rates are over SCORED cases (cases voided by infrastructure are excluded — see "
        "the Void column). A model whose Void count is high is not comparable to one whose "
        "isn't: they were effectively measured on different case sets.",
        "",
        "| Model | Pass | Pass rate | Void | Mean planner score | Mean duration (s) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        if run.error and not run.results:
            lines.append(f"| `{run.model_id}` | ERROR | — | — | — | — |")
            continue
        score = run.mean_planner_score()
        dur = run.mean_duration()
        void_cell = str(run.void) + ("" if run.comparable else " **!**")
        lines.append(
            f"| `{run.model_id}` | {run.passed}/{run.total} | {run.pass_rate:.0%} | "
            f"{void_cell} | {score if score is not None else '—'} | "
            f"{dur if dur is not None else '—'} |"
        )
    if any(not run.comparable for run in runs if run.results):
        lines += ["", "**!** = more than 10% of this model's cases were voided by "
                      "infrastructure, so its pass rate is not a fair comparison. Fix the "
                      "environment (`python -m evals.preflight`) and re-run."]

    lines.extend(["", "## Per case", ""])
    case_ids = []
    for run in runs:
        for r in run.results:
            if r.case_id not in case_ids:
                case_ids.append(r.case_id)

    header = "| Case | " + " | ".join(f"`{r.model_id}`" for r in runs) + " |"
    sep = "|---|" + "|".join("---:" for _ in runs) + "|"
    lines.extend([header, sep])

    for cid in case_ids:
        cells = []
        for run in runs:
            match = next((r for r in run.results if r.case_id == cid), None)
            if not match:
                cells.append("—")
                continue
            mark = "PASS" if match.overall_passed else "FAIL"
            pq = next((j for j in match.judge_results if j.get("rubric") == "planner_quality"), None)
            score = pq.get("score") if pq else None
            steps = match.metrics.get("plan_steps")
            extra = []
            if score is not None:
                extra.append(f"score={score}")
            if steps is not None:
                extra.append(f"{steps} steps")
            if match.metrics.get("duration_s") is not None:
                extra.append(f"{match.metrics['duration_s']}s")
            detail = f" ({', '.join(extra)})" if extra else ""
            cells.append(f"{mark}{detail}")
        lines.append(f"| `{cid}` | " + " | ".join(cells) + " |")

    lines.append("")
    return "\n".join(lines)


async def run_model_comparison(
    *,
    suite: str,
    model_ids: List[str],
    do_judge: bool,
    judge_model: str,
    quiet: bool,
    live: bool,
    workflow: bool,
    limit: int,
    approval: str,
    max_rounds: int,
    repeat: int,
) -> Tuple[List[ModelRun], bool]:
    """Run the suite once per model; restore the original active model when done."""
    from evals.runner import run_suite

    if not live and not workflow:
        raise ValueError("--models requires --live or --workflow (dry-run uses no model)")

    if not server_is_up():
        raise RuntimeError(
            "Brain server not running on :8767. Start it first:\n"
            "  $env:PYTHONPATH='.'; $env:CHAD_DAEMONS_ENABLED='false'; "
            ".venv\\Scripts\\python.exe src\\remote\\server.py"
        )

    missing = gate_check_models(model_ids)
    if missing:
        loaded = list_loaded_models()
        raise RuntimeError(
            f"Refusing to JIT-load models (VRAM safety). Not loaded in LM Studio: {missing}\n"
            f"Currently loaded: {loaded or '(none — is LM Studio running?)'}"
        )

    original = get_active_model_from_config()
    token = load_auth_token()
    lane = "workflow" if workflow else "live"
    all_runs: List[ModelRun] = []

    print(f"[compare] models: {', '.join(model_ids)} | suite={suite} | lane={lane}")
    print(f"[compare] gate OK — all target models already loaded in LM Studio")

    try:
        for model_id in model_ids:
            print(f"\n[compare] ── swapping to `{model_id}` ──")
            try:
                await hot_swap_model(model_id, auth_token=token)
            except Exception as e:
                all_runs.append(ModelRun(model_id=model_id, error=str(e)))
                continue

            _, results = await run_suite(
                suite, do_judge, judge_model, quiet,
                live=live, workflow=workflow, limit=limit,
                approval=approval, max_rounds=max_rounds, repeat=repeat,
            )
            all_runs.append(ModelRun(model_id=model_id, results=results))
    finally:
        if original:
            restore_id = original if isinstance(original, str) else original.get("name")
            if restore_id and restore_id not in model_ids:
                print(f"\n[compare] restoring original model `{restore_id}`")
            elif restore_id:
                print(f"\n[compare] restoring config model `{restore_id}`")
            if restore_id:
                try:
                    await hot_swap_model(restore_id, auth_token=token)
                except Exception as e:
                    print(f"[compare] WARNING: failed to restore original model: {e}")

    all_passed = all(
        run.passed == run.total and run.total > 0 and not run.error
        for run in all_runs
        if run.results
    )
    return all_runs, all_passed


def persist_comparison(suite: str, lane: str, runs: List[ModelRun], judge_model: str) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report = render_comparison_matrix(suite, lane, runs, judge_model)
    out = RUNS_DIR / f"compare_{suite}_{ts}.md"
    out.write_text(report, encoding="utf-8")
    latest = RUNS_DIR / "compare_latest.md"
    latest.write_text(report, encoding="utf-8")
    return out
