"""Ensure BaseTool.get_schema() nested parameters reach the LLM unchanged.

Regression guard for the LangChain wrap path: wrap_lc_tool builds a flat Pydantic
args model (array → list, object → dict) for LangGraph execution, but ChatChad
must forward the canonical parameters block from get_schema() so nested items
like ask_user_question.questions[].options[] are visible to the model.
"""

from src.infrastructure.tools.schema.schema_builder import (
    CHAD_PARAMETERS_SCHEMA_ATTR,
    ToolSchemaBuilder,
    parameters_for_llm_from_tool,
    pydantic_args_to_parameters,
)
from src.tools.filesystem.propose_file_plan import ProposeFilePlanTool
from src.tools.maps.osm_tools import ShowMapTool
from src.tools.system.utilities.ask_user_question import AskUserQuestionTool


class _RegistryStub:
    _tools = {}
    _categories = {}
    _tool_sources = {}

    def execute_tool(self, *args, **kwargs):
        pass

    _event_bus = None


def _builder_with(*tools):
    r = _RegistryStub()
    for t in tools:
        r._tools[t.name] = t
    return ToolSchemaBuilder(r), r


def _questions_items_schema(parameters: dict) -> dict:
    return parameters["properties"]["questions"]["items"]


def test_wrap_lc_tool_preserves_canonical_parameters_on_structured_tool():
    _, registry = _builder_with(AskUserQuestionTool())
    builder = ToolSchemaBuilder(registry)
    raw = AskUserQuestionTool().get_schema()["parameters"]

    lc_tool = builder.get_langchain_tool_by_name("ask_user_question")

    assert getattr(lc_tool, CHAD_PARAMETERS_SCHEMA_ATTR) == raw


def test_parameters_for_llm_includes_nested_question_and_options():
    _, registry = _builder_with(AskUserQuestionTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("ask_user_question")

    llm_params = parameters_for_llm_from_tool(lc_tool)
    items = _questions_items_schema(llm_params)

    assert "question" in items["properties"]
    assert "options" in items["properties"]
    assert "question" in items["required"]
    assert "options" in items["required"]

    option_items = items["properties"]["options"]["items"]
    assert "label" in option_items["properties"]
    assert option_items["required"] == ["label"]


def test_pydantic_fallback_strips_nested_items():
    """Document why we must not rely on Pydantic alone for LLM exposure."""
    _, registry = _builder_with(AskUserQuestionTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("ask_user_question")

    pydantic_params = pydantic_args_to_parameters(lc_tool.args_schema)
    questions_prop = pydantic_params["properties"]["questions"]

    assert questions_prop["type"] == "array"
    assert questions_prop.get("items") in ({}, None)


def test_llm_schema_matches_get_schema_for_ask_user_question():
    _, registry = _builder_with(AskUserQuestionTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("ask_user_question")
    canonical = AskUserQuestionTool().get_schema()["parameters"]

    assert parameters_for_llm_from_tool(lc_tool) == canonical


def test_llm_schema_matches_get_schema_for_propose_file_plan():
    _, registry = _builder_with(ProposeFilePlanTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("propose_file_plan")
    canonical = ProposeFilePlanTool().get_schema()["parameters"]

    assert parameters_for_llm_from_tool(lc_tool) == canonical


def test_llm_schema_matches_get_schema_for_show_map_nested_places():
    _, registry = _builder_with(ShowMapTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("show_map")
    canonical = ShowMapTool().get_schema()["parameters"]
    llm_params = parameters_for_llm_from_tool(lc_tool)

    assert llm_params == canonical
    place_items = llm_params["properties"]["places"]["items"]
    assert "lat" in place_items["properties"]
    assert "lon" in place_items["properties"]
    assert "name" in place_items["required"]


def test_flat_tools_still_expose_string_properties():
    from src.tools.system.pause_for_input import PauseForInputTool

    _, registry = _builder_with(PauseForInputTool())
    builder = ToolSchemaBuilder(registry)
    lc_tool = builder.get_langchain_tool_by_name("pause_for_input")
    canonical = PauseForInputTool().get_schema()["parameters"]

    assert parameters_for_llm_from_tool(lc_tool) == canonical
