"""
triage.py — cluster eval failures by root cause so an agent (or you) knows where to look.

The runner produces per-case pass/fail with assertion + judge reasons. On its own that's a
flat list. Triage groups failures by a *signature* (what kind of failure) and maps each
signature to the harness area / files likely responsible, so the fix target is named, not hunted.

This is the deterministic seed for the `harness-triage` skill: run suite → read triage.md →
investigate the top cluster. It does NOT call a model; it's pure clustering over results.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


# ── Signature → (human label, likely area, files/levers to check) ─────────────
# Order matters: first matching rule wins. Keep specific rules above generic ones.

@dataclass
class Cluster:
    key: str
    label: str
    area: str
    files: str
    cases: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)   # one reason string per case


def _classify(failure: str) -> Tuple[str, str, str, str]:
    """Map a single failure/reason string to (key, label, area, files)."""
    f = failure.lower()

    # — deterministic assertion signatures —
    if f.startswith("tools_include") or f.startswith("tools_any"):
        return ("tool_missing", "Required tool not selected/called",
                "tool selection (RAG / wiring)",
                "SemanticToolSelector, tool registry, Supabase tool sync; verify the tool exists")
    if f.startswith("tools_exclude"):
        return ("tool_overselect", "Irrelevant tool leaked into selection",
                "tool selection over-reach",
                "SemanticToolSelector thresholds / negative filtering")
    if f.startswith(("max_tools", "max_system_tokens", "max_total_tokens")):
        return ("context_bloat", "Context/token budget exceeded",
                "prompt + tool-schema bloat",
                "PromptRegistry fragments, tool schema verbosity, dual-listing in system prompt")
    if f.startswith(("min_steps", "max_steps")):
        return ("planner_decomp", "Plan under/over-decomposed",
                "planner decomposition",
                "planner_node.py + the planner prompt (step granularity guidance)")
    if f.startswith("max_duration_s"):
        return ("latency", "Turn latency exceeded threshold",
                "turn latency (NOT necessarily a correctness bug -- check tool_names/response "
                "in the transcript before assuming the model was wrong)",
                "SemanticToolSelector shortlist size/timing, tool catalog size, LM Studio "
                "contention with prod daemons (heartbeat/cron) sharing the same model slot -- "
                "re-run with daemons off (config/preferences.yaml daemons.enabled) to isolate "
                "harness/contention latency from real model latency")
    if f.startswith(("must_complete", "max_rounds", "min_rounds")):
        return ("workflow_stall", "Workflow didn't complete / too many rounds",
                "workflow execution loop",
                "supervisor_node.py, routing.py (route_after_supervisor), step-management tools")
    if f.startswith(("response_includes", "response_excludes")):
        return ("response_content", "Final response missing/contains expected text",
                "response content",
                "final-response extraction in turn_coordinator / langgraph_engine")
    if f.startswith("system_includes") or f.startswith("system_excludes"):
        return ("prompt_assembly", "System prompt missing/contains expected fragment",
                "prompt assembly",
                "PromptRegistry scopes, system-prompt builder")

    # — judge rubric signatures (reason strings are prefixed by the runner) —
    if "tool_appropriateness" in f:
        return ("tool_semantic", "Judge: wrong/missing tools (semantic)",
                "tool selection (semantic)",
                "SemanticToolSelector, tool descriptions; cross-check vs deterministic tool_* clusters")
    if "planner_quality" in f:
        return ("planner_logic", "Judge: weak/illogical plan",
                "planner logic",
                "planner_node.py + planner prompt (ordering, tool-naming, verifiability)")
    if "response_grounded" in f:
        return ("hallucination", "Judge: response not grounded in tool results",
                "grounding / hallucination",
                "model behavior on small models; tool-result injection into context")
    if "prompt_contract" in f:
        return ("prompt_contradiction", "Judge: contradictory/duplicated instructions",
                "prompt contradictions",
                "PromptRegistry fragments across scopes (agent/planner/supervisor)")
    if "workflow_outcome" in f:
        return ("workflow_outcome", "Judge: workflow goal not actually accomplished",
                "workflow execution quality",
                "supervisor_node.py execution, step completion, tool wiring")

    return ("other", "Uncategorized failure", "unknown", "read the transcript for this case")


def cluster_failures(results) -> List[Cluster]:
    """Group failing CaseResults into root-cause clusters. results = list of CaseResult."""
    clusters: Dict[str, Cluster] = {}
    for r in results:
        if r.overall_passed:
            continue
        # VOID cases get their own cluster keyed by the specific infrastructure fault, so
        # "12 cases: google_oauth_expired" reads as the ONE thing to fix instead of being
        # spread across a dozen misleading capability clusters.
        if getattr(r, "is_void", False):
            reason = r.void_reason or "unknown"
            c = clusters.setdefault(
                f"void:{reason}",
                Cluster(f"void:{reason}",
                        f"VOID ({reason}) — model was never fairly tested",
                        "environment / harness, NOT model capability",
                        "run `python -m evals.preflight` to reproduce and fix, then re-run"))
            c.cases.append(r.case_id)
            if r.error:
                c.examples.append(r.error[:200])
            continue
        # Collect this case's failure signals (errors, assertions, failed judges).
        signals: List[str] = []
        if r.error:
            signals.append(f"error: {r.error}")
        signals.extend(r.assertion_failures)
        for j in r.judge_results:
            if not j.get("passed", True):
                reasons = "; ".join(j.get("reasons", [])) or "(no reason)"
                signals.append(f"judge[{j['rubric']}] {reasons}")

        # Pick the PRIMARY signature for this case (first signal classifies it),
        # but record the case under every distinct signature it hit.
        prompts_offline = bool(r.metrics.get("prompts_offline"))
        seen_keys = set()
        for sig in signals or ["(no signal)"]:
            if sig.startswith("error:"):
                key, label, area, files = ("infra", "Infrastructure error / crash",
                                           "harness/server/probe",
                                           "server bootstrap, WS probe, model availability")
            else:
                key, label, area, files = _classify(sig)
                # Offline mock prompts were active — a prompt-content failure here proves
                # nothing about the real prompt (stub text, not Supabase persona_config).
                # Don't let it masquerade as a genuine prompt_assembly/contradiction bug.
                if prompts_offline and key in ("prompt_assembly", "prompt_contradiction"):
                    key, label, area, files = (
                        "prompt_fallback", "Offline mock prompts active (not a real prompt bug)",
                        "eval environment — Supabase unreachable",
                        "fix Supabase connectivity/creds and re-run; ignore prompt-content "
                        "findings from this run until then")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            c = clusters.setdefault(key, Cluster(key, label, area, files))
            c.cases.append(r.case_id)
            c.examples.append(sig[:200])
    # Sort by number of cases (biggest cluster first).
    return sorted(clusters.values(), key=lambda c: len(c.cases), reverse=True)


def render_triage(suite: str, results, clusters: List[Cluster]) -> str:
    total = len(results)
    voided = sum(1 for r in results if getattr(r, "is_void", False))
    failed = sum(1 for r in results if not r.overall_passed) - voided
    L = [
        "# HARNESS TRIAGE",
        f"Suite   : {suite}",
        f"Failures: {failed}/{total}"
        + (f"  |  VOID (environment, not the model): {voided}/{total}" if voided else ""),
        "",
        "Failures grouped by root cause. Investigate the top cluster first — it names the "
        "harness area and files most likely responsible. Cross-reference the per-case "
        "transcripts in `runs/transcripts/` for the full trajectory.",
        "",
    ]
    if not clusters:
        L.append("No failures — nothing to triage. 🎉")
        return "\n".join(L)

    for i, c in enumerate(clusters, 1):
        L.append(f"## {i}. {c.label}  ({len(c.cases)} case{'s' if len(c.cases) != 1 else ''})")
        L.append(f"- **Likely area:** {c.area}")
        L.append(f"- **Where to look:** {c.files}")
        L.append(f"- **Cases:** {', '.join(sorted(set(c.cases)))}")
        L.append(f"- **Example:** {c.examples[0] if c.examples else '(none)'}")
        L.append("")

    # A coarse "is this a code bug or a dataset/threshold issue?" nudge.
    L += [
        "## Next step",
        "For each cluster decide: **real harness bug** (wrong wiring, missing tool, prompt "
        "conflict, broken node) vs **dataset threshold to tune** (assertion too strict, judge "
        "miscalibrated). The `note` field on the case + the transcript tell you which. A cluster "
        "that's ALL judge-failures with sound trajectories is often a judge/threshold issue; a "
        "cluster of deterministic `tools_*`/`must_complete` failures is usually a real wiring bug.",
    ]
    return "\n".join(L)
