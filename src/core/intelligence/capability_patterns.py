"""
capability_patterns.py — the "unlocked" self-improvement signal.

Counterpart to capability_gaps.py. Where the gap signal fires when Chad LACKS a tool,
the pattern signal fires when Chad HAS the tools but keeps stitching the same multi-tool
sequence together by hand — a recurring procedure that would be cleaner as one skill
(e.g. web_search → fetch → summarize, run on every research question). This is the
SkillWeaver idea: mine successful trajectories, propose a reusable skill.

Pipeline (reuses capability_gaps' gatekeeper + persistence + event so suggestions land
in the SAME side-channel, never in active chat):

  1. SOURCE   — `tool_outcome_log` (already written on every tool call). Successful rows
                grouped by turn_id give each turn's tool sequence. No new logging.
  2. COUNT    — `count_patterns()` (pure) groups turns by their distinct tool-set and
                counts how many DISTINCT turns share it. Set-based grouping is recall-
                oriented (tolerates step-order jitter); the judge sees real sequences.
  3. DRAFT    — once a pattern recurs across >= min_occurrences turns, a judge drafts a
                SKILL suggestion. Repetition across turns is what makes it trustworthy.
  4. GATEKEEP — SuggestionGatekeeper rejects low-confidence / redundant drafts; we also
                skip patterns already drafted before (fingerprint stored in evidence) so
                a stable pattern isn't re-judged every cycle.
  5. SURFACE  — persist `pending` + publish CapabilitySuggestionRaised (shared helpers).

Pure counting logic is dependency-free and unit-tested; the analyzer injects the judge.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Tuple

from src.core.intelligence.capability_gaps import (
    SuggestionGatekeeper, existing_pending_titles, persist_and_publish,
)

logger = logging.getLogger("CapabilityPatterns")

# Tools that are plumbing, not part of a meaningful user-facing procedure. Including
# them would manufacture spurious "patterns" (every turn touches them).
DEFAULT_IGNORE_TOOLS = frozenset({
    "search_for_tool", "list_skills", "ask_user_question", "get_datetime",
})


# ── pure pattern mining ─────────────────────────────────────────────────────────


def extract_tool_sequences(rows: List[dict], *,
                           ignore: FrozenSet[str] = DEFAULT_IGNORE_TOOLS) -> Dict[str, List[str]]:
    """Group successful tool_outcome_log rows into per-turn ordered tool sequences.

    Rows may arrive in any order; each must have `turn_id`, `tool_name`, `outcome`, and
    `created_at`. Only successful calls count. Consecutive duplicates collapse (a retry
    of the same tool isn't a richer procedure). Ignored plumbing tools are dropped. Pure.
    """
    by_turn: Dict[str, List[Tuple[str, str]]] = {}
    for r in rows or []:
        if (r.get("outcome") or "") != "success":
            continue
        turn_id = r.get("turn_id")
        tool = r.get("tool_name")
        if not turn_id or not tool or tool in ignore:
            continue
        by_turn.setdefault(turn_id, []).append((r.get("created_at") or "", tool))

    sequences: Dict[str, List[str]] = {}
    for turn_id, pairs in by_turn.items():
        pairs.sort(key=lambda p: p[0])
        seq: List[str] = []
        for _, tool in pairs:
            if not seq or seq[-1] != tool:  # collapse consecutive repeats
                seq.append(tool)
        sequences[turn_id] = seq
    return sequences


@dataclass
class ToolPattern:
    tools: FrozenSet[str]               # the distinct tool-set that recurs
    turn_count: int                     # how many DISTINCT turns used it
    examples: List[List[str]]           # representative ordered sequences (for the judge)


def count_patterns(sequences: Dict[str, List[str]], *, min_tools: int = 2) -> List[ToolPattern]:
    """Count how many distinct turns share each multi-tool set. Pure + deterministic.

    Grouping is by the distinct tool-SET (frozenset), so "search→fetch→summarize" and
    "search→summarize→fetch" count as the same recurring procedure (the judge gets the
    real orderings via `examples`). Only sets of >= min_tools distinct tools qualify.
    Returns patterns sorted by turn_count desc.
    """
    groups: Dict[FrozenSet[str], dict] = {}
    for seq in sequences.values():
        distinct = frozenset(seq)
        if len(distinct) < min_tools:
            continue
        g = groups.setdefault(distinct, {"turns": 0, "examples": []})
        g["turns"] += 1
        if len(g["examples"]) < 5:
            g["examples"].append(list(seq))

    patterns = [ToolPattern(tools=fp, turn_count=g["turns"], examples=g["examples"])
                for fp, g in groups.items()]
    patterns.sort(key=lambda p: p.turn_count, reverse=True)
    return patterns


# ── analyzer ─────────────────────────────────────────────────────────────────

# Given the recurring tool-set + example sequences, draft a skill suggestion or None.
PatternDraftFn = Callable[[FrozenSet[str], List[List[str]]], Awaitable[Optional[dict]]]


@dataclass
class WorkflowPatternAnalyzer:
    draft_fn: PatternDraftFn
    capability_snapshot: List[str] = field(default_factory=list)  # "name: desc" of live tools/skills
    min_occurrences: int = 3
    min_tools: int = 2
    confidence_floor: float = 0.7
    lookback_limit: int = 500
    # Stage-6 extension point: objective execute-and-check before surfacing (mirrors
    # CapabilityGapAnalyzer.verify_fn). Unwired unless build_default_pattern_analyzer
    # injects it behind CHAD_CAPABILITY_VERIFY. Falsy return drops the candidate.
    verify_fn: Optional[Callable[[dict], Awaitable[bool]]] = None

    async def analyze(self) -> int:
        """Mine recent successful turns for recurring multi-tool procedures and surface
        skill suggestions for the strong ones. Returns the number surfaced."""
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        if not supabase or not supabase.enabled:
            return 0

        rows = await supabase.async_query(
            "tool_outcome_log", select="tool_name,turn_id,outcome,created_at",
            filters={"outcome": "eq.success"},
            order="created_at.desc", limit=self.lookback_limit) or []
        if not rows:
            return 0

        sequences = extract_tool_sequences(rows)
        patterns = [p for p in count_patterns(sequences, min_tools=self.min_tools)
                    if p.turn_count >= self.min_occurrences]
        if not patterns:
            return 0

        already = await self._already_suggested_fingerprints(supabase)
        candidates: List[dict] = []
        for p in patterns:
            if p.tools in already:
                continue  # drafted before — don't re-judge a stable pattern
            try:
                draft = await self.draft_fn(p.tools, p.examples)
            except Exception as e:
                logger.error(f"[ CAP_PATTERN ] draft_fn failed: {e}")
                draft = None
            already.add(p.tools)  # mark attempted regardless, to avoid re-judging
            if not draft:
                continue
            draft["kind"] = "skill"  # the unlocked signal always proposes a skill
            draft["cluster_size"] = p.turn_count
            # Evidence carries the fingerprint so future cycles can skip this pattern.
            draft["evidence"] = {"tools": sorted(p.tools), "examples": p.examples}
            candidates.append(draft)

        if not candidates:
            return 0

        existing = await existing_pending_titles(supabase)
        gate = SuggestionGatekeeper(
            confidence_floor=self.confidence_floor,
            existing_capabilities=self.capability_snapshot,
            existing_suggestions=existing)
        outcome = gate.filter(candidates)
        logger.info(f"[ CAP_PATTERN ] Suggestion gate: {outcome.summary}")
        for r in outcome.rejected:
            logger.info(f"[ CAP_PATTERN ] rejected {r['title']!r}: {r['reason']}")

        surfaced = 0
        for cand in outcome.admitted:
            if self.verify_fn:
                try:
                    if not await self.verify_fn(cand):
                        logger.info(f"[ CAP_PATTERN ] verify_fn rejected {cand.get('title')!r}")
                        continue
                except Exception as e:
                    logger.error(f"[ CAP_PATTERN ] verify_fn errored: {e}")
                    continue
            if await persist_and_publish(supabase, cand):
                surfaced += 1
        return surfaced

    async def _already_suggested_fingerprints(self, supabase) -> set:
        """Tool-sets we've already drafted a skill suggestion for (any status), read back
        from each suggestion's evidence — so a recurring pattern is judged at most once."""
        rows = await supabase.async_query(
            "capability_suggestions", select="evidence",
            filters={"kind": "eq.skill"}, limit=200) or []
        fps = set()
        for r in rows:
            ev = r.get("evidence") or {}
            tools = ev.get("tools") if isinstance(ev, dict) else None
            if tools:
                fps.add(frozenset(tools))
        return fps


# ── trigger throttle ────────────────────────────────────────────────────────────


class WorkflowPatternTracker:
    """Counts multi-tool turns and kicks off a background mining pass every N. Hooked
    into the orchestrator's turn finalize (fire-and-forget), so it needs no heartbeat.
    """

    _lock = asyncio.Lock()
    _counter = 0
    _is_analyzing = False
    _registry: Optional[object] = None

    @classmethod
    async def note_turn(cls, successful_tools: List[str], *, registry: Optional[object] = None,
                        min_tools: int = 2, target_count: int = 8):
        """Record a completed turn. Only turns with >= min_tools DISTINCT successful tools
        advance the counter (single-tool turns can't form a procedure)."""
        if registry is not None:
            cls._registry = registry
        if len(set(successful_tools)) < min_tools:
            return
        async with cls._lock:
            cls._counter += 1
            logger.info(f"[ CAP_PATTERN ] Multi-tool turns since last mining: "
                        f"{cls._counter}/{target_count}")
            if cls._counter >= target_count and not cls._is_analyzing:
                cls._counter = 0
                cls._is_analyzing = True
                asyncio.create_task(cls._run_in_background())

    @classmethod
    async def _run_in_background(cls):
        try:
            analyzer = build_default_pattern_analyzer(cls._registry)
            if analyzer:
                await analyzer.analyze()
        except Exception as e:
            logger.error(f"[ CAP_PATTERN ] Background mining failed: {e}", exc_info=True)
        finally:
            async with cls._lock:
                cls._is_analyzing = False


# ── default wiring (judge from the live system) ─────────────────────────────────

_DRAFT_SYSTEM = (
    "You are Chad's capability architect. Chad keeps performing the SAME multi-step tool "
    "procedure by hand across many separate turns. Decide whether wrapping it as ONE "
    "reusable SKILL (a specialized agent that runs the whole procedure) would genuinely "
    "save effort and reduce errors.\n"
    "Be conservative: if the steps are too varied to generalize, or the procedure is "
    "trivial, return null. Only propose a skill when the procedure is coherent and "
    "clearly repeatable.\n"
    "Respond with ONLY a JSON object: "
    '{"title": "snake_case_skill_name", "description": "what the skill does end-to-end in one sentence", '
    '"rationale": "why a skill beats doing the steps manually", "confidence": <float 0..1>}  '
    "or the literal null. No prose, no markdown fences."
)


def _build_judge_pattern_draft_fn(model: str) -> PatternDraftFn:
    async def _draft(tools: FrozenSet[str], examples: List[List[str]]) -> Optional[dict]:
        from evals.judges import build_judge_adapter, _extract_json_object
        adapter = build_judge_adapter(model)
        ex = "\n".join(" → ".join(seq) for seq in examples[:5])
        user = (f"RECURRING TOOL SET: {', '.join(sorted(tools))}\n"
                f"EXAMPLE SEQUENCES (one per past turn):\n{ex}")
        messages = [
            {"role": "system", "content": _DRAFT_SYSTEM},
            {"role": "user", "content": user},
        ]
        resp = await adapter.generate(messages=messages, temperature=0.0,
                                      thinking_level="low", max_tokens=1200)
        data = _extract_json_object(resp.text or "")
        if not isinstance(data, dict) or not data.get("title"):
            return None
        return data
    return _draft


def build_default_pattern_analyzer(registry: Optional[object]) -> Optional[WorkflowPatternAnalyzer]:
    """Construct an analyzer wired to a small judge and (if available) the live catalog
    so the gatekeeper can reject skills that duplicate existing tools/skills."""
    import os
    capability_snapshot: List[str] = []
    if registry is not None:
        try:
            for schema in registry.get_all_schemas():
                capability_snapshot.append(f"{schema.get('name','')}: {schema.get('description','')}")
        except Exception:
            pass
    model = os.getenv("CHAD_CAPABILITY_JUDGE_MODEL", "qwen/qwen3.5-9b")
    from src.core.intelligence.capability_verifier import build_capability_verify_fn
    return WorkflowPatternAnalyzer(
        draft_fn=_build_judge_pattern_draft_fn(model),
        capability_snapshot=capability_snapshot,
        verify_fn=build_capability_verify_fn())
