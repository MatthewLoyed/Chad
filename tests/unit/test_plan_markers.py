"""Unit tests for shared plan marker utilities."""
import json

from src.shared.plan_markers import (
    extract_plan_from_text,
    sanitize_response_text,
    strip_plan_markers,
)


def test_strip_plan_markers():
    text = "Hello [[PLAN]]{\"steps\":[]}[[/PLAN]] world"
    assert "[[PLAN]]" not in strip_plan_markers(text)
    assert "Hello" in strip_plan_markers(text)


def test_extract_plan_from_legacy_markers():
    raw = "Preamble [[PLAN]]" + json.dumps({"steps": [{"id": "s1"}]}) + "[[/PLAN]] tail"
    plan_json, clean = extract_plan_from_text(raw)
    assert plan_json is not None
    assert "[[PLAN]]" not in clean
    assert "steps" in plan_json


def test_extract_plan_from_raw_json():
    raw = json.dumps({"steps": [{"id": "s1", "description": "x", "tools": [], "status": "pending"}]})
    plan_json, clean = extract_plan_from_text(raw)
    assert plan_json == raw
    assert clean == ""


def test_sanitize_response_text_strips_markers_and_flags():
    text = "Done [[PLAN]]x[[/PLAN]] TASK_COMPLETED 🦾"
    clean = sanitize_response_text(text)
    assert "[[PLAN]]" not in clean
    assert "TASK_COMPLETED" not in clean
