"""Project route integrity, plus opt-in local-model orientation comprehension.

The model probe is a controlled reader test, not an autonomous coding-agent
benchmark. It cannot establish that a real agent will open the right files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from evals.core import CaseResult, render_report
from scripts.dev.check_docs import ROOT, audit, destinations, git_binary, tracked_docs

SCENARIOS = [
    {"id": "persistence", "route": "Persistence",
     "task": "Fix duplicate user messages across the persisted entry paths. Identify existing files and checks; do not implement yet.",
     "refs": ["src/remote/gui_limb.py", "src/application/commands/message_commands.py"],
     "check": "session", "constraint": "8767"},
    {"id": "specialists", "route": "Specialists",
     "task": "Extend an existing specialist's domain. Find the registry and handoff path before adding another agent. Identify relevant verification.",
     "refs": ["src/core/orchestration/agents/factory.py", "src/core/orchestration/handoffs.py"],
     "check": "eval", "constraint": "existing"},
    {"id": "memory", "route": "Memory",
     "task": "A user edits a personal fact in markdown. Locate the existing initialization path and memory guide; identify a check. Preserve runtime facts and invalidation history.",
     "refs": ["src/core/intelligence/graph_rag_client.py", "docs/architecture/MEMORY_AND_CONTEXT.md"],
     "check": "test", "constraint": "preserv"},
    {"id": "coding", "route": "Coding",
     "task": "Support a coding task using the project's existing coding harness. Locate it and its dispatch path instead of designing a parallel implementation. Identify verification.",
     "refs": ["docs/architecture/CHAD_CODE.md", "src/tools/dev/openhands_dispatch.py"],
     "check": "verif", "constraint": "existing"},
    {"id": "resume", "route": "Compaction",
     "task": "Resume after compaction: preserve the user's goal and prior decisions. Find the existing compactor and bounded-checkpoint check. Never restart the production Brain on port 8767. Prior tests passed before subsequent edits: rerun them.",
     "refs": ["src/core/orchestration/turn/consolidation.py", "src/core/utils/consolidation_manager.py"],
     "check": "test_turn_consolidator_bounded_checkpoint.py", "constraint": "8767"},
]
ORIENTATION_FILES = ("AGENTS.md", "docs/architecture/END_TO_END.md", "README.md")


def orientation(root: Path, ref: str | None, budget: int) -> str:
    """Same ordered inputs and character budget for baseline and mapped trials."""
    chunks = []
    for path in ORIENTATION_FILES:
        if ref:
            result = subprocess.run([git_binary(), "show", f"{ref}:{path}"], cwd=root,
                                    capture_output=True, encoding="utf-8")
            text = result.stdout if result.returncode == 0 else ""
        else:
            text = (root / path).read_text(encoding="utf-8") if (root / path).exists() else ""
        if text:
            chunks.append(f"FILE {path}\n{text}")
    return "\n\n".join(chunks)[:budget]


def structural_results(root: Path) -> list[CaseResult]:
    map_text = (root / ORIENTATION_FILES[1]).read_text(encoding="utf-8")
    errors = audit(root, tracked_docs(root))
    results = []
    for case in SCENARIOS:
        route = next((line for line in map_text.splitlines() if f"**{case['route']}:**" in line), "")
        failures = [f"Missing route: {case['route']}"] if not route else []
        targets = {(root / ORIENTATION_FILES[1]).parent.joinpath(dest.split('#')[0]).resolve()
                   for dest in destinations(route)}
        failures += [f"Missing source: {ref}" for ref in case["refs"] if not (root / ref).is_file()]
        failures += [f"Source not linked by route: {ref}" for ref in case["refs"]
                     if (root / ref).resolve() not in targets]
        failures += sorted(errors)
        results.append(CaseResult(case["id"], case["task"], not failures,
                                  assertion_failures=failures, lane="project-context-structure"))
    return results


def assess_answer(case: dict, answer: str) -> list[str]:
    """Reference checks only. Rationale and negation still require human review."""
    lower = answer.lower()
    failures = [f"Missing expected reference: {ref}" for ref in case["refs"] if ref.lower() not in lower]
    for field in ("check", "constraint"):
        if case[field].lower() not in lower:
            failures.append(f"Missing {field} signal: {case[field]}")
    return failures


def loaded_model_ids() -> set[str]:
    with urlopen("http://localhost:1234/api/v1/models", timeout=5) as response:
        models = json.load(response).get("models", [])
    return {instance["id"] for model in models for instance in model.get("loaded_instances", [])}


def reader_results(root: Path, model: str, ref: str | None, budget: int) -> list[CaseResult]:
    context = orientation(root, ref, budget)
    results = []
    for case in SCENARIOS:
        started = time.monotonic()
        payload = {
            "model": model, "temperature": 0, "max_tokens": 650,
            "messages": [
                {"role": "system", "content": "You are a repository reader. Use only supplied project context. "
                 "Identify existing implementation paths, checks and constraints. Do not write code or invent paths. "
                 "If context is insufficient, say so. All scenarios must keep production port 8767 running. "
                 "Reply concisely with Sources, Checks, Constraints and Rationale.\n\n" + context},
                {"role": "user", "content": case["task"]},
            ],
        }
        request = Request("http://localhost:1234/v1/chat/completions",
                          data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        error, answer, usage = None, "", {}
        try:
            # Sequential; only an already-loaded instance is accepted by main.
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            answer = result["choices"][0]["message"].get("content") or ""
            usage = result.get("usage", {})
            failures = assess_answer(case, answer)
            if result["choices"][0].get("finish_reason") == "length":
                failures.append("Incomplete response: generation reached its output budget")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures = [error]
        results.append(CaseResult(case["id"], case["task"], not failures,
            assertion_failures=failures, error=error, lane="project-context-reader",
            metrics={"duration_s": time.monotonic() - started,
                     "total_tokens": usage.get("total_tokens", 0), "orientation_chars": len(context)},
            transcript={"answer": answer, "model": model, "ref": ref or "working-tree",
                        "manual_review_required": True, "context": context}))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Opt in to sequential reader trials on this already-loaded LM Studio instance")
    parser.add_argument("--compare-ref", help="Compare the same model and inputs with this Git revision")
    parser.add_argument("--context-chars", type=int, default=16000)
    args = parser.parse_args(argv)
    if not 1000 <= args.context_chars <= 32000:
        parser.error("--context-chars must be 1000..32000")
    if args.compare_ref:
        if not args.model:
            parser.error("--compare-ref requires --model")
        args.compare_ref = subprocess.check_output(
            [git_binary(), "rev-parse", "--verify", args.compare_ref + "^{commit}"],
            cwd=ROOT, text=True).strip()
    results = structural_results(ROOT)
    reports = [render_report("project_context", results, False, lane="project-context-structure")]
    if args.model:
        if args.model not in loaded_model_ids():
            parser.error("Model is not already loaded; this probe never loads or swaps models")
        for label, ref in (("baseline", args.compare_ref), ("mapped", None)):
            if label == "baseline" and ref is None:
                continue
            batch = reader_results(ROOT, args.model, ref, args.context_chars)
            results.extend(batch)
            reports.append(f"## {label}\n" + render_report("project_context", batch, False,
                                                         lane="project-context-reader"))
    note = ("\nScope: structural checks verify routes; reader scores verify reference signals only. "
            "Manually review rationale, constraints and wrong turns in JSON transcripts. "
            "This does not measure autonomous file discovery or real-agent compaction retention.\n")
    output = ROOT / "evals/runs"
    output.mkdir(parents=True, exist_ok=True)
    (output / "project_context_latest.md").write_text("\n\n".join(reports) + note, encoding="utf-8")
    (output / "project_context_latest.json").write_text(json.dumps({
        "revision": subprocess.check_output([git_binary(), "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(
            [git_binary(), "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "scope": note.strip(), "results": [r.__dict__ for r in results]}, indent=2), encoding="utf-8")
    print("\n\n".join(reports) + note)
    # Baseline misses are expected in comparison; gate only current results.
    current = [r for r in results if not r.transcript or r.transcript.get("ref") == "working-tree"]
    return int(any(not r.passed for r in current))


if __name__ == "__main__":
    raise SystemExit(main())
