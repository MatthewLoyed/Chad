"""Unit tests for eval live-lane reasoning chunk capture."""
from evals.live import LiveTrace


def test_reasoning_by_agent_groups_chunks():
    trace = LiveTrace(prompt="x", category="planning")
    trace.reasoning_chunks = [
        {"agent": "Scout", "state": "reasoning", "text": "find readme", "seq": 0},
        {"agent": "Scout", "state": "reasoning", "text": " report facts", "seq": 1},
        {"agent": "Planner", "state": "thinking", "text": "step 1 read", "seq": 2},
    ]
    grouped = trace.reasoning_by_agent
    assert grouped["Scout"] == "find readme report facts"
    assert grouped["Planner"] == "step 1 read"
