# Contact names are fictional aliases; legacy case IDs/filenames remain unchanged.
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from src.core.orchestration.scout_evidence import report_tool, evidence_record, render_evidence, relevant_known_context
from src.core.orchestration.scout_tool_policy import scout_tool_allowed
from src.core.orchestration.nodes import planner_node as planner
from src.core.orchestration.nodes import scout_node as scout

ITEM = {"question": "Contact email", "status": "verified", "finding": "contact@example.test", "source": "search_contacts fixture-person", "source_call_id": "lookup-1"}
EVIDENCE = [{"brief": "Find contact email", "items": [ITEM], "status": "reported", "report": ""}]


@pytest.mark.asyncio
async def test_structured_report_rejects_unattributed_verified_claim():
    tool = report_tool()
    good = await tool.ainvoke({"items": [ITEM]})
    assert good["_scout_report"]
    bad = await tool.ainvoke({"items": [{**ITEM, "source": ""}]})
    assert bad["success"] is False
    unknown = await tool.ainvoke({"items": [{**ITEM, "status": "unresolved", "source": ""}]})
    assert unknown["_scout_report"]


def test_ledger_retains_first_pass_when_followup_adds_facts():
    records = evidence_record({"scout_evidence": EVIDENCE}, "Find app path", [], "App unavailable", status="unavailable")
    assert records[0] == EVIDENCE[0]
    assert "contact@example.test" in render_evidence(records)
    assert "App unavailable" in render_evidence(records)
    assert render_evidence(records, "Unrelated weather forecast") == ""


def test_known_context_is_relevant_bounded_and_sourced():
    text = relevant_known_context("Find Jordan email", "email Jordan", [("persona:user", "Jordan email is contact@example.test\nLikes astronomy.\n" + "Jordan " * 200)])
    assert "[persona:user] Jordan email" in text
    assert "astronomy" not in text
    assert len(text) < 2700


@pytest.mark.parametrize("name", ["invented_read_tool", "delete_messages", "create_gmail_draft", "call_mcp_tool", "delegate_to_system_agent"])
def test_unclassified_or_mutating_tools_are_denied(name):
    assert not scout_tool_allowed(name, SimpleNamespace(_tools={}))


def test_registered_read_only_metadata_enables_unknown_reader():
    registry = SimpleNamespace(_tools={"custom_reader": {"annotations": {"readOnlyHint": True}}})
    assert scout_tool_allowed("custom_reader", registry)
    assert not scout_tool_allowed("made_up", registry)


@pytest.mark.asyncio
async def test_execute_boundary_denies_unclassified_tool_before_invocation():
    tool = SimpleNamespace(ainvoke=AsyncMock())
    result = await scout._execute_scout_tool("invented_read_tool", {}, {"invented_read_tool": tool}, SimpleNamespace(_tools={}), {}, "root")
    assert result["scout_blocked"]
    tool.ainvoke.assert_not_called()


def test_approval_input_does_not_erase_checkpoint_evidence():
    from src.core.orchestration.turn.state_builder import TurnStateBuilder
    from langgraph.graph import StateGraph, START, END
    from src.core.orchestration.graph.state import ChadState
    from langgraph.checkpoint.memory import InMemorySaver
    incoming = TurnStateBuilder.build(lc_messages=[HumanMessage(content="Proceed")], turn_id="approval", session_id="same", category="planning", turn_mode="plan", platform="pc", is_simple=False, sys_instr="role", tools=[], max_tool_calls=10, thinking_level="low", bundled_skill_content=None, bundled_skill_name=None, telemetry=SimpleNamespace(on_token=None, on_thinking=None, on_usage=None), metadata={}, resuming_thread=True)
    for key in ("scout_evidence", "gather_brief", "scout_coverage"):
        assert key not in incoming
    graph = StateGraph(ChadState)
    graph.add_node("keep", lambda state: {})
    graph.add_edge(START, "keep"); graph.add_edge("keep", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "evidence-approval"}}
    app.invoke({"scout_evidence": EVIDENCE, "gather_brief": "Known contact", "scout_coverage": "Sufficient"}, config)
    resumed = app.invoke(incoming, config)
    assert resumed["scout_evidence"] == EVIDENCE
    assert resumed["gather_brief"] == "Known contact"
    assert resumed["scout_coverage"] == "Sufficient"


class PlannerModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.bindings = []
    def copy(self, **kwargs): return self
    def bind_tools(self, tools):
        self.bindings.append({t.name for t in tools})
        return self
    async def ainvoke(self, messages, config=None): return next(self.responses)


