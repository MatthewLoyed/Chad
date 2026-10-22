"""Unit tests for the structured-output planner helpers and resilient step closing."""
import json
from src.core.orchestration.nodes.plan_parser import (
    _parse_plan,
    _resolve_step,
    _next_actionable_step,
    PLAN_SCHEMA,
)


def test_structured_happy_path():
    steps = _parse_plan(json.dumps({"steps": [
        {"id": "step_1", "description": "do x", "tools": [], "status": "pending"}
    ]}))
    assert steps and steps[0]["id"] == "step_1"


def test_bare_array():
    steps = _parse_plan(json.dumps([
        {"id": "s1", "description": "y", "tools": [], "status": "pending"}
    ]))
    assert steps and steps[0]["id"] == "s1"


def test_legacy_plan_markers_not_mangled():
    # The literal '[[' must NOT be mangled into nested arrays by json_repair.
    txt = ('Here is the plan.\n[[PLAN]]\n'
           '[{"id":"step_1","description":"z","tools":[],"status":"pending"}]\n[[/PLAN]]')
    steps = _parse_plan(txt)
    assert steps and steps[0]["id"] == "step_1"


def test_json_repair_recovers_trailing_comma():
    steps = _parse_plan(
        '{"steps":[{"id":"step_1","description":"w","tools":[],"status":"pending"},]}'
    )
    assert steps and steps[0]["id"] == "step_1"


def test_unparseable_returns_none():
    assert _parse_plan("total garbage no json here") is None


def test_wrong_step_id_falls_back_to_active():
    plan = [{"id": "step_1", "status": "running"}, {"id": "step_2", "status": "pending"}]
    resolved = _resolve_step(plan, "step_99", active_id="step_1")
    assert resolved["id"] == "step_1"


def test_resolve_step_final_fallback_to_first_nonterminal():
    plan = [{"id": "step_1", "status": "completed"}, {"id": "step_2", "status": "pending"}]
    resolved = _resolve_step(plan, "bogus", active_id="")
    assert resolved["id"] == "step_2"


def test_next_actionable_prefers_pending():
    plan = [{"id": "step_1", "status": "running"}, {"id": "step_2", "status": "pending"}]
    assert _next_actionable_step(plan)["id"] == "step_2"


def test_next_actionable_falls_back_to_running():
    plan = [{"id": "step_1", "status": "running"}, {"id": "step_2", "status": "completed"}]
    assert _next_actionable_step(plan)["id"] == "step_1"


def test_next_actionable_none_when_all_terminal():
    plan = [{"id": "step_1", "status": "completed"}, {"id": "step_2", "status": "failed"}]
    assert _next_actionable_step(plan) is None


def test_schema_shape():
    assert PLAN_SCHEMA["type"] == "json_schema"
    assert PLAN_SCHEMA["json_schema"]["schema"]["required"] == ["steps"]
