"""langsmith_trace.py — read the FULL nested execution trace of an eval run from LangSmith.

Why this exists: the workflow probe (live.py) reconstructs a transcript from the WebSocket
event stream, which only carries top-level supervisor turn-completes. The real work — every
subagent (research_agent/traversal_agent/...) turn and its dozens of internal web_search /
tool calls — happens inside nested subgraphs that DON'T broadcast to the eval client. So the
WS transcript shows ~3 opaque turns while a single 3-caterer run actually did ~600 runs /
300+ searches. LangChain tracing (LANGSMITH_TRACING=true, project ChadAi) already records the
complete callback tree; every Chad turn is a `ChadTurn` root run tagged `platform:eval` with
`metadata.session_id`. This module pulls that tree back so the harness can show what REALLY
happened — which subagents ran, how many searches, where time went, what looped, what failed.

Standalone:  python -m evals.langsmith_trace <session_substring>   (e.g. eval_wf_53250f2f)
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    """Best-effort load of LANGSMITH_* from .env (walking up from the worktree) so a standalone
    run or an eval process that didn't inherit the server's env can still authenticate."""
    if os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"):
        return
    for base in (_PROJECT_ROOT, *_PROJECT_ROOT.parents):
        env = base / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            return


@dataclass
class TraceSummary:
    session_id: Optional[str] = None
    found: bool = False
    turns: int = 0                       # ChadTurn root runs
    total_runs: int = 0                  # every run in every turn's tree
    llm_calls: int = 0
    tool_calls: int = 0
    duration_s: float = 0.0              # wall-clock first-start → last-end
    tool_counts: Dict[str, int] = field(default_factory=dict)
    subagent_counts: Dict[str, int] = field(default_factory=dict)  # delegate_to_* invocations
    errored_runs: List[str] = field(default_factory=list)          # "name: error"
    slowest: List[str] = field(default_factory=list)               # "name  Ns"
    note: str = ""

    @property
    def search_calls(self) -> int:
        return sum(v for k, v in self.tool_counts.items() if "search" in k)


def fetch_session_trace(session_substring: str, project: Optional[str] = None,
                        max_roots: int = 40) -> TraceSummary:
    """Aggregate every LangSmith run under the eval session whose ChadTurn root metadata
    session_id CONTAINS ``session_substring`` (the eval uses conv ids like ``eval_wf_ab12``
    while LangSmith stores the fully-qualified ``main:default:gui:eval_wf_ab12``)."""
    _load_env()
    summary = TraceSummary(session_id=session_substring)
    try:
        from langsmith import Client
    except Exception as e:  # noqa: BLE001
        summary.note = f"langsmith SDK unavailable: {e}"
        return summary
    if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        summary.note = "no LANGSMITH_API_KEY in env/.env — trace enrichment skipped"
        return summary

    project = project or os.environ.get("LANGSMITH_PROJECT", "ChadAi")
    try:
        client = Client()
        roots = [
            r for r in client.list_runs(project_name=project,
                                        filter='eq(name, "ChadTurn")', limit=max_roots)
            if session_substring in str((r.extra or {}).get("metadata", {}).get("session_id", ""))
        ]
        if not roots:
            summary.note = f"no ChadTurn traces found for session '{session_substring}' in {project}"
            return summary

        summary.found = True
        summary.turns = len(roots)
        tool_counts: Counter = Counter()
        subagents: Counter = Counter()
        durations: List[tuple] = []
        starts: List[Any] = []
        ends: List[Any] = []

        for root in roots:
            for r in client.list_runs(project_name=project, trace_id=root.trace_id):
                summary.total_runs += 1
                if r.start_time:
                    starts.append(r.start_time)
                if r.end_time:
                    ends.append(r.end_time)
                    if r.start_time:
                        durations.append(((r.end_time - r.start_time).total_seconds(), r.name))
                if r.run_type == "llm":
                    summary.llm_calls += 1
                elif r.run_type == "tool":
                    summary.tool_calls += 1
                    tool_counts[r.name] += 1
                    if r.name.startswith("delegate_to_"):
                        subagents[r.name] += 1
                if r.error:
                    first = str(r.error).splitlines()[0][:120]
                    summary.errored_runs.append(f"{r.name}: {first}")

        summary.tool_counts = dict(tool_counts.most_common())
        summary.subagent_counts = dict(subagents.most_common())
        if starts and ends:
            summary.duration_s = round((max(ends) - min(starts)).total_seconds(), 1)
        summary.slowest = [f"{n}  {d:.0f}s" for d, n in sorted(durations, reverse=True)[:6]]
    except Exception as e:  # noqa: BLE001
        summary.note = f"LangSmith fetch error: {type(e).__name__}: {e}"
    return summary


def render_trace_oneline(s: TraceSummary) -> str:
    """One-line live summary for per-round console logging DURING a run — so the eval log
    shows the real nested activity as it progresses, instead of an agent having to query
    LangSmith by hand afterward."""
    if not s.found:
        return f"langsmith: {s.note or 'no trace yet'}"
    subs = ", ".join(f"{k.replace('delegate_to_','')}x{v}" for k, v in s.subagent_counts.items())
    bits = [
        f"turns={s.turns}",
        f"runs={s.total_runs}",
        f"llm={s.llm_calls}",
        f"searches={s.search_calls}",
    ]
    if s.tool_counts.get("parallel_research"):
        bits.append(f"parallel_research x{s.tool_counts['parallel_research']}")
    line = f"langsmith: {' '.join(bits)}"
    if subs:
        line += f" | subagents: {subs}"
    if s.errored_runs:
        line += f" | errs={len(s.errored_runs)}"
    return line


def render_trace_markdown(s: TraceSummary) -> str:
    """Compact markdown block for the eval transcript — the REAL nested activity."""
    if not s.found:
        return f"## LangSmith trace\n_{s.note or 'not available'}_\n"
    lines = [
        "## LangSmith trace (full nested execution -- what the WS transcript can't see)",
        f"- **{s.turns} turns**, **{s.total_runs} total runs**, "
        f"**{s.llm_calls} LLM calls**, **{s.tool_calls} tool calls** "
        f"(**{s.search_calls} searches**), wall-clock **{s.duration_s}s**",
    ]
    if s.subagent_counts:
        lines.append("- **Subagent delegations:** "
                     + ", ".join(f"{k.replace('delegate_to_','')} x{v}"
                                 for k, v in s.subagent_counts.items()))
    if s.tool_counts:
        top = ", ".join(f"{k} x{v}" for k, v in list(s.tool_counts.items())[:10])
        lines.append(f"- **Tools:** {top}")
    if s.slowest:
        lines.append(f"- **Slowest runs:** {', '.join(s.slowest)}")
    if s.errored_runs:
        lines.append(f"- **Errored runs ({len(s.errored_runs)}):** "
                     + "; ".join(s.errored_runs[:5]))
    if s.note:
        lines.append(f"- _note: {s.note}_")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    if not sub:
        print("usage: python -m evals.langsmith_trace <session_substring>")
        raise SystemExit(2)
    print(render_trace_markdown(fetch_session_trace(sub)))
