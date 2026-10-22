"""Unit tests for the 'unlocked' signal: pure tool-sequence mining."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.intelligence.capability_patterns import (
    extract_tool_sequences, count_patterns, DEFAULT_IGNORE_TOOLS,
)


def _row(turn, tool, ts, outcome="success"):
    return {"turn_id": turn, "tool_name": tool, "created_at": ts, "outcome": outcome}


def test_extract_orders_by_timestamp_and_collapses_repeats():
    rows = [
        _row("t1", "fetch", "2026-06-01T00:00:02"),
        _row("t1", "web_search", "2026-06-01T00:00:01"),
        _row("t1", "fetch", "2026-06-01T00:00:03"),   # consecutive dup → collapsed
        _row("t1", "summarize", "2026-06-01T00:00:04"),
    ]
    seqs = extract_tool_sequences(rows)
    assert seqs["t1"] == ["web_search", "fetch", "summarize"]


def test_extract_drops_failures_and_ignored_tools():
    rows = [
        _row("t1", "web_search", "1"),
        _row("t1", "search_for_tool", "2"),            # ignored plumbing
        _row("t1", "broken_tool", "3", outcome="failure"),  # failed
        _row("t1", "summarize", "4"),
    ]
    seqs = extract_tool_sequences(rows)
    assert "search_for_tool" not in seqs["t1"]
    assert "broken_tool" not in seqs["t1"]
    assert seqs["t1"] == ["web_search", "summarize"]


def test_count_patterns_groups_by_toolset_across_turns():
    # Three turns share {web_search, fetch, summarize} (one in a different order);
    # one unrelated turn. Set-based grouping treats the reordered turn as the same.
    seqs = {
        "t1": ["web_search", "fetch", "summarize"],
        "t2": ["web_search", "fetch", "summarize"],
        "t3": ["web_search", "summarize", "fetch"],
        "t4": ["open_app", "type_text"],
    }
    patterns = count_patterns(seqs, min_tools=2)
    top = patterns[0]
    assert top.tools == frozenset({"web_search", "fetch", "summarize"})
    assert top.turn_count == 3
    assert len(patterns) == 2  # the research trio + the open_app/type_text pair


def test_count_patterns_respects_min_tools():
    seqs = {"t1": ["web_search"], "t2": ["web_search"]}  # single-tool turns
    assert count_patterns(seqs, min_tools=2) == []


def test_count_patterns_sorted_by_frequency():
    seqs = {
        "t1": ["a", "b"], "t2": ["a", "b"], "t3": ["a", "b"],
        "t4": ["c", "d"], "t5": ["c", "d"],
    }
    patterns = count_patterns(seqs, min_tools=2)
    assert [p.turn_count for p in patterns] == [3, 2]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
