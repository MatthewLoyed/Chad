"""Bounded identification-only full dev-Brain probe. No diagnosis/repair dispatch.

Two reset synthetic listing trials and one supplied-data positive control. Reports
persist after each turn. The only allowed Brain target is local port 8788.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path

from evals.chad_bug_findings import (
    identify_listing_trial, summarize_findings, self_review_prompt, assess_self_review, render_handoff,
)
from evals.chad_persona_capture import snapshot_persona


def require_dev_server(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme != "ws" or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port != 8788 or parsed.path != "/ws"):
        raise ValueError("Identification behavior probes require full local dev Brain on 8788")


def fixture_manifest(folder: Path) -> dict:
    result = {}
    for path in sorted(folder.iterdir()):
        if path.is_symlink():
            result[path.name] = {"type": "symlink"}
        elif path.is_file():
            result[path.name] = {"type": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            result[path.name] = {"type": "directory_or_other"}
    return result


def listing_prompt(folder: Path) -> str:
    return (f'Inspect the immediate files in this local folder: {folder}. Return exactly one JSON object '
            'with key "files" containing the actual filenames as a sorted array of strings. '
            'Use the local filesystem tools to inspect it; do not guess. Do not modify files, '
            'search elsewhere, or access the network.')


def resident_models() -> list[dict]:
    with urllib.request.urlopen("http://localhost:1234/api/v1/models", timeout=10) as response:
        return [model for model in json.load(response)["models"] if model.get("loaded_instances")]


async def run_identification(output: Path) -> dict:
    from evals import live
    from evals.chad_behavior_contracts import CASES, evaluate
    from evals.preflight.gate import run_preflight
    from evals.preflight.model import PreflightContext
    from evals.runtime_bootstrap import bootstrap_path

    require_dev_server(live.SERVER_WS)
    root = Path(__file__).resolve().parents[1]
    bootstrap_path()
    report = {"run_id": uuid.uuid4().hex, "phase": "2_detect_and_reproduce",
              "transport": "full-dev-brain-websocket", "server": live.SERVER_WS,
              "started_at": time.time(), "trials": [], "repair_dispatched": False,
              "model_role_changed": False, "decision": "inconclusive"}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    async def turn(prompt):
        try:
            return asdict(await asyncio.wait_for(live.probe_live(prompt), timeout=270))
        except Exception as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    try:
        report["base_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        report["working_tree_status"] = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
        report["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        config = root / "config/config.yaml"
        report["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
        report["persona_baseline"] = snapshot_persona(root, ("rich_output", "supervisor_routing"))
        report["initial_residency"] = resident_models()
        ctx = PreflightContext(lanes=["live"], server_health_url="http://localhost:8788/health")
        preflight = run_preflight(ctx, only=["server", "auth_token", "disk"])
        report["preflight"] = preflight.manifest()
        save()
        if not preflight.ok:
            raise RuntimeError("Preflight blocked identification; no behavioral verdict")
        # Positive control establishes transport/response completion independently of listing failure.
        control = await turn(CASES["sum"]["prompt"])
        normalized = {"response": control.get("final_response"), "error": control.get("error")}
        if control.get("status") != "success":
            normalized["error"] = normalized["error"] or "Incomplete control turn"
        report["positive_control"] = {"trace": control, "acceptance": evaluate("sum", normalized)}
        save()
        if report["positive_control"]["acceptance"]["passed"] is not True:
            raise RuntimeError("Positive control failed; investigate before listing trials")
        # Reset the exact same synthetic manifest for both fresh-conversation trials.
        folder = Path(tempfile.mkdtemp(prefix="chad-identification-"))
        names = sorted([f"alpha_{uuid.uuid4().hex[:8]}.txt", f"beta_{uuid.uuid4().hex[:8]}.txt"])
        contents = b"Synthetic evaluation fixture. No personal data.\n"
        for number in (1, 2):
            # Never recursively remove unexpected output. Preserve it for local review.
            existing = set(path.name for path in folder.iterdir())
            if existing - set(names):
                raise RuntimeError("Unexpected fixture entries; retain folder for review")
            for name in names:
                path = folder / name
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    raise RuntimeError("Fixture path was replaced; retain folder for review")
                path.write_bytes(contents)
            trial = {"id": f"listing-{number}", "folder": str(folder), "expected": names,
                     "prompt": listing_prompt(folder), "fixture_before": fixture_manifest(folder),
                     "trace": {"status": "running"}}
            report["trials"].append(trial)
            save()
            trial["trace"] = await turn(trial["prompt"])
            trial["fixture_after"] = fixture_manifest(folder)
            trial["grade"] = identify_listing_trial(trial)
            report["identification"] = summarize_findings(report["trials"])
            save()
            print(json.dumps({"trial": trial["id"], "status": trial["grade"]["status"],
                              "findings": [f["kind"] for f in trial["grade"]["findings"]]}), flush=True)
            if trial["grade"]["status"] == "inconclusive":
                raise RuntimeError("Incomplete live trial; no further live turn dispatched")
        report["decision"] = "findings_identified" if report["identification"]["findings"] else "no_failure_observed"
        report["fixture_retained"] = str(folder)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            report["final_residency"] = resident_models()
        except Exception as exc:
            report["residency_error"] = str(exc)
        try:
            report["persona_final_sha256"] = snapshot_persona(root, ("rich_output", "supervisor_routing"))["sha256"]
            report["config_final_sha256"] = hashlib.sha256((root / "config/config.yaml").read_bytes()).hexdigest()
            report["provenance_unchanged"] = (
                report["persona_final_sha256"] == report["persona_baseline"]["sha256"] and
                report["config_final_sha256"] == report["config_sha256"])
            if not report["provenance_unchanged"]:
                report["decision"] = "inconclusive"
                report["provenance_error"] = "Persona/config drift during identification"
        except Exception as exc:
            report["provenance_error"] = str(exc)
            report["decision"] = "inconclusive"
        report["finished_at"] = time.time()
        save()
        output.with_suffix(".handoff.md").write_text(render_handoff(report), encoding="utf-8")
    return report


async def run_self_review(source: dict, output: Path) -> dict:
    """One prompted Chad self-review of an independently identified completed failure."""
    from evals import live
    from evals.preflight.gate import run_preflight
    from evals.preflight.model import PreflightContext
    require_dev_server(live.SERVER_WS)
    report = {"phase": "3_analyze_and_handoff", "parent_run_id": source.get("run_id"),
              "transport": "full-dev-brain-websocket", "server": live.SERVER_WS,
              "repair_dispatched": False, "started_at": time.time(), "status": "inconclusive"}
    try:
        trial = next((trial for trial in source["trials"]
                      if identify_listing_trial(trial)["status"] == "failed_contract"), None)
        if trial is None:
            report["status"] = "no_completed_failure_to_review"
            return report
        report["trial_id"] = trial["id"]
        report["prompt"] = self_review_prompt(trial)
        report["prompt_sha256"] = hashlib.sha256(report["prompt"].encode()).hexdigest()
        preflight = run_preflight(PreflightContext(lanes=["live"],
                                  server_health_url="http://localhost:8788/health"),
                                  only=["server", "auth_token", "disk"])
        report["preflight"] = preflight.manifest()
        if not preflight.ok:
            raise RuntimeError("Preflight blocked self-review")
        report["initial_residency"] = resident_models()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        trace = asdict(await asyncio.wait_for(live.probe_live(report["prompt"]), timeout=270))
        report["trace"] = trace
        report["assessment"] = assess_self_review(trial, trace)
        report["status"] = report["assessment"]["status"]
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            report["final_residency"] = resident_models()
        except Exception as exc:
            report["residency_error"] = str(exc)
        report["finished_at"] = time.time()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--self-review-input", type=Path,
                        help="Review one captured failure instead of running behavioral trials")
    args = parser.parse_args()
    os.environ["CHAD_SERVER_URL"] = "ws://localhost:8788/ws"
    os.environ["EVAL_LIVE_TURN_TIMEOUT_S"] = "240"
    os.environ["EVAL_FIRST_EVENT_TIMEOUT"] = "60"
    os.environ["EVAL_ROUND_IDLE_CHECK_S"] = "60"
    os.environ["EVAL_ROUND_MAX_IDLE_CHECKS"] = "2"
    if args.self_review_input:
        source = json.loads(args.self_review_input.read_text(encoding="utf-8"))
        result = asyncio.run(run_self_review(source, args.output))
    else:
        result = asyncio.run(run_identification(args.output))
    print(json.dumps({key: result.get(key) for key in ("decision", "status", "error", "run_id")}), flush=True)