def tc(name, args, ident="t"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


@pytest.fixture
def planner_hooks(monkeypatch):
    async def lifecycle(name, args, run): return await run()
    monkeypatch.setattr(planner, "run_with_tool_lifecycle", lifecycle)
    finish = MagicMock(side_effect=lambda state, **kwargs: {"plan": True, "scout_coverage": state.get("scout_coverage")})
    monkeypatch.setattr(planner, "_finish_with_plan", finish)
    return finish


@pytest.mark.asyncio
async def test_coverage_gate_blocks_early_proposal_then_records_review(planner_hooks):
    model = PlannerModel([tc("propose_plan", {"steps": []}), tc("review_scout", {"decision": "proceed", "reason": "Contact resolved; no blocking gaps"}), tc("propose_plan", {"steps": [{"description": "Draft", "tools": []}]})])
    out = await planner._run_planner_tool_loop(model, {"scout_evidence": EVIDENCE, "scout_dispatch_count": 1}, {}, tool_registry=None, planning_base="role", menu=[])
    assert model.bindings == [{"review_scout"}, {"propose_plan"}]
    assert out["scout_coverage"] == "Contact resolved; no blocking gaps"
    planner_hooks.assert_called_once()


@pytest.mark.asyncio
async def test_followup_is_targeted_and_capped(planner_hooks):
    state = {"scout_evidence": EVIDENCE, "scout_dispatch_count": 1}
    model = PlannerModel([tc("review_scout", {"decision": "follow_up", "reason": "App path missing", "brief": "Verify only app path"})])
    out = await planner._run_planner_tool_loop(model, state, {}, tool_registry=None, planning_base="role", menu=[])
    assert out["scout_gather_request"] == "Verify only app path"
    assert out["scout_dispatch_count"] == 2
    assert out["scout_coverage"] is None
    planner_hooks.assert_not_called()
    # Third pass rejected; the model can still acknowledge unresolved gaps and plan.
    model = PlannerModel([tc("review_scout", {"decision": "follow_up", "reason": "Still missing", "brief": "Again"}), tc("review_scout", {"decision": "proceed", "reason": "App path remains unavailable; plan targeted verification"}), tc("propose_plan", {"steps": [{"description": "Verify", "tools": []}]})])
    out = await planner._run_planner_tool_loop(model, {**state, "scout_dispatch_count": 2}, {}, tool_registry=None, planning_base="role", menu=[])
    assert out["plan"]


@pytest.mark.asyncio
async def test_both_delegate_paths_receive_evidence_even_with_cached_system(monkeypatch):
    import src.core.orchestration.handoffs as handoffs
    monkeypatch.setattr(handoffs, "_swap_for_subagent", AsyncMock(return_value=None))
    monkeypatch.setattr(handoffs, "_swap_back", AsyncMock())
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={"messages": [SystemMessage(content="cached")]}))
    graph.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content="observed")]})
    config = {"configurable": {"thread_id": "parent", "scout_evidence": EVIDENCE}}
    tool = handoffs.create_handoff_tools({"system_agent": graph})[0]
    await tool.coroutine("Read contact email", config)
    assert "contact@example.test" in graph.ainvoke.call_args.args[0]["messages"][-1].content
    node = handoffs.create_subagent_adapter("system_agent", graph)
    await node({"messages": [tc("delegate_to_system_agent", {"task": "Read contact email"})], "scout_evidence": EVIDENCE}, config)
    assert "contact@example.test" in graph.ainvoke.call_args.args[0]["messages"][-1].content


@pytest.mark.asyncio
async def test_actual_scout_report_records_items_for_coverage(monkeypatch):
    from src.application.events.event_bus import EventBus
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    monkeypatch.setattr(SystemPromptBuilder, "build", lambda **kwargs: "Scout role")
    monkeypatch.setattr(SystemPromptBuilder, "core_profile_block", lambda: "Contact email is contact@example.test")
    monkeypatch.setattr(scout, "_maybe_unload_main_model", AsyncMock())
    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = lambda n: SimpleNamespace(name=n, description="read")
    registry.get_all_schemas.return_value = []
    monkeypatch.setattr(scout, "_execute_scout_tool", AsyncMock(return_value={"success": True, "result": "contact@example.test"}))
    model = PlannerModel([tc("manage_user_memory", {"action": "search", "query": "contact"}, "lookup-1"), tc("finish_scout_report", {"items": [ITEM]})])
    out = await scout.make_scout_node(model, registry, [])({"messages": [HumanMessage(content="Email contact")], "scout_gather_request": "Find contact email"}, {})
    assert out["scout_evidence"][0]["items"] == [ITEM]
    assert out["scout_coverage"] is None
    assert "[verified] Contact email" in out["gather_brief"]


