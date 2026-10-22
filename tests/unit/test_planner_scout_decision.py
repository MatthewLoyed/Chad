from unittest.mock import AsyncMock, MagicMock
import pytest
from langchain_core.messages import AIMessage
from src.core.orchestration.nodes import planner_node as planner


def call(name, args, ident="call"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.bindings = []
    def copy(self, **kwargs):
        return self
    def bind_tools(self, tools):
        self.bindings.append({t.name for t in tools})
        return self
    async def ainvoke(self, messages, config=None):
        return next(self.responses)


@pytest.fixture
def finish(monkeypatch):
    fn = MagicMock(side_effect=lambda state, **kwargs: {"planned": True, "scout_decision": state.get("scout_decision")})
    monkeypatch.setattr(planner, "_finish_with_plan", fn)
    async def lifecycle(name, args, run):
        return await run()
    monkeypatch.setattr(planner, "run_with_tool_lifecycle", lifecycle)
    return fn


async def run(model, state=None):
    return await planner._run_planner_tool_loop(model, state or {}, {}, tool_registry=None,
                                               planning_base="role", menu=[], allow_ask=False)


@pytest.mark.asyncio
async def test_first_phase_only_allows_scout_decision(finish):
    model = Model([call("dispatch_scout", {"brief": "Find contact in memory"})])
    out = await run(model)
    assert model.bindings == [{"dispatch_scout", "skip_scout"}]
    assert out["scout_gather_request"] == "Find contact in memory"
    assert out["scout_decision"] == "dispatched"
    finish.assert_not_called()


@pytest.mark.asyncio
async def test_proposal_cannot_bypass_decision(finish):
    model = Model([call("propose_plan", {"steps": []}), call("dispatch_scout", {"brief": "Verify app location"})])
    out = await run(model)
    assert out["scout_gather_request"] == "Verify app location"
    finish.assert_not_called()


@pytest.mark.asyncio
async def test_skip_unlocks_planning_and_records_reason(finish):
    model = Model([call("skip_scout", {"reason": "All inputs supplied"}), call("propose_plan", {"steps": [{"description": "Use supplied inputs", "tools": []}]})])
    out = await run(model)
    assert model.bindings[0] == {"dispatch_scout", "skip_scout"}
    assert model.bindings[1] == {"propose_plan"}
    assert out["scout_decision"] == "skipped: All inputs supplied"
    finish.assert_called_once()


@pytest.mark.asyncio
async def test_mixed_batch_cannot_skip_recon(finish):
    mixed = AIMessage(content="", tool_calls=[
        {"id": "a", "name": "dispatch_scout", "args": {"brief": "Read"}},
        {"id": "b", "name": "propose_plan", "args": {"steps": []}},
    ])
    out = await run(Model([mixed, call("dispatch_scout", {"brief": "Read actual source"})]))
    assert out["scout_gather_request"] == "Read actual source"
    assert len([m for m in out["messages"] if getattr(m, "type", None) == "tool"]) == 3
    finish.assert_not_called()


@pytest.mark.asyncio
async def test_no_output_cannot_use_degraded_plan_fallback(finish):
    with pytest.raises(RuntimeError, match="did not choose"):
        await run(Model([AIMessage(content="I will plan"), AIMessage(content="Here is a plan")]))
    finish.assert_not_called()


@pytest.mark.asyncio
async def test_return_from_scout_does_not_require_second_decision(finish):
    model = Model([call("propose_plan", {"steps": [{"description": "Execute", "tools": []}]})])
    await run(model, {"gather_brief": "Facts and gaps", "scout_dispatch_count": 1})
    assert model.bindings == [{"propose_plan"}]


@pytest.mark.asyncio
async def test_blank_skip_does_not_unlock_planning(finish):
    model = Model([call("skip_scout", {"reason": " "}), call("dispatch_scout", {"brief": "Verify facts"})])
    out = await run(model)
    assert out["scout_gather_request"] == "Verify facts"
    assert model.bindings == [{"dispatch_scout", "skip_scout"}]


@pytest.mark.asyncio
async def test_invalid_decisions_exhaust_budget_without_creating_plan(finish, monkeypatch):
    from src.shared.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: 2 if key == "planner.info_needs_max_rounds" else default)
    with pytest.raises(RuntimeError, match="decision budget"):
        await run(Model([call("propose_plan", {"steps": []}), call("skip_scout", {"reason": ""})]))
    finish.assert_not_called()


def test_skip_is_not_an_execution_plan_step():
    from src.core.orchestration.nodes.scout_node import validate_plan_tools_against_menu
    steps = [{"description": "Skip", "tools": ["skip_scout"]}]
    assert planner._strip_forbidden_plan_tools(steps)[0]["tools"] == []
    assert "skip_scout" in validate_plan_tools_against_menu(steps, None)
