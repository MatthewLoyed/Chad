"""Issue #513 — parallel sub-agent fan-out.

Covers the two things a live smoke test can't cheaply exercise per-turn: the routing
decision (does route_after_supervisor send >1 delegate calls to parallel_delegate, while
leaving the single-delegate path byte-for-byte unchanged) and the VRAM-eligibility
partitioning (does would_coexist correctly separate coexist-eligible targets from the
large/unknown-size ones flagged, e.g. the coder model).
"""
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.nodes.routing import make_route_after_supervisor
from src.core.intelligence import model_lifecycle
from src.core.orchestration.handoffs import _partition_for_concurrency
from src.shared.config_manager import ConfigManager


def _tool_call(name: str, call_id: str) -> dict:
    return {"name": name, "args": {"task": "do it"}, "id": call_id}


def _state_with_calls(*names) -> dict:
    msg = AIMessage(content="", tool_calls=[_tool_call(n, f"call_{i}") for i, n in enumerate(names)])
    return {"messages": [msg]}


SUB_AGENTS = {"traversal_agent": object(), "research_agent": object(), "code_agent": object()}


class TestRouteAfterSupervisor:
    def test_single_delegate_routes_directly_to_agent(self):
        route = make_route_after_supervisor(SUB_AGENTS)
        state = _state_with_calls("delegate_to_traversal_agent")
        assert route(state) == "traversal_agent"

    def test_two_delegates_route_to_parallel_delegate(self):
        route = make_route_after_supervisor(SUB_AGENTS)
        state = _state_with_calls("delegate_to_traversal_agent", "delegate_to_research_agent")
        assert route(state) == "parallel_delegate"

    def test_unknown_agent_delegate_is_ignored(self):
        route = make_route_after_supervisor(SUB_AGENTS)
        state = _state_with_calls("delegate_to_not_a_real_agent")
        assert route(state) == "tools"

    def test_duplicate_delegate_to_same_agent_counts_once(self):
        # Same specialist called twice for one task should NOT trigger parallel_delegate —
        # it's still a single-domain case (routing dedupes agent names before counting).
        route = make_route_after_supervisor(SUB_AGENTS)
        state = _state_with_calls("delegate_to_traversal_agent", "delegate_to_traversal_agent")
        assert route(state) == "traversal_agent"


class TestWouldCoexist:
    def test_same_model_deduped_fits_budget(self, monkeypatch):
        def fake_get(key, default=None):
            if key == "local_models.lifecycle.vram_budget_gb":
                return 8
            if key == "local_models.lifecycle.model_size_estimates_gb":
                return {"9b": 6}
            return default
        monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))
        assert model_lifecycle.would_coexist(["qwen/qwen3.5-9b", "qwen/qwen3.5-9b"]) is True

    def test_unknown_size_model_is_not_eligible(self, monkeypatch):
        def fake_get(key, default=None):
            if key == "local_models.lifecycle.vram_budget_gb":
                return 8
            if key == "local_models.lifecycle.model_size_estimates_gb":
                return {"9b": 6}
            return default
        monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))
        assert model_lifecycle.would_coexist(["qwen/qwen3.5-9b", "qwen/qwen3-coder-30b"]) is False

    def test_over_budget_pair_is_not_eligible(self, monkeypatch):
        def fake_get(key, default=None):
            if key == "local_models.lifecycle.vram_budget_gb":
                return 8
            if key == "local_models.lifecycle.model_size_estimates_gb":
                return {"9b": 6, "26b": 16}
            return default
        monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))
        assert model_lifecycle.would_coexist(["qwen/qwen3.5-9b", "google/gemma-4-26b-a4b-qat"]) is False

    def test_no_budget_configured_is_unconstrained(self, monkeypatch):
        def fake_get(key, default=None):
            if key == "local_models.lifecycle.vram_budget_gb":
                return 0
            return default
        monkeypatch.setattr(ConfigManager, "get", staticmethod(fake_get))
        assert model_lifecycle.would_coexist(["anything", "goes"]) is True


class TestPartitionForConcurrency:
    def test_two_small_models_partition_concurrent(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.orchestration.handoffs._delegate_model_id",
            lambda name: "qwen/qwen3.5-9b",
        )
        monkeypatch.setattr(model_lifecycle, "would_coexist", lambda ids: len(set(ids)) <= 1)
        concurrent, serial = _partition_for_concurrency(["traversal_agent", "research_agent"], cap=4)
        assert concurrent == ["traversal_agent", "research_agent"]
        assert serial == []

    def test_incompatible_model_falls_back_to_serial(self, monkeypatch):
        ids = {"traversal_agent": "qwen/qwen3.5-9b", "code_agent": "qwen/qwen3-coder-30b"}
        monkeypatch.setattr(
            "src.core.orchestration.handoffs._delegate_model_id",
            lambda name: ids[name],
        )
        monkeypatch.setattr(
            model_lifecycle, "would_coexist",
            lambda model_ids: len(set(model_ids)) <= 1,  # only identical models "fit"
        )
        concurrent, serial = _partition_for_concurrency(["traversal_agent", "code_agent"], cap=4)
        assert concurrent == ["traversal_agent"]
        assert serial == ["code_agent"]

    def test_cap_limits_concurrent_batch_size(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.orchestration.handoffs._delegate_model_id",
            lambda name: "qwen/qwen3.5-9b",
        )
        monkeypatch.setattr(model_lifecycle, "would_coexist", lambda ids: True)
        concurrent, serial = _partition_for_concurrency(["a", "b", "c"], cap=2)
        assert concurrent == ["a", "b"]
        assert serial == ["c"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