@pytest.mark.asyncio
async def test_plain_text_scout_exit_is_not_silently_verified(monkeypatch):
    from src.application.events.event_bus import EventBus
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    monkeypatch.setattr(EventBus, "get_instance", lambda: MagicMock())
    monkeypatch.setattr(SystemPromptBuilder, "build", lambda **kwargs: "Scout role")
    monkeypatch.setattr(scout, "_maybe_unload_main_model", AsyncMock())
    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = lambda n: SimpleNamespace(name=n, description="read")
    registry.get_all_schemas.return_value = []
    model = PlannerModel([AIMessage(content="I found something"), AIMessage(content="I found something")])
    out = await scout.make_scout_node(model, registry, [])({"messages": [HumanMessage(content="Locate a file")], "scout_gather_request": "Find file"}, {})
    assert out["scout_evidence"][0]["status"] == "unstructured"
    assert out["scout_evidence"][0]["items"] == []
    assert out["scout_coverage"] is None


@pytest.mark.asyncio
async def test_new_planning_episode_resets_prior_evidence_but_replan_preserves_it(monkeypatch):
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    monkeypatch.setattr(SystemPromptBuilder, "build", lambda **kwargs: "Planner")
    loop = AsyncMock(return_value={"active_plan": None})
    monkeypatch.setattr(planner, "_run_planner_tool_loop", loop)
    node = planner.make_planner_node(object(), SimpleNamespace(get_langchain_tool_by_name=lambda n: None), [])
    state = {"turn_id": "new", "scout_episode_id": "old", "scout_evidence": EVIDENCE, "scout_dispatch_count": 2, "scout_coverage": "old", "messages": [], "active_plan": [{"status": "completed"}]}
    out = await node(state, {})
    assert loop.await_args.args[1]["scout_evidence"] == []
    assert out["scout_evidence"] == []
    await node({**state, "validation_feedback": "Repair output"}, {})
    assert loop.await_args.args[1]["scout_evidence"] == EVIDENCE


def test_verified_sources_must_reference_successful_observation_or_exact_known_fact():
    from src.core.orchestration.scout_evidence import validate_report_sources
    assert validate_report_sources([ITEM], [], "")
    for outcome in ["failed", "discovery"]:
        assert validate_report_sources([ITEM], [{"call_id": "lookup-1", "outcome": outcome}], "")
    assert not validate_report_sources([ITEM], [{"call_id": "lookup-1", "outcome": "returned"}], "")
    supplied = {**ITEM, "source_call_id": "", "source": "persona:user"}
    assert not validate_report_sources([supplied], [], "- [persona:user] Contact email is contact@example.test")
    assert validate_report_sources([{**supplied, "finding": "invented@example.test"}], [], "- [persona:user] Contact email is contact@example.test")


@pytest.mark.asyncio
async def test_actual_supervisor_receives_evidence_and_propagates_delegate_config(monkeypatch):
    from langchain_core.runnables import RunnableLambda
    from src.core.orchestration.nodes.supervisor_node import make_supervisor_node
    import src.core.skill_system.skill_loader as skill_loader
    monkeypatch.setattr(skill_loader, "get_skill_loader", lambda: SimpleNamespace(is_empty=False, list_skills=lambda: []))
    seen = []
    async def respond(prompt):
        seen.extend(prompt.to_messages())
        return AIMessage(content="Continue execution")
    model = SimpleNamespace(bind_tools=lambda tools: RunnableLambda(respond))
    registry = SimpleNamespace(_categories={}, _tool_sources={}, get_all_schemas=lambda: [], get_langchain_tool_by_name=lambda n: None)
    node = make_supervisor_node(model, {}, registry, [])
    config = {}
    await node({"messages": [HumanMessage(content="Proceed")], "system_instruction": "Supervisor role", "active_plan": [{"id": "step1", "description": "Draft contact email", "tools": [], "status": "pending"}], "scout_evidence": EVIDENCE}, config)
    assert any("contact@example.test" in str(m.content) for m in seen)
    assert config["configurable"]["scout_evidence"] == EVIDENCE


@pytest.mark.parametrize("name", ["escalate_to_agent", "propose_plan", "dispatch_scout", "complete_step", "dispatch_coding_task", "load_skill"])
def test_retry_safe_control_tools_are_not_read_only_observations(name):
    # Metadata previously let escalate_to_agent leak through Scout's filter.
    registry = SimpleNamespace(_tools={name: SimpleNamespace(is_side_effecting=False)})
    assert not scout_tool_allowed(name, registry)
    registry._tools[name] = {"annotations": {"readOnlyHint": True}}
    assert not scout_tool_allowed(name, registry)
