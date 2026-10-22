import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.skill_system import skill_commands
from src.core.skill_system.skill_loader import SkillEntry


@pytest.fixture
def catalog(monkeypatch):
    entry = SkillEntry("microsoft-assignment", "Read Canvas assignment, copy chapter files, open Project and PDF", "test/SKILL.md")
    loader = SimpleNamespace(
        initialize=AsyncMock(), _catalog={entry.name: entry},
        _is_loadable=lambda e: e.name == "microsoft-assignment",
        load_skill_content=AsyncMock(return_value="Read assignment and prepare its working copies."),
    )
    monkeypatch.setattr(skill_commands, "get_skill_loader", lambda: loader)
    monkeypatch.setattr(skill_commands.ConfigManager, "get", lambda *args: {"course_name": "Example course"})
    return loader


@pytest.mark.asyncio
async def test_arguments_and_private_resources_reach_selected_skill(catalog):
    result = await skill_commands.resolve_skill_command("/microsoft-assignment assignment 5")
    assert result.name == "microsoft-assignment"
    assert 'Arguments: "assignment 5"' in result.content
    assert json.dumps({"course_name": "Example course"}) in result.content
    assert "Canvas" in result.selection_query and "assignment 5" in result.selection_query
    catalog.load_skill_content.assert_awaited_once_with("microsoft-assignment")


@pytest.mark.asyncio
async def test_no_arguments_does_not_reuse_previous_invocation(catalog):
    await skill_commands.resolve_skill_command("/microsoft-assignment 5")
    result = await skill_commands.resolve_skill_command("/microsoft-assignment")
    assert 'Arguments: ""' in result.content
    assert 'Arguments: "5"' not in result.content


@pytest.mark.asyncio
async def test_underscore_alias_and_case_are_supported(catalog):
    assert (await skill_commands.resolve_skill_command("/MICROSOFT_ASSIGNMENT 5")).name == "microsoft-assignment"


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["/missing 5", "/../../secrets", "/microsoft-assignment/../../config"])
async def test_unknown_or_path_commands_never_load_content(catalog, prompt):
    result = await skill_commands.resolve_skill_command(prompt)
    assert result.response
    assert not result.name
    catalog.load_skill_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_skill_is_not_invocable(catalog):
    catalog._catalog["microsoft-assignment"].enabled = False
    result = await skill_commands.resolve_skill_command("/microsoft-assignment 5")
    assert result.response
    catalog.load_skill_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_skills_help_does_not_run_any_skill(catalog):
    result = await skill_commands.resolve_skill_command("/skills")
    assert "/microsoft-assignment" in result.response
    catalog.load_skill_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_text_does_not_initialize_skills(catalog):
    assert await skill_commands.resolve_skill_command("Explain routines") is None
    catalog.initialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_forces_skill_into_agent_lane_and_preserves_arguments(catalog, monkeypatch):
    from src.core.orchestration.turn.pipeline import TurnPipeline

    pipeline = TurnPipeline.__new__(TurnPipeline)
    router = SimpleNamespace(get_model_for_category=Mock(return_value=SimpleNamespace(adapter="supervisor-adapter")))
    pipeline._c = SimpleNamespace(model_coordinator=SimpleNamespace(router=router))
    pipeline.prompt, pipeline.category = "/microsoft-assignment 5", "chat"
    pipeline._prepare = Mock(return_value=None)
    pipeline._try_voice_fast_lane = AsyncMock(return_value=None)
    pipeline._try_voice_quick_tool_lane = AsyncMock(return_value=None)
    pipeline._prepare_approval_continuation = AsyncMock(return_value=True)
    pipeline._resolve_mode = AsyncMock()
    pipeline._open_span = Mock()
    pipeline._resolve_tools = AsyncMock(return_value=None)
    pipeline._build_prompt = Mock()
    pipeline._prepare_graph = AsyncMock()
    pipeline._invoke = AsyncMock(return_value="Setup complete")
    assert await pipeline.run() == "Setup complete"
    assert pipeline.category == "agent"
    assert pipeline.adapter == "supervisor-adapter"
    router.get_model_for_category.assert_called_once_with("agent")
    assert pipeline.prompt == "/microsoft-assignment 5"
    assert 'Arguments: "5"' in pipeline.skill_command.content
    pipeline._prepare_approval_continuation.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_skill_command_cannot_escalate_to_desktop(catalog):
    from src.core.orchestration.turn.pipeline import TurnPipeline

    pipeline = TurnPipeline.__new__(TurnPipeline)
    pipeline._c = SimpleNamespace()
    pipeline.prompt, pipeline.category = "/microsoft-assignment 5", "contact"
    pipeline._prepare = Mock(return_value=None)
    pipeline._complete_from_quick_lane = Mock(side_effect=lambda response, reason: response)
    pipeline._resolve_mode = AsyncMock()
    result = await pipeline.run()
    assert "desktop" in result
    assert pipeline.category == "contact"
    pipeline._resolve_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_loads_explicit_guide_and_uses_skill_purpose_for_tool_selection(catalog):
    from src.core.orchestration.turn.pipeline import TurnPipeline

    pipeline = TurnPipeline.__new__(TurnPipeline)
    pipeline.skill_command = await skill_commands.resolve_skill_command("/microsoft-assignment 5")
    selector = SimpleNamespace(resolve_bundled_skill=AsyncMock())
    pipeline._c = SimpleNamespace(
        tool_registry=None, _tool_selector=selector,
        _get_optimized_tools=AsyncMock(return_value=[]),
        _fetch_knowledge_for_selection=AsyncMock(return_value=""),
    )
    pipeline.scratch = SimpleNamespace(bundled_skills=["some-other-skill"], tool_selection={}, knowledge_boost="")
    pipeline.delivery, pipeline.category = None, "agent"
    pipeline.prompt, pipeline.session_id, pipeline.history = "/microsoft-assignment 5", "test-session", []
    pipeline.active_adapter = SimpleNamespace(model_name="supervisor")
    assert await pipeline._resolve_tools_for_agent() is None
    assert pipeline.bundled_skill_name == "microsoft-assignment"
    assert 'Arguments: "5"' in pipeline.bundled_skill_content
    assert "Read assignment" in pipeline.bundled_skill_content
    selector.resolve_bundled_skill.assert_not_awaited()
    assert "Canvas" in pipeline._c._get_optimized_tools.call_args.args[0]


@pytest.mark.asyncio
async def test_missing_skill_file_returns_actionable_error(catalog):
    catalog.load_skill_content.side_effect = FileNotFoundError
    result = await skill_commands.resolve_skill_command("/microsoft-assignment 5")
    assert "Could not load" in result.response
    assert not result.name
