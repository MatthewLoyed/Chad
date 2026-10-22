# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
"""Package the saved Phase 4D.2 decisions without replaying external actions.

Run with --evidence-dir pointing at the private chad-relevance-cutoff directory.
Output contains private prompts; keep it in the ignored experiment directory.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from evals.chad_evidence_review import review_evidence


def package_decisions(root: Path) -> tuple[str, dict, dict]:
    sources = {}

    def read(name):
        raw = (root / name).read_bytes()
        sources[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    combined = {"completed": False, "stop_reason": "intentional first-decision captures", "tool_calls": [], "events": []}
    cases = []
    prompt = None
    for label, capture, messages_name, cutoff in (
        ("before", "runtime-first-decision.json", "runtime-supervisor-messages.json", .42),
        ("after", "verified-cutoff-first-decision.json", "verified-supervisor-messages.json", .50),
    ):
        report, messages = read(capture), read(messages_name)
        if not report.get("intentional_stop_after_first_tool") or not report.get("cancellation_confirmed"):
            raise ValueError(f"{label}: first-decision/cancellation evidence required")
        if prompt is not None and prompt != report["executed_prompt"]:
            raise ValueError("Cannot compare different executed prompts")
        prompt = report["executed_prompt"]
        if not messages[-1]["content"].startswith(prompt):
            raise ValueError(f"{label}: prompt snapshot does not match executed request")
        trace = report["trace"]
        start = len(combined["tool_calls"])
        combined["tool_calls"].extend(trace.get("tool_calls") or [])
        combined["events"].extend(trace.get("events") or [])
        cases.append({"label": label, "capture_source": capture, "prompt_source": messages_name,
            "prompt_provenance_limit": "Snapshots selected from prompt dump by historical audit; task prefix checked here. No request correlation ID; see saved runtime audit/logs.",
            "cutoff": cutoff, "call_ids": [f"call-{i:04d}" for i in range(start, len(combined["tool_calls"]))],
            "completed": trace.get("completed"), "stop_boundary": report["scope"],
            "cancellation_confirmed": True, "supervisor_user_input": messages[-1]["content"],
            "system_sha256": hashlib.sha256(messages[0]["content"].encode()).hexdigest(),
            "bound_tool_menu": "Not included in these snapshots; do not infer menu absence from uncalled tools."})
    return prompt, combined, {"cases": cases, "source_sha256": sources,
        "operator_audit": "Saved runtime audit and prompt inspection recorded the two unrelated Drive locations at 0.42 (scores 0.447389/0.436330), omitted at 0.50. Other graph/activity/session context remains. This is document removal evidence, not proven causation or task success.",
        "review_questions": ["What action intent is evidenced before and after?", "Does either decision establish that code means the new Meet join artifact?", "Is gmail_draft an evidenced saved routine or an unsupported capability guess? Do not invent its result.", "Which missing menu/results/state evidence distinguishes selector, supervisor and execution faults?"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Preserve prior evidence; choose a new output")
    prompt, trace, context = package_decisions(args.evidence_dir)
    expected = "Create a Google Meet, obtain its real join code/link and prepare an addressed Gmail DRAFT ONLY for unambiguously identified Jordan; independently launch Draftout in Prism Launcher. No send or unrelated changes. First-decision captures cannot prove these outcomes."
    report = {"behavior_rerun": False, "repair_dispatched": False, "review_context": context}
    if args.package_only:
        from evals.chad_evidence_review import bounded_review_messages
        report["messages"], report["estimated_prompt_tokens_proxy"] = bounded_review_messages(prompt, expected, trace, review_context=context)
    else:
        from evals.chad_identification_probe import resident_models
        if not any(row.get("key") == "qwen/qwen3.6-35b-a3b" and row.get("loaded_instances")
                   for row in resident_models()):
            raise RuntimeError("Explicitly preload Qwen through model_lifecycle before review; no heavy JIT")
        report["review"] = asyncio.run(review_evidence(prompt, expected, trace,
            model="qwen/qwen3.6-35b-a3b", review_context=context))
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": report.get("review", {}).get("status", "packaged")}))


if __name__ == "__main__":
    main()
