"""Deterministic quality gate for personal-memory writes.

WHY THIS EXISTS
---------------
`manage_user_memory(save=...)` used to be a straight pass-through to
`GraphRAGClient.ingest()`: whatever string the model produced became a node.
No dedup, no length limit, no atomicity, no contradiction check. Every quality
property of the graph depended on a local model getting it right unprompted,
which is exactly the thing local models are worst at and the thing a harness is
best at.

DESIGN: HARNESS STRUCTURES, MODEL DECIDES
-----------------------------------------
The split of labour here is deliberate, and follows the "reason free, constrain
late" finding (arXiv:2605.26128 — hard schema decoding cost small models 43.5
points of executable accuracy on a tool-call analogue, while schema *validity*
stayed at 100%; the syntax holds and the content silently rots).

So the model is NOT asked to emit a rigid memory schema. It supplies a plain
sentence — the one thing it is reliably good at — and answers the genuinely
judgment-laden question: *is this worth remembering at all?*

Everything mechanical happens here, with no LLM call:
  normalize -> atomize -> validate -> dedup -> conflict-check

REJECTIONS ARE PROMPTS
----------------------
Every skip carries a reason written to be *read by the model mid-turn*, and to
say what to do instead. That is the adaptation channel: the gate does not need
the model to be trained on our schema, it needs the model to be able to recover
in one retry. Cheap on a local model, where attempts are free but context is not
— so reasons are short, concrete, and never repeated verbatim per item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# A durable personal fact is a sentence, not a paragraph. Anything longer is
# usually several facts glued together (which retrieval then truncates) or a
# transcript pasted into memory.
DEFAULT_MAX_FACT_CHARS = 200
DEFAULT_MIN_FACT_CHARS = 8

# Cosine similarity against the closest existing fact. Measured, not guessed:
# verbatim and paraphrased restatements scored 0.83-1.00, while genuinely new
# facts topped out at 0.43 — a wide, comfortable gap. Deliberately set nearer the
# duplicate floor than the midpoint: wrongly dropping a NEW fact loses information
# permanently, while wrongly keeping a near-duplicate just costs one node.
DEFAULT_DUPLICATE_THRESHOLD = 0.80
# Below duplicate but well above novel — "you may be restating something that has
# changed". Saved anyway, with the old fact surfaced so the model can forget it.
DEFAULT_CONFLICT_THRESHOLD = 0.55

# Task/transient language. These are things to DO, not things that are TRUE.
_TRANSIENT_PATTERNS = (
    r"^remind me\b",
    r"^remember to\b",
    r"^todo\b",
    r"^note to self\b",
    r"^schedule\b",
    r"\bfor (?:this|the current) (?:task|turn|conversation|session)\b",
    r"\bright now\b",
    r"\bfor now\b",
)

# A fact that opens with a bare pronoun is not self-contained: six months later
# nothing in the graph says who "he" was.
_LEADING_PRONOUN = re.compile(
    r"^(he|she|they|it|that|this|those|these|him|her|them)\b", re.IGNORECASE
)

_PREFIXES = re.compile(
    r"^(?:please\s+)?(?:remember|note|save|store|fyi|just so you know|keep in mind)"
    r"(?:\s+that)?\s*[,:]?\s+",
    re.IGNORECASE,
)


@dataclass
class Conflict:
    """A saved fact that appears to replace something already known.

    `structural` distinguishes the two detection mechanisms, because they demand
    different things of the model. An embedding near-miss is a *guess* -- the old
    fact is still live and the model has to decide whether to forget it. A
    structural hit (a single-valued typed relation, #656) is a *certainty*: the
    old edge has already been expired, and telling the model to go forget it
    would be wrong.
    """

    fact: str
    existing: str
    structural: bool = False


@dataclass
class WriteDecision:
    """Outcome of gating one save call. `accepted` is what should be written."""

    accepted: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.accepted)

    def summary(self) -> str:
        """Model-facing result text. Terse, concrete, actionable."""
        parts: list[str] = []
        if self.accepted:
            parts.append("Remembered:\n" + "\n".join(f"- {f}" for f in self.accepted))
        for conflict in self.conflicts:
            if conflict.structural:
                parts.append(
                    f'Saved "{conflict.fact}". This replaces "{conflict.existing}", '
                    "which has been marked as no longer current — nothing further "
                    "to do."
                )
            else:
                parts.append(
                    f'Saved "{conflict.fact}", but it may supersede an existing fact: '
                    f'"{conflict.existing}". If the old one is no longer true, call this '
                    "tool again with action='forget' and a description of the outdated fact."
                )
        if self.skipped:
            parts.append(
                "Not saved:\n"
                + "\n".join(f'- "{fact}" — {reason}' for fact, reason in self.skipped)
            )
        return "\n\n".join(parts) if parts else "Nothing to save."


class MemoryWriteGate:
    """Validates and de-duplicates candidate facts before they reach the graph."""

    def __init__(
        self,
        graph: Any,
        *,
        max_fact_chars: int = DEFAULT_MAX_FACT_CHARS,
        min_fact_chars: int = DEFAULT_MIN_FACT_CHARS,
        duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
        conflict_threshold: float = DEFAULT_CONFLICT_THRESHOLD,
    ) -> None:
        self._graph = graph
        self._max_chars = max_fact_chars
        self._min_chars = min_fact_chars
        self._duplicate_threshold = duplicate_threshold
        self._conflict_threshold = conflict_threshold

    @classmethod
    def from_config(cls, graph: Any) -> "MemoryWriteGate":
        from src.shared.config_manager import ConfigManager

        def _get(key: str, default):
            value = ConfigManager.get(f"knowledge_graph.write.{key}", default)
            return default if value is None else value

        return cls(
            graph,
            max_fact_chars=int(_get("max_fact_chars", DEFAULT_MAX_FACT_CHARS)),
            min_fact_chars=int(_get("min_fact_chars", DEFAULT_MIN_FACT_CHARS)),
            duplicate_threshold=float(_get("duplicate_threshold", DEFAULT_DUPLICATE_THRESHOLD)),
            conflict_threshold=float(_get("conflict_threshold", DEFAULT_CONFLICT_THRESHOLD)),
        )

    # ------------------------------------------------------------------
    # Stage 1-2: normalize + atomize (pure, no graph access)
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(text: str) -> str:
        """Strip the conversational wrapper the model tends to include."""
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        # "Please remember that Sam's birthday is in June" -> "Sam's birthday is in June"
        while True:
            stripped = _PREFIXES.sub("", cleaned)
            if stripped == cleaned:
                break
            cleaned = stripped
        return cleaned.strip().strip("\"'").strip()

    def atomize(self, text: str) -> list[str]:
        """Split a multi-fact save into one fact per item.

        Models save "Sam is my partner and their birthday is June 3" as one node.
        Retrieval then returns it as a unit and truncates it, so half the content
        is unreachable — the same failure the seed-fact split fixed on the read
        side. Newlines and bullets are unambiguous separators; sentences are
        split only when the result is clearly several standalone statements.
        """
        raw_parts: list[str] = []
        for line in (text or "").splitlines():
            line = line.strip().lstrip("-*•").strip()
            if line:
                raw_parts.append(line)

        atoms: list[str] = []
        for part in raw_parts:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", part) if s.strip()]
            atoms.extend(sentences if len(sentences) > 1 else [part])

        return [a for a in (self.normalize(a) for a in atoms) if a]

    # ------------------------------------------------------------------
    # Stage 3: per-fact validation (pure)
    # ------------------------------------------------------------------

    def validate(self, fact: str) -> Optional[str]:
        """Return a rejection reason, or None if the fact is acceptable."""
        if len(fact) < self._min_chars:
            return "too short to be a useful fact on its own."
        if len(fact) > self._max_chars:
            return (
                f"too long ({len(fact)} chars, limit {self._max_chars}). "
                "Save one short fact per call instead of a paragraph."
            )
        if fact.rstrip().endswith("?"):
            return "this is a question, not a fact about the user."
        if not re.search(r"[A-Za-z]{3}", fact):
            return "no readable content."
        for pattern in _TRANSIENT_PATTERNS:
            if re.search(pattern, fact, re.IGNORECASE):
                return (
                    "this is a task or a temporary detail, not a durable fact. "
                    "Memory is for things that stay true."
                )
        if _LEADING_PRONOUN.match(fact):
            return (
                "starts with a pronoun, so it won't make sense on its own later. "
                "Name the person or thing explicitly."
            )
        return None

    # ------------------------------------------------------------------
    # Stage 4-5: dedup + conflict (needs the graph)
    # ------------------------------------------------------------------

    async def evaluate(self, content: str) -> WriteDecision:
        """Full pipeline. Returns what to write and what to tell the model."""
        decision = WriteDecision()
        atoms = self.atomize(content)
        if not atoms:
            decision.skipped.append((content or "", "empty after cleanup."))
            return decision

        for fact in atoms:
            reason = self.validate(fact)
            if reason:
                decision.skipped.append((fact, reason))
                continue

            score, existing = await self._closest(fact)
            if existing and score >= self._duplicate_threshold:
                decision.skipped.append((fact, f'already known — "{existing}".'))
                continue

            decision.accepted.append(fact)

            # Structural first: a typed single-valued relation KNOWS it replaces
            # the old value, where the embedding only suspects it. Checked even
            # when the embedding already flagged, so the model gets the accurate
            # "already handled" message rather than a spurious forget instruction.
            superseded = await self._superseded_value(fact)
            if superseded:
                decision.conflicts.append(Conflict(fact, superseded, structural=True))
            elif existing and score >= self._conflict_threshold:
                decision.conflicts.append(Conflict(fact, existing))

        # Two near-identical atoms in the SAME call would both pass the graph
        # check (neither is written yet), so de-dup within the batch too.
        deduped: list[str] = []
        for fact in decision.accepted:
            if fact.lower() not in {d.lower() for d in deduped}:
                deduped.append(fact)
        decision.accepted = deduped

        return decision

    async def _closest(self, fact: str) -> tuple[float, Optional[str]]:
        """Nearest existing fact, or (0.0, None) if that can't be determined.

        Dedup is an optimization, never a gate on writing: an embedding outage or
        an unexpected return shape must degrade to "save it" rather than silently
        swallowing the user's fact. The unpack is inside the guard for that reason.
        """
        try:
            score, existing = await self._graph.max_similarity(fact)
            return float(score), existing
        except Exception:
            return 0.0, None

    async def _superseded_value(self, fact: str) -> Optional[str]:
        """The live typed value this fact replaces, if any (#656).

        Same degrade-to-silence contract as `_closest`: supersession detection is
        an enrichment of the result text, never a gate on writing, so a graph
        that doesn't implement it (or throws) must not block the save.
        """
        try:
            return await self._graph.describe_supersession(fact)
        except Exception:
            return None
