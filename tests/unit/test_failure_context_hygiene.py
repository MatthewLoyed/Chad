"""Context hygiene for repeated failures.

Attempts are free on local inference; context is not. These lock the behaviour
that lets a model try many times without losing sight of the task.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core.tools import failure
from src.core.orchestration.nodes.supervisor_node import _collapse_repeated_failures
from src.core.orchestration.nodes.tool_node import _truncate_large_tool_outputs


def _failed_msg(tool: str, detail: str, call_id: str) -> ToolMessage:
    payload = failure.failure(detail, tool_name=tool)
    return ToolMessage(
        content="[TOOL_DATA]\n" + json.dumps(payload), tool_call_id=call_id, name=tool
    )


def test_repeated_failures_collapse_to_a_counter():
    msgs = [HumanMessage(content="do the thing")]
    for i in range(5):
        msgs.append(AIMessage(content=f"try {i}"))
        msgs.append(_failed_msg("web_search", f"timed out after {60 + i}s", f"c{i}"))

    out = _collapse_repeated_failures(msgs)

    assert len(out) == len(msgs), "collapsing replaces messages, never drops them"
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    full = [m for m in tool_msgs if "NEXT_ACTIONS" in m.content]
    collapsed = [m for m in tool_msgs if "attempt" in m.content and "of 5" in m.content]

    # First and last kept in full; the three in between become one-liners.
    assert len(full) == 2
    assert len(collapsed) == 3
    assert "change approach" in collapsed[0].content


def test_collapse_actually_reclaims_context():
    msgs = []
    for i in range(6):
        msgs.append(_failed_msg("run_sandboxed_bash", "401 unauthorized", f"c{i}"))

    before = sum(len(m.content) for m in msgs)
    after = sum(len(m.content) for m in _collapse_repeated_failures(msgs))
    assert after < before * 0.6


def test_two_failures_are_left_alone():
    """Two attempts is normal recovery, not a loop — nothing to collapse."""
    msgs = [_failed_msg("web_search", "timed out", "a"), _failed_msg("web_search", "timed out", "b")]
    assert _collapse_repeated_failures(msgs) is msgs


def test_different_failures_are_not_collapsed_together():
    msgs = [
        _failed_msg("web_search", "timed out", "a"),
        _failed_msg("read_file", "404 not found", "b"),
        _failed_msg("gmail", "401 unauthorized", "c"),
    ]
    assert _collapse_repeated_failures(msgs) is msgs


def test_successful_output_is_never_collapsed():
    msgs = [
        ToolMessage(content="[TOOL_DATA]\nresult one", tool_call_id="a", name="web_search"),
        ToolMessage(content="[TOOL_DATA]\nresult two", tool_call_id="b", name="web_search"),
        ToolMessage(content="[TOOL_DATA]\nresult three", tool_call_id="c", name="web_search"),
    ]
    assert _collapse_repeated_failures(msgs) is msgs


def test_collapse_does_not_mutate_graph_history():
    msgs = [_failed_msg("web_search", "timed out", f"c{i}") for i in range(4)]
    originals = [m.content for m in msgs]
    _collapse_repeated_failures(msgs)
    assert [m.content for m in msgs] == originals


def test_failed_output_truncates_tighter_than_successful_output():
    """A failure needs its class and next actions, not its stack trace."""
    big_failure = _failed_msg("run_sandboxed_python", "Traceback " + "x" * 50_000, "a")
    big_success = ToolMessage(
        content="[TOOL_DATA]\n" + "y" * 50_000, tool_call_id="b", name="web_search"
    )
    msgs = [big_failure, big_success]

    _truncate_large_tool_outputs(msgs)

    assert len(big_failure.content) < len(big_success.content)
    assert len(big_failure.content) <= failure.MAX_FAILED_OUTPUT_CHARS + 200


def test_truncated_failure_keeps_its_guidance():
    """Head-only truncation used to drop NEXT_ACTIONS — the only actionable part."""
    msg = _failed_msg("run_sandboxed_python", "Traceback " + "x" * 50_000, "a")
    _truncate_large_tool_outputs([msg])
    assert "TOOL_FAILED[" in msg.content
    assert "next_actions" in msg.content or "NEXT_ACTIONS" in msg.content


def test_browser_snapshot_gets_larger_budget():
    """browser_snapshot gets a generous context budget (24k) to avoid severing SPAs."""
    # 20k characters: exceeds generic tool cap (8k/16k), but within browser snapshot cap (24k)
    snapshot_msg = ToolMessage(
        content="### Interactive Elements (Set-of-Mark)\n[1] button 'OK' -> target='e1'\n\n### Pruned Snapshot\n" + "x" * 19_000,
        tool_call_id="snap1",
        name="browser_snapshot",
    )
    generic_msg = ToolMessage(
        content="generic data " + "y" * 19_000,
        tool_call_id="gen1",
        name="some_api_tool",
    )
    _truncate_large_tool_outputs([snapshot_msg, generic_msg])

    # Generic msg was truncated
    assert "[...truncated" in generic_msg.content
    # Snapshot msg was within the 24k budget and kept intact
    assert "[...truncated" not in snapshot_msg.content
    assert len(snapshot_msg.content) > len(generic_msg.content)


def test_browser_snapshot_truncation_preserves_set_of_mark():
    """If a snapshot exceeds even 24k, the Set-of-Mark menu is preserved intact."""
    som_block = (
        "### Interactive Elements (Set-of-Mark)\n"
        "Pick a target by its number, then pass its exact target=\"...\" value.\n\n"
        "[1] link \"Wireless Networks\"  ->  target=\"e45\"\n"
        "[2] button \"Upload PDF\"  ->  target=\"e88\"\n\n"
    )
    huge_yaml_body = "### Pruned Snapshot (500 lines)\n```yaml\n" + ("  - row item: lecture\n" * 2000) + "```"
    snapshot_msg = ToolMessage(
        content=som_block + huge_yaml_body,
        tool_call_id="snap2",
        name="browser_snapshot",
    )

    _truncate_large_tool_outputs([snapshot_msg])

    assert "[1] link \"Wireless Networks\"  ->  target=\"e45\"" in snapshot_msg.content
    assert "[2] button \"Upload PDF\"  ->  target=\"e88\"" in snapshot_msg.content
    assert "[...truncated" in snapshot_msg.content
    assert len(snapshot_msg.content) <= 26000
