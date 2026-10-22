"""
lesson_gatekeeper.py — admission guards for self-authored Reflective Lessons.

The reflection judge (see evals/reflect_probe.py) proposes candidate lessons. This
gatekeeper decides which are safe to KEEP, before any of them are written into
Chad's persona. It is the anti-memory-poisoning layer from
docs/DESIGN_reflective-lessons.md.

Design principles:
  • DETERMINISTIC + EXPLAINABLE first. Every rejection carries a human-readable
    reason. No silent drops. (An optional LLM contradiction check is a separate,
    later hook — not required for the core guards.)
  • PURE. No DB, no registry import — the caller passes existing lessons (persona
    ## Learned Lessons, reflection review queue texts, and knowledge-graph fact
    summaries) plus the live tool-name set. Trivially unit-testable.
  • Admission-time only. Re-validating ALREADY-STORED lessons as they age (e.g. a
    tool gets deprecated AFTER its lesson was written) is a separate "lesson GC /
    refiner" pass; this module guards the WRITE, and the dead-tool check below
    catches the common case at admission time.

Guards, in order (first failure wins, so the reason is the most fundamental one):
  1. schema      — valid scope + non-empty lesson text
  2. confidence  — judge confidence >= floor
  3. suppression — not a "stop asking/clarifying" instruction with no resolving fact
  4. outage      — not a transient failure/outage description ("currently returning
                   401", "is down right now") frozen as permanent doctrine (#640) —
                   live failure state is handled by src/core/tools/failure.py, not lessons
  5. dead-tool   — lesson must not reference a snake_case tool name absent from the
                   live registry (e.g. a deprecated `deep_research`). This is the
                   concrete staleness guard.
  6. duplicate   — not a near-duplicate of an existing lesson, queue item, or graph
                   fact summary (token-overlap Jaccard)
  7. batch-dup   — not a near-duplicate of an earlier admitted candidate this cycle
Then a cap (max_per_cycle) keeps only the highest-confidence admits.

Admitted lessons are returned tagged `provisional` — the design graduates them to
`active` only after they survive a shadow period; that graduation is downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

VALID_SCOPES = {"user", "tools"}
# Detect tokens REFERENCED AS TOOLS so we can check them against the live registry.
# Naively flagging every snake_case token false-positives on PARAMETER names
# (device_name, query_text). Instead we only treat a snake_case identifier as a tool
# reference when it's used like one: after a tool verb/preposition (use/via/avoid…),
# in a function-call form `name(`, before the word "tool", or in `backticks`.
_SNAKE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
# Snake_case tokens that end in one of these are almost always PARAMETER names
# (device_name, query_text, file_path, max_tokens), not tool names. A live tool with
# such a suffix is in the registry anyway, so excluding them only suppresses
# false-positive "dead tool" rejections (e.g. "use device_name='Bedroom'").
_PARAM_SUFFIXES = ("_name", "_id", "_ids", "_text", "_path", "_url", "_uri", "_query",
                   "_key", "_type", "_level", "_tokens", "_token", "_count", "_limit",
                   "_content", "_value", "_format", "_mode", "_size")
_TOOL_REF_PATTERNS = [
    re.compile(r"\b(?:use|using|used|via|call|calling|invoke|invoking|avoid|avoiding|"
               r"prefer|preferring|run|running|with|through)\s+(?:the\s+)?(" + _SNAKE + r")"),
    re.compile(r"\b(" + _SNAKE + r")\s*\("),          # name(...) call form
    re.compile(r"\b(" + _SNAKE + r")\s+tool\b"),       # "the X tool"
    re.compile(r"`(" + _SNAKE + r")`"),                 # `name` in backticks
    # Subject position: "deep_research is unreliable", "X failed/returns/requires…".
    # A snake_case token acting as the actor is almost certainly a tool reference.
    re.compile(r"\b(" + _SNAKE + r")\s+(?:is|was|are|were|fails?|failed|failing|"
               r"returns?|returned|requires?|required|works?|worked|keeps|kept|"
               r"consistently|repeatedly|reliably|unreliabl|always|never)\b"),
]

# Behavior-SUPPRESSION anti-pattern: lessons that tell Chad to STOP asking/clarifying
# without supplying the resolving fact. Harmful because Chad then neither asks NOR
# knows → confident wrong guess. The fix is always to state the fact instead, so we
# reject these outright and force the judge to phrase lessons as knowledge.
_SUPPRESSION = re.compile(
    r"\b(?:do\s*n[o']?t|don't|never|stop|avoid|skip|no\s+need\s+to|without|"
    r"refrain\s+from)\b[^.]{0,40}\b(?:ask|asking|clarif|confirm|prompt|question)",
    re.IGNORECASE)

# Outage/transient-state anti-pattern (#640): a lesson should record a DURABLE fact
# ("web_search has a max of 10 queries per call"), never live failure state
# ("sandboxed tools are currently returning 401 Unauthorized"). The latter is real
# but temporary infra trouble — freezing it as permanent doctrine biases Chad against
# a tool/service indefinitely for a problem that may be fixed the same day. Live
# failure state is now handled properly by src/core/tools/failure.py (classifies
# auth/config failures and the sandbox preflight latches them for the turn), so
# there is no reason for a lesson to carry it.
_OUTAGE = re.compile(
    r"\b(?:currently|at\s+the\s+moment|right\s+now|as\s+of\s+now|for\s+now|"
    r"temporarily|until\s+further\s+notice)\b"
    r"|\b(?:is|are|was|were|seems?\s+to\s+be)\s+(?:currently\s+)?(?:down|unavailable|"
    r"offline|broken)\b"
    r"|\b(?:returning|returns?|throwing|throws?|raising|raises?)\s+(?:an?\s+)?"
    r"(?:\d{3}\b|error)"
    r"|\bhttp\s*\d{3}\b"
    r"|\b(?:401|403|404|429|500|502|503)\b",
    re.IGNORECASE,
)


def is_outage_shaped(lesson: str) -> bool:
    """True when `lesson` describes transient failure/outage state, not a durable fact.

    Used both by the admission gate below and by reflection.py's GC (`--prune`) pass,
    which re-scans already-written lessons for this same shape (the sandbox-401 lesson
    in #640 predates this guard and needed to be pruned by hand).
    """
    return bool(_OUTAGE.search(lesson or ""))


@dataclass
class GateOutcome:
    admitted: List[dict] = field(default_factory=list)   # each tagged status=provisional
    rejected: List[dict] = field(default_factory=list)   # {lesson, scope, reason}

    @property
    def summary(self) -> str:
        return f"{len(self.admitted)} admitted, {len(self.rejected)} rejected"


def _norm_tokens(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class LessonGatekeeper:
    def __init__(self, *, confidence_floor: float = 0.7, max_per_cycle: int = 5,
                 dup_threshold: float = 0.6,
                 available_tools: Optional[Iterable[str]] = None,
                 existing_lessons: Optional[Iterable[str]] = None):
        self.confidence_floor = confidence_floor
        self.max_per_cycle = max_per_cycle
        self.dup_threshold = dup_threshold
        self.available_tools: Set[str] = set(available_tools or [])
        self.existing_lessons: List[str] = list(existing_lessons or [])
        self._existing_tok = [_norm_tokens(l) for l in self.existing_lessons]

    def _dead_tool(self, *parts: str) -> Optional[str]:
        """Return the first TOOL-REFERENCED token that isn't in the live registry.

        Only tokens used like a tool (call form, after a tool verb, before 'tool', or
        backticked) are considered — so parameter names like device_name are not
        mistaken for deprecated tools.
        """
        if not self.available_tools:
            return None  # can't judge without a registry snapshot — skip this guard
        for part in parts:
            low = (part or "").lower()
            for pat in _TOOL_REF_PATTERNS:
                for tok in pat.findall(low):
                    if tok.endswith(_PARAM_SUFFIXES):
                        continue  # parameter name, not a tool — don't flag
                    if tok not in self.available_tools:
                        return tok
        return None

    def filter(self, candidates: List[dict]) -> GateOutcome:
        out = GateOutcome()
        admitted_tok: List[Set[str]] = []

        # Highest-confidence first so the cap keeps the best, and so a strong lesson
        # wins over a weaker near-duplicate of it within the same batch.
        ordered = sorted(candidates or [],
                         key=lambda c: float(c.get("confidence", 0.0) or 0.0), reverse=True)

        for c in ordered:
            scope = (c.get("scope") or "").strip().lower()
            lesson = (c.get("lesson") or "").strip()
            conf = float(c.get("confidence", 0.0) or 0.0)

            def reject(reason: str):
                out.rejected.append({"lesson": lesson, "scope": scope, "reason": reason})

            if scope not in VALID_SCOPES or not lesson:
                reject(f"invalid schema (scope={scope!r}, empty={not lesson})")
                continue
            if conf < self.confidence_floor:
                reject(f"confidence {conf:.2f} < floor {self.confidence_floor:.2f}")
                continue
            if _SUPPRESSION.search(lesson):
                reject("behavior-suppression lesson (tells Chad to stop asking/clarifying "
                       "without supplying the resolving fact) — state the fact instead")
                continue
            if is_outage_shaped(lesson):
                reject("outage-shaped lesson (describes transient failure/HTTP-status state, "
                       "not a durable fact) — live failures are handled by "
                       "src/core/tools/failure.py, not lessons")
                continue
            # Only the LESSON text is injected into the prompt; evidence is free-form
            # provenance where the judge often paraphrases tool names loosely. Scanning
            # evidence false-rejected good lessons (e.g. a `user` fact whose evidence
            # named an approximate tool), so the dead-tool guard checks lesson text only.
            dead = self._dead_tool(lesson)
            if dead:
                reject(f"references tool not in registry (deprecated/unknown): {dead!r}")
                continue

            tok = _norm_tokens(lesson)
            dup_existing = max((_jaccard(tok, e) for e in self._existing_tok), default=0.0)
            if dup_existing >= self.dup_threshold:
                reject(f"duplicate of an existing lesson (overlap {dup_existing:.2f})")
                continue
            dup_batch = max((_jaccard(tok, a) for a in admitted_tok), default=0.0)
            if dup_batch >= self.dup_threshold:
                reject(f"duplicate of another candidate this cycle (overlap {dup_batch:.2f})")
                continue

            if len(out.admitted) >= self.max_per_cycle:
                reject(f"over per-cycle cap ({self.max_per_cycle})")
                continue

            out.admitted.append({**c, "scope": scope, "lesson": lesson,
                                 "status": "provisional"})
            admitted_tok.append(tok)

        return out
