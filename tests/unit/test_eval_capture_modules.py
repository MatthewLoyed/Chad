"""Shared capture modules: agent_scope, tool_lifecycle, trajectory packer."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.core.orchestration.agent_scope import agent_scope
from src.core.orchestration.context import agent_name_var, parent_correlation_id_var
from src.core.orchestration.tool_lifecycle import wire_tool_result_fields
from evals.trajectory import build_trajectory_summary, pack_judge_evidence


def test_agent_scope_sets_and_resets_name():
    prev = agent_name_var.get()
    with agent_scope("Planner"):
        assert agent_name_var.get() == "Planner"
    assert agent_name_var.get() == prev


def test_agent_scope_parent_id():
    with agent_scope("Scout", parent_id="scout_abc", set_parent=True):
        assert agent_name_var.get() == "Scout"
        assert parent_correlation_id_var.get() == "scout_abc"


def test_wire_tool_result_preserves_content_and_brief():
    raw = {
        "success": True,
        "result": "Read file X (10 chars)",
        "content": "FULL BODY",
        "gather_brief": "Answered: yes",
    }
    out = wire_tool_result_fields(raw)
    assert out["content"] == "FULL BODY"
    assert out["gather_brief"] == "Answered: yes"
    assert out["result"] == "Read file X (10 chars)"


def test_trajectory_summary_flags_duplicate_reads():
    events = [
        {"type": "tool_start", "name": "google_drive_read_file", "params": {"file_id": "1abc"}},
        {"type": "tool_end", "name": "google_drive_read_file", "result": "ok"},
        {"type": "tool_start", "name": "google_drive_read_file", "params": {"file_id": "1abc"}},
        {"type": "tool_end", "name": "google_drive_read_file", "result": "ok"},
        {"type": "tool_start", "name": "google_drive_read_file", "params": {"file_id": "MASTER_BUSINESS_MODEL"}},
    ]
    summary = build_trajectory_summary(events=events, tool_names=[e["name"] for e in events if e["type"] == "tool_start"])
    assert "1abc" in summary
    assert "MASTER_BUSINESS_MODEL" in summary
    assert "duplicate_file_id_reads" in summary


from src.core.orchestration.read_memory import cache_get, cache_put, fresh_read_cache
from src.tools.workspace.drive import looks_like_drive_file_id


def test_looks_like_drive_file_id():
    assert looks_like_drive_file_id("1yrfzJ7q5B26L1yCY38opLsrckKuxFvYcFs-vG87rnCo")
    assert not looks_like_drive_file_id("MASTER_BUSINESS_MODEL")
    assert not looks_like_drive_file_id("Website Concept")


def test_fresh_read_cache_roundtrip():
    with fresh_read_cache():
        assert cache_get("k") is None
        cache_put("k", {"success": True, "content": "x"})
        assert cache_get("k")["content"] == "x"
    assert cache_get("k") is None
