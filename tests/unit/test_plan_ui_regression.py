"""UI protocol and turn-coordinator regression tests for plan rendering."""
import inspect

from src.shared.ui_protocol import ACTIVITY_TYPE_PLAN, ACTIVITY_TYPE_TOOLS, build_activity_plan_snapshot, build_activity_tools_snapshot, build_run_started


def test_activity_plan_snapshot_ag_ui_shape():
    snap = build_activity_plan_snapshot(
        message_id="turn_3_123",
        plan=[{"id": "s1", "description": "Step", "tools": [], "status": "pending"}],
        goal="Do the thing",
        replace=True,
    )
    assert snap["type"] == "activity_snapshot"
    assert snap["activity_type"] == ACTIVITY_TYPE_PLAN
    assert snap["message_id"] == "turn_3_123"
    assert snap["replace"] is True
    assert snap["content"]["goal"] == "Do the thing"
    assert len(snap["content"]["steps"]) == 1


def test_activity_tools_snapshot_shape():
    snap = build_activity_tools_snapshot(
        message_id="turn_5_tools",
        tools=["web_search", "read_file"],
        shortlist_size=14,
        selection_path="hybrid",
    )
    assert snap["type"] == "activity_snapshot"
    assert snap["activity_type"] == ACTIVITY_TYPE_TOOLS
    assert snap["content"]["tools"] == ["web_search", "read_file"]
    assert snap["content"]["shortlist_size"] == 14
    assert snap["content"]["selection_path"] == "hybrid"


def test_run_started_wire_event():
    evt = build_run_started(run_id="turn_1", thread_id="thread-abc")
    assert evt["type"] == "run_started"
    assert evt["run_id"] == "turn_1"
    assert evt["thread_id"] == "thread-abc"


def test_turn_coordinator_does_not_republish_execution_plan_after_extract():
    """Post-turn plan extraction must sanitize text only — not emit a second ExecutionPlanEvent."""
    # The turn body moved to TurnPipeline; scan the whole class so the guard keeps
    # covering it no matter which phase method the extraction lands in.
    from src.core.orchestration.turn.pipeline import TurnPipeline

    source = inspect.getsource(TurnPipeline)
    assert "EventBus.get_instance().publish(ExecutionPlanEvent" not in source
