"""Unit tests for the memory write gate.

The gate is what stops `manage_user_memory(save=...)` from being a straight
pass-through into the graph. Ranking behaviour is covered by
evals/memory_write_probe.py against a real embedding model; these cover the pure
logic (normalize / atomize / validate) plus the tool wiring, with no model.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.intelligence.memory_write_gate import MemoryWriteGate


class FakeGraph:
    """Stands in for GraphRAGClient: fixed similarity, records what was written."""

    def __init__(self, similarity: float = 0.0, closest: str | None = None):
        self._similarity = similarity
        self._closest = closest
        self.ingested: list[str] = []

    async def max_similarity(self, text: str):
        return self._similarity, self._closest

    async def ingest(self, content: str, source: str = "manual", topic: str = "") -> bool:
        self.ingested.append(content)
        return True

    async def initialize(self) -> bool:
        return True


def gate(similarity: float = 0.0, closest: str | None = None) -> MemoryWriteGate:
    return MemoryWriteGate(FakeGraph(similarity, closest))


# ---------------------------------------------------------------------------
# normalize / atomize
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Please remember that the code is 4417.", "the code is 4417."),
        ("Remember: Sam likes ramen.", "Sam likes ramen."),
        ("Note, Sam likes ramen.", "Sam likes ramen."),
        ("FYI Sam likes ramen.", "Sam likes ramen."),
        ("Just so you know, Sam likes ramen.", "Sam likes ramen."),
        ("  Sam   likes    ramen.  ", "Sam likes ramen."),
        ("Note that remember that Sam likes ramen.", "Sam likes ramen."),
    ],
)
def test_normalize_strips_conversational_wrapper(raw, expected):
    assert MemoryWriteGate.normalize(raw) == expected


def test_atomize_splits_sentences():
    assert gate().atomize("Sam likes ramen. Alex prefers mornings.") == [
        "Sam likes ramen.",
        "Alex prefers mornings.",
    ]


def test_atomize_splits_bullets_and_newlines():
    assert gate().atomize("- Sam likes ramen\n- Alex prefers mornings") == [
        "Sam likes ramen",
        "Alex prefers mornings",
    ]


def test_atomize_keeps_a_single_fact_whole():
    """Abbreviations and decimals must not be shredded into fragments."""
    assert gate().atomize("Alex works at Redwing Marine Inc. in Portland.") == [
        "Alex works at Redwing Marine Inc. in Portland."
    ]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fact,expected_reason",
    [
        ("Remind me to call Sam tomorrow.", "task"),
        ("TODO: book the photographer.", "task"),
        ("Alex is on the report right now.", "task"),
        ("They really liked it.", "pronoun"),
        ("It was great.", "pronoun"),
        ("What is Alex's favorite food?", "question"),
        ("hi", "too short"),
        ("x" * 250, "too long"),
    ],
)
def test_validate_rejects_bad_facts(fact, expected_reason):
    reason = gate().validate(fact)
    assert reason and expected_reason in reason


@pytest.mark.parametrize(
    "fact",
    [
        "Sam's birthday is June 3.",
        "Alex prefers morning meetings.",
        "The garage door code is 4417.",
        "Alex is allergic to shellfish.",
    ],
)
def test_validate_accepts_good_facts(fact):
    assert gate().validate(fact) is None


def test_rejection_reasons_say_what_to_do_instead():
    """The reason text is read by the model mid-turn — it has to be actionable."""
    assert "Name the person" in gate().validate("They liked it.")
    assert "one short fact per call" in gate().validate("x" * 250).lower()


# ---------------------------------------------------------------------------
# evaluate (dedup / conflict)
# ---------------------------------------------------------------------------

def test_duplicate_is_skipped_with_the_existing_fact_quoted():
    decision = asyncio.run(
        gate(similarity=0.95, closest="Father is named Marcus.").evaluate(
            "Alex's dad is called Marcus."
        )
    )
    assert decision.accepted == []
    assert "already known" in decision.skipped[0][1]
    assert "Father is named Marcus." in decision.skipped[0][1]


def test_near_match_is_saved_but_flagged_as_possible_supersession():
    decision = asyncio.run(
        gate(similarity=0.70, closest="Favorite foods are green curry.").evaluate(
            "Favorite foods are ramen."
        )
    )
    assert decision.accepted == ["Favorite foods are ramen."]
    assert decision.conflicts
    assert "forget" in decision.summary()


def test_novel_fact_is_saved_without_a_flag():
    decision = asyncio.run(gate(similarity=0.3, closest="Something else.").evaluate(
        "The garage door code is 4417."
    ))
    assert decision.accepted == ["The garage door code is 4417."]
    assert not decision.conflicts


def test_duplicates_within_one_call_collapse():
    decision = asyncio.run(gate().evaluate("The code is 4417.\nThe code is 4417."))
    assert decision.accepted == ["The code is 4417."]


def test_mixed_batch_saves_the_good_and_explains_the_rest():
    decision = asyncio.run(
        gate().evaluate("Alex is allergic to shellfish.\nRemind me to call Sam.")
    )
    assert decision.accepted == ["Alex is allergic to shellfish."]
    assert len(decision.skipped) == 1
    summary = decision.summary()
    assert "Remembered:" in summary and "Not saved:" in summary


def test_dedup_failure_never_blocks_a_save():
    """An embedding outage must not silently turn saves into no-ops."""

    class Broken(FakeGraph):
        async def max_similarity(self, text):
            raise RuntimeError("embeddings down")

    decision = asyncio.run(MemoryWriteGate(Broken()).evaluate("Sam's birthday is June 3."))
    assert decision.accepted == ["Sam's birthday is June 3."]


# ---------------------------------------------------------------------------
# tool wiring
# ---------------------------------------------------------------------------

def test_tool_writes_only_gated_facts(monkeypatch):
    from src.tools.system.utilities.manage_user_memory import ManageUserMemory

    fake = FakeGraph(similarity=0.0)
    tool = ManageUserMemory()
    monkeypatch.setattr(tool, "_get_graph", lambda: _async(fake))

    result = asyncio.run(
        tool.execute(action="save", content="Sam's birthday is June 3.\nRemind me to call Sam.")
    )
    assert fake.ingested == ["Sam's birthday is June 3."]
    assert result["saved_count"] == 1
    assert result["skipped_count"] == 1


def test_tool_reports_success_when_everything_was_already_known(monkeypatch):
    """A fully-deduped save is a correct outcome, not a failure to retry."""
    from src.tools.system.utilities.manage_user_memory import ManageUserMemory

    fake = FakeGraph(similarity=0.99, closest="Father is named Marcus.")
    tool = ManageUserMemory()
    monkeypatch.setattr(tool, "_get_graph", lambda: _async(fake))

    result = asyncio.run(tool.execute(action="save", content="Alex's dad is Marcus."))
    assert fake.ingested == []
    assert result["success"] is True
    assert result["saved_count"] == 0
    assert "already known" in result["result"]


async def _async(value):
    return value
