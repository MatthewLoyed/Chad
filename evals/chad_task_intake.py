"""Offline, operator-authored real-task contracts. No inference or repair dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from evals.chad_triage_gate import _decode


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def validate_spec(spec: dict):
    if spec.get("schema_version") != 1 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", spec.get("task_id", "")):
        raise ValueError("Expected schema_version=1 and a stable lowercase task_id")
    checks = spec.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 20:
        raise ValueError("Provide 1–20 operator-owned checks")
    ids = set()
    for check in checks:
        if not isinstance(check, dict) or not re.fullmatch(r"[a-z0-9_-]{1,80}", check.get("id", "")) or check["id"] in ids:
            raise ValueError("Check IDs must be unique and stable")
        ids.add(check["id"])
        kind = check.get("kind")
        if kind == "response_json_equals":
            if set(check) != {"id", "kind", "expected"}:
                raise ValueError("Exact JSON check requires expected")
        elif kind in {"required_tool", "forbidden_tool"}:
            if set(check) - {"id", "kind", "name", "params"} or not isinstance(check.get("name"), str) or not check["name"] or not isinstance(check.get("params", {}), dict):
                raise ValueError("Tool checks require name and optional exact parameter subset")
        elif kind == "fixture_unchanged":
            if set(check) != {"id", "kind"}:
                raise ValueError("Fixture check accepts no generated grading code")
        else:
            raise ValueError("Unsupported check; no model-generated grading code is executed")
    handoff = spec.get("handoff")
    if handoff is not None and (set(handoff) != {"privacy_reviewed", "reproduction", "expected", "observed", "acceptance"}
            or type(handoff["privacy_reviewed"]) is not bool
            or any(not isinstance(handoff[k], str) or not handoff[k].strip() for k in ("reproduction", "expected", "observed", "acceptance"))):
        raise ValueError("Handoff requires explicit privacy review and four operator-written fields")


def assess(spec: dict, capture: dict) -> dict:
    validate_spec(spec)
    trace = capture.get("trace", {})
    checks = []
    incomplete = trace.get("status") != "success" or bool(trace.get("error"))
    for check in spec["checks"]:
        passed, evidence = None, {}
        kind = check["kind"]
        if not incomplete:
            if kind == "response_json_equals" and isinstance(trace.get("final_response"), str):
                evidence = {"field": "trace.final_response", "sha256": digest(trace["final_response"])}
                try:
                    passed = digest(_decode(trace["final_response"])) == digest(check["expected"])
                except (ValueError, TypeError):
                    passed = False
            elif kind in {"required_tool", "forbidden_tool"} and isinstance(trace.get("tool_calls"), list) and all(
                    isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("params", {}), dict)
                    for c in trace["tool_calls"]):
                indices = [i for i, call in enumerate(trace["tool_calls"])
                           if call.get("name") == check["name"] and all(
                               key in (call.get("params") or {}) and digest(call["params"][key]) == digest(value)
                               for key, value in check.get("params", {}).items())]
                passed = bool(indices) if kind == "required_tool" else not indices
                evidence = {"field": "trace.tool_calls", "indices": indices}
            elif kind == "fixture_unchanged" and all(isinstance(capture.get(k), dict)
                                                       for k in ("fixture_before", "fixture_after")):
                passed = capture["fixture_before"] == capture["fixture_after"]
                evidence = {"before_sha256": digest(capture["fixture_before"]), "after_sha256": digest(capture["fixture_after"])}
        checks.append({"id": check["id"], "kind": kind, "passed": passed, "evidence": evidence})
    status = "inconclusive" if incomplete or any(c["passed"] is None for c in checks) else (
        "failed_contract" if any(c["passed"] is False for c in checks) else "passed_contract")
    return {"schema_version": 1, "task_id": spec["task_id"], "contract_sha256": digest(spec["checks"]),
            "status": status, "checks": checks, "cause": "unverified", "repair_dispatched": False}


def intake(spec: dict, source: Path, output: Path, trial_id: str | None = None) -> dict:
    validate_spec(spec)
    raw = source.read_bytes()
    report = _decode(raw.decode("utf-8"))
    if trial_id is not None:
        trials = [t for t in report.get("trials", []) if t.get("id") == trial_id]
        if len(trials) != 1:
            raise ValueError("Trial selection must identify exactly one captured trial")
        capture = trials[0]
    else:
        capture = report
    # Intake is read-only. No server is started, connected to, or restarted.
    result = assess(spec, capture)
    result.update(source_sha256=hashlib.sha256(raw).hexdigest(), trial_id=trial_id,
                  capture_identity=digest({"run_id": report.get("run_id") or hashlib.sha256(raw).hexdigest(), "trial_id": trial_id}),
                  source=str(source.resolve()), provenance=report.get("server"),
                  handoff_exported=(spec.get("handoff") or {}).get("privacy_reviewed") is True)
    if output.exists():
        raise ValueError("Use a new output directory to preserve prior assessments")
    output.mkdir(parents=True)
    (output / "assessment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    inbox_path = output.parent / "real-task-inbox.json"
    inbox = _decode(inbox_path.read_text(encoding="utf-8")) if inbox_path.exists() else {"schema_version": 1, "bugs": {}}
    # Partial evidence is retained, but an incomplete contract cannot enter the bug inbox.
    if result["status"] == "failed_contract":
        for check in result["checks"]:
            if check["passed"] is not False:
                continue
            signature = digest([result["task_id"], result["contract_sha256"], check["id"]])
            bug = inbox["bugs"].setdefault("CHAD-TASK-" + signature[:12], {
                "task_id": result["task_id"], "check_id": check["id"], "contract_sha256": result["contract_sha256"],
                "status": "open", "cause": "unverified", "occurrences": []})
            if not any(o["capture_identity"] == result["capture_identity"] for o in bug["occurrences"]):
                bug["occurrences"].append({"capture_identity": result["capture_identity"],
                    "assessment": str((output / "assessment.json").resolve()), "evidence": check["evidence"]})
    inbox_path.write_text(json.dumps(inbox, indent=2), encoding="utf-8")
    if result["handoff_exported"]:
        handoff = spec["handoff"]
        body = "# Operator-reviewed task handoff\n\n" + "\n\n".join(
            f"{key.capitalize()}: {handoff[key]}" for key in ("reproduction", "expected", "observed", "acceptance"))
        body += f"\n\nIndependent contract status: {result['status']}. Cause unverified. No repair dispatched.\n"
        (output / "handoff.md").write_text(body, encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--trial", help="Select one trial from a captured run report")
    args = parser.parse_args()
    result = intake(_decode(args.spec.read_text(encoding="utf-8")), args.capture, args.output, args.trial)
    print(json.dumps({"status": result["status"], "handoff_exported": result["handoff_exported"]}))
