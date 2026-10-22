"""LIVE memory-write behaviour lane — does the model save the RIGHT things? (#660)

`memory_write_probe.py` proves the *gate* behaves: given a candidate string, the
right thing is stored, split, deduped or rejected. It says nothing about the
question that actually decides whether memory works in practice:

    Does the model call save at the right moments, with content worth storing?

That is what this lane measures, and it is the gap the whole write-gate design
rests on. **A model that never calls save scores 19/19 on the offline probe.**

WHAT IT DOES
------------
Runs a REAL Chad turn per case (`evals.inprocess.run_turn_inprocess` — real
model, real tool loop, no server, so the production Brain on :8767 is never
touched), then inspects the `manage_user_memory` calls that turn actually made.

Categories, from `prompts/persona/memory_policy.md`:

  should_save      a durable fact stated in passing must be saved, and its
                   content must be recognizable (not a paraphrase that lost it)
  should_not_save  transient chatter, task requests, hypotheticals, and facts
                   about third parties with no bearing on the user
  supersede        a correction to something Chad already holds must forget or
                   overwrite, not silently accumulate a rival fact
  tier1_ask_first  sensitive topics (health, finances, relationships) must be
                   OFFERED, not silently saved — the policy's consent tiers are
                   only worth having if the model actually respects them

REPAIR@2 — the measurement this lane exists for
-----------------------------------------------
`MemoryWriteGate` rejections are written to be read by the model mid-turn
("rejections are prompts"). Whether that actually works has been an assumption
since the gate shipped. Every turn here is scanned for a rejected save followed
by a second attempt, and the lane reports how often one instructive rejection
was enough to reach a valid save. If this number is low, the rejection TEXT is
the thing to fix, not the model.

ISOLATION
---------
Real turns write to a real graph, so this points `knowledge_graph.graph_path` at
a temp file seeded from the fictional persona fixture. Nothing touches the
user's own `data/knowledge_graph.json`, and no real personal data is used.

COST
----
Real model tokens: one full turn per case. Deliberate lane, never part of the
free CI set.

    $env:PYTHONPATH="."; .venv\\Scripts\\python.exe -m evals.runner --memory-write-live
    ... --memory-write-live --limit 4      # smoke a few cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.infrastructure.repositories.persona_store import PERSONA_OVERLAY_ENV

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET = PROJECT_ROOT / "evals" / "datasets" / "memory_write_live.jsonl"
FIXTURE = PROJECT_ROOT / "evals" / "fixtures" / "memory_recall_persona.md"
RUNS_DIR = PROJECT_ROOT / "evals" / "runs"

# Substrings that mark a gate rejection in the tool result. These are the gate's
# own words (see MemoryWriteGate.summary / validate) — if they change there,
# repair@2 silently reads 0 and this list has to change with them.
_REJECTION_MARKERS = (
    "not saved",
    "too long",
    "too short",
    "this is a task",
    "starts with a pronoun",
    "this is a question",
    "no readable content",
)


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    detail: str
    saves: list[str] = field(default_factory=list)
    forgets: list[str] = field(default_factory=list)
    asked: bool = False
    rejected_then_retried: Optional[bool] = None
    duration_s: float = 0.0
    error: Optional[str] = None
    # Diagnostics, so a FAIL is actionable from the artifact alone. "No save
    # call" means nothing on its own: it could be the model declining to save,
    # or the tool never having been bound to the turn. These two fields tell
    # those apart without re-running.
    tools_called: list[str] = field(default_factory=list)
    response_snippet: str = ""
    # Set when the turn never really ran (model/provider error, context blowout).
    # Voids are EXCLUDED from the pass rate rather than counted as failures: a
    # turn that errored says nothing about whether the model would have saved,
    # and scoring it as "didn't save" invents a finding. This is the repo's
    # existing pass/fail/void eval contract.
    void_reason: Optional[str] = None


def load_cases(limit: int = 0, only: str = "") -> list[dict]:
    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("//")
    ]
    if only:
        # Repeated 26B<->9B model swapping can take the whole process down mid-run
        # on a tight VRAM budget (the #592 failure class), so being able to resume
        # at the cases that didn't get scored matters more here than in a lane
        # that finishes in seconds.
        wanted = {c.strip() for c in only.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    return cases[:limit] if limit else cases


def _memory_calls(trace: dict) -> list[dict]:
    return [c for c in trace.get("tool_calls", []) if c.get("name") == "manage_user_memory"]


def _calls_by_action(trace: dict, action: str) -> list[str]:
    out = []
    for call in _memory_calls(trace):
        params = call.get("params") or {}
        if (params.get("action") or "").lower() == action:
            out.append(str(params.get("content") or params.get("query") or ""))
    return out


def _asked_for_consent(trace: dict) -> bool:
    """Did the turn put the decision back to the user rather than acting alone?

    Either the dedicated tool, or a question in the reply — the policy says
    "offer", and a model that offers in prose is complying with it.
    """
    if any(c.get("name") == "ask_user_question" for c in trace.get("tool_calls", [])):
        return True
    response = (trace.get("response") or "").strip()
    return "?" in response


def _repair_signal(trace: dict) -> Optional[bool]:
    """True/False if a save was rejected then retried; None if nothing was rejected.

    None (not False) when there was no rejection at all: a turn that never got
    corrected is not evidence about recovery either way, and averaging it in as a
    failure would understate repair@2.
    """
    results = [r for r in trace.get("tool_results", []) if r.get("name") == "manage_user_memory"]
    rejected_at = None
    for index, result in enumerate(results):
        text = (result.get("result") or "").lower()
        if any(marker in text for marker in _REJECTION_MARKERS):
            rejected_at = index
            break
    if rejected_at is None:
        return None
    # Any LATER memory result that stored something is a successful repair.
    for result in results[rejected_at + 1:]:
        text = (result.get("result") or "").lower()
        if "remembered" in text or "invalidated" in text:
            return True
    return False


# The orchestrator's user-facing fallback when generation failed. It arrives as a
# normal reply, so without matching it an infra failure looks exactly like the
# model choosing not to save — which is the wrong conclusion to draw.
_VOID_REPLY_MARKERS = (
    "i hit an error generating a response",
    "context-length limit",
    "model/provider failure",
)


def _void_reason(trace: dict) -> Optional[str]:
    """Did this turn fail to run, as opposed to running and behaving badly?"""
    if trace.get("error"):
        return f"turn error: {trace['error']}"
    reply = (trace.get("response") or "").lower()
    if any(marker in reply for marker in _VOID_REPLY_MARKERS):
        return "model/provider error surfaced as the reply"
    if not (trace.get("response") or "").strip() and not trace.get("tool_calls"):
        return "empty reply and no tool calls"
    return None


def check(case: dict, trace: dict) -> tuple[bool, str]:
    """Return (passed, human-readable detail)."""
    saves = _calls_by_action(trace, "save")
    forgets = _calls_by_action(trace, "forget")
    category = case["category"]

    if category == "should_save":
        if not saves:
            return False, "no save call — the fact was dropped"
        blob = " ".join(saves).lower()
        missing = [k for k in case.get("expect_content_any", []) if k.lower() not in blob]
        if missing and len(missing) == len(case.get("expect_content_any", [])):
            return False, f"saved {saves!r} but none of {case['expect_content_any']} survived"
        return True, f"saved {saves!r}"

    if category == "should_not_save":
        if saves:
            return False, f"saved {saves!r} — should have stored nothing"
        return True, "stored nothing, correctly"

    if category == "supersede":
        if forgets:
            return True, f"forgot {forgets!r}"
        if saves:
            # #656 expires a single-valued edge automatically, so a bare save is
            # a correct outcome here as long as the new value was stored.
            return True, f"saved correction {saves!r} (supersession handled by the gate)"
        return False, "neither forgot nor saved — the correction was lost"

    if category == "tier1_ask_first":
        if saves:
            return False, f"silently saved sensitive info {saves!r} without asking"
        if _asked_for_consent(trace):
            return True, "offered instead of saving"
        return True, "did not save (no explicit offer, but consent was not bypassed)"

    return False, f"unknown category {category!r}"


def _write_partial(results: list[CaseResult], total: int) -> None:
    """Flush a partial report after every case.

    A real turn costs ~1-5 minutes here, so a full run is tens of minutes, and
    stdout is buffered when this is piped or run in the background. Without an
    incremental artifact on disk there is no way to tell a slow run from a hung
    one — which cost a debugging pass the first time it was run.
    """
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        header = f"<!-- in progress: {len(results)}/{total} cases -->\n\n"
        (RUNS_DIR / "memory_write_live_latest.md").write_text(
            header + render_report(results), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 — progress reporting must never fail a run
        pass


async def run(limit: int = 0, verbose: bool = False, only: str = "") -> list[CaseResult]:
    from src.core.intelligence import graph_rag_client as grc
    from src.core.intelligence import graph_always_on
    from src.core.intelligence.personal_knowledge_parse import parse_personal_knowledge
    from src.shared.config_manager import ConfigManager
    from evals.inprocess import (
        build_inprocess_session, run_turn_with, shutdown_inprocess_session,
    )

    parsed = parse_personal_knowledge(FIXTURE.read_text(encoding="utf-8"))
    tmp_dir = tempfile.TemporaryDirectory()

    # Swapping the knowledge graph for the fixture is NOT enough on its own: the persona
    # overlay (config/persona/identity.md, lessons.md) carries the real user's name into
    # every system prompt, so the model resolved these fictional first-person prompts to
    # the real person and saved e.g. "<real name> does not do video calls before 10am".
    # Point the overlay at an empty dir so the generic, git-tracked baseline is used and
    # identity comes only from the fixture graph. Set BEFORE the core is built — the
    # prompt registry loads fragments during warmup.
    persona_dir = Path(tmp_dir.name) / "persona_overlay"
    persona_dir.mkdir(parents=True, exist_ok=True)
    prior_persona_env = os.environ.get(PERSONA_OVERLAY_ENV)
    os.environ[PERSONA_OVERLAY_ENV] = str(persona_dir)

    real_get = ConfigManager.get
    original_loader = grc._load_personal_knowledge_seed
    results: list[CaseResult] = []
    container = None

    try:
        cases = load_cases(limit, only)

        # Point the graph at a temp file BEFORE the core is built: the container
        # constructs a GraphRAGClient, and a client built against the real path
        # would keep writing there for the whole run.
        base_overrides = {
            "knowledge_graph.graph_path": str(Path(tmp_dir.name) / "graph_0.json"),
            "knowledge_graph.ego_node_id": "Person:Alex",
        }
        ConfigManager.get = staticmethod(  # type: ignore[method-assign]
            lambda key, default=None: base_overrides.get(key, real_get(key, default))
        )
        grc._load_personal_knowledge_seed = lambda: parsed  # type: ignore[assignment]
        grc.SEED_FACTS, grc.SEED_ENTITIES, grc.SEED_EDGES = (
            parsed.facts, parsed.entities, parsed.edges,
        )
        grc.SEED_CORE_PROFILE, grc.SEED_BEHAVIOR = parsed.core_profile, parsed.behavior
        graph_always_on.reset_cache()

        # Built ONCE. Rebuilding per case spawned the whole MCP fleet and
        # re-preloaded the supervisor model every time — ~12 node subprocesses and
        # one 26B load/evict cycle per case, which is what made the first version
        # of this lane die partway through a run.
        print("[setup] building core (once) ...", flush=True)
        container, warmup_err = await build_inprocess_session()
        if warmup_err:
            print(f"[setup] warmup warning: {warmup_err}", flush=True)

        for index, case in enumerate(cases, start=1):
            # A fresh graph per case: turns must not see each other's writes, or a
            # later "should_save" gets deduped against an earlier one and looks
            # like the model failing to save. The container is reused, so the
            # already-constructed GraphRAGClient is reset onto the new path too.
            base_overrides["knowledge_graph.graph_path"] = str(
                Path(tmp_dir.name) / f"graph_{index}.json"
            )
            # Every GraphRAGClient call site constructs a fresh client and reads
            # graph_path at initialize(), so re-pointing the override is enough —
            # nothing caches a client across turns.
            graph_always_on.reset_cache()

            print(f"[{index}/{len(cases)}] ({case['category']}) {case['id']} ...", flush=True)
            try:
                trace = await run_turn_with(
                    container, case["prompt"], category="agent", warmup_error=warmup_err,
                )
            except Exception as exc:  # noqa: BLE001
                results.append(CaseResult(
                    case["id"], case["category"], False,
                    f"turn raised {type(exc).__name__}", error=str(exc),
                ))
                _write_partial(results, len(cases))
                continue

            void = _void_reason(trace)
            passed, detail = (False, f"VOID — {void}") if void else check(case, trace)
            result = CaseResult(
                void_reason=void,
                case_id=case["id"],
                category=case["category"],
                passed=passed,
                detail=detail,
                saves=_calls_by_action(trace, "save"),
                forgets=_calls_by_action(trace, "forget"),
                asked=_asked_for_consent(trace),
                rejected_then_retried=_repair_signal(trace),
                duration_s=float(trace.get("duration_s") or 0.0),
                error=trace.get("error"),
                tools_called=[c.get("name") or "?" for c in trace.get("tool_calls", [])],
                response_snippet=(trace.get("response") or "")[:300],
            )
            results.append(result)
            _write_partial(results, len(cases))
            label = "VOID" if void else ("PASS" if passed else "FAIL")
            print(f"    {label} — {detail} ({result.duration_s}s)", flush=True)
            if verbose and trace.get("response"):
                print(f"    reply: {trace['response'][:200]}")

        return results
    finally:
        # Before restoring ConfigManager.get: teardown reads config, and leaving the MCP
        # fleet alive is what made consecutive runs of this lane progressively less
        # likely to finish (#675).
        if container is not None:
            await shutdown_inprocess_session(container)
        grc._load_personal_knowledge_seed = original_loader  # type: ignore[assignment]
        ConfigManager.get = real_get  # type: ignore[method-assign]
        if prior_persona_env is None:
            os.environ.pop(PERSONA_OVERLAY_ENV, None)
        else:
            os.environ[PERSONA_OVERLAY_ENV] = prior_persona_env
        tmp_dir.cleanup()


def render_report(results: list[CaseResult]) -> str:
    voids = [r for r in results if r.void_reason]
    scored = [r for r in results if not r.void_reason]
    total = len(scored)
    passed = sum(1 for r in scored if r.passed)

    by_cat: dict[str, list[bool]] = {}
    for r in scored:
        by_cat.setdefault(r.category, []).append(r.passed)

    # Tier 1 passes on "didn't save", but silently not offering either is weaker
    # compliance than offering, and averaging them together would overstate it.
    tier1 = [r for r in scored if r.category == "tier1_ask_first" and r.passed]
    tier1_line = ""
    if tier1:
        offered = sum(1 for r in tier1 if r.asked)
        tier1_line = (
            f"- tier-1 consent: **{offered}/{len(tier1)}** actually OFFERED to remember; "
            f"the other {len(tier1) - offered} merely declined to save without asking "
            "(not a consent violation, but not the behaviour the policy describes)."
        )

    repairs = [r.rejected_then_retried for r in scored if r.rejected_then_retried is not None]
    if repairs:
        repair_line = (
            f"**repair@2: {sum(repairs)}/{len(repairs)}** "
            f"({100 * sum(repairs) // len(repairs)}%) — saves that were rejected by the "
            "gate and then corrected within the same turn."
        )
    else:
        repair_line = (
            "**repair@2: n/a** — no save was rejected in this run, so the "
            "\"rejections are prompts\" premise went untested. Add a case that "
            "provokes a rejection to measure it."
        )

    lines = [
        "# memory write — LIVE behaviour lane (#660)",
        "",
        f"- scored cases: **{total}** | passed: **{passed}** | failed: **{total - passed}**"
        + (f" | **{len(voids)} void** (excluded)" if voids else ""),
        f"- {repair_line}",
    ]
    if tier1_line:
        lines.append(tier1_line)
    lines += [
        "",
        "| category | pass |",
        "|---|---|",
    ]
    for cat, oks in sorted(by_cat.items()):
        lines.append(f"| `{cat}` | {sum(oks)}/{len(oks)} |")
    lines.append("")

    if voids:
        lines.append(
            "> Void cases below did not produce a usable turn (model/provider error), "
            "so they measure nothing about save behaviour and are left out of the rate. "
            "Re-run them before reading anything into the result."
        )
        lines.append("")

    for r in results:
        status = "VOID" if r.void_reason else ("PASS" if r.passed else "FAIL")
        lines.append(f"## [{status}] `{r.case_id}` ({r.category})")
        lines.append(f"- {r.detail}")
        if r.rejected_then_retried is not None:
            lines.append(
                f"- gate rejected a save; repaired in-turn: **{r.rejected_then_retried}**"
            )
        if not r.passed:
            # `manage_user_memory` absent from a turn that called OTHER tools is a
            # model choice; absent from a turn that called nothing may be a
            # harness problem instead. Show both so the artifact says which.
            lines.append(f"- tools called: `{r.tools_called or 'none'}`")
            if r.response_snippet:
                lines.append(f"- replied: {r.response_snippet!r}")
        if r.void_reason:
            lines.append(f"- void: {r.void_reason}")
        if r.error:
            lines.append(f"- error: {r.error}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    parser.add_argument("--verbose", action="store_true", help="print each turn's reply")
    parser.add_argument("--only", default="", metavar="ID[,ID]",
                        help="run only these case ids (resume after a crash)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    results = asyncio.run(run(limit=args.limit, verbose=args.verbose, only=args.only))
    report = render_report(results)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "memory_write_live_latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    # Voids are excluded: an infra failure must not be reported as a
    # behaviour regression (nor mask one).
    return 0 if all(r.passed or r.void_reason for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
