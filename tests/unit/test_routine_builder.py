"""Supervisor-backed routine drafting: parsing, schema validation, and failure paths."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.routines.routine_builder import RoutineBuilder

CATALOG = [
    {"name": "web_search", "description": "search the web", "parameters": {"properties": {"queries": {}}}},
    {"name": "get_datetime", "description": "current time", "parameters": {}},
]


def _builder():
    return RoutineBuilder.__new__(RoutineBuilder)  # skip __init__ (no ConfigManager needed for _parse_steps)


# --- _parse_steps: pure logic ------------------------------------------------ #

def test_parse_steps_plain_json_array():
    b = _builder()
    raw = json.dumps([
        {"tool_name": "web_search", "label": "Search", "params": {"queries": ["x"]}},
    ])
    steps = b._parse_steps(raw, valid_tools={"web_search", "get_datetime"})
    assert steps == [{"tool_name": "web_search", "label": "Search", "params": {"queries": ["x"]}}]


def test_parse_steps_strips_markdown_fences():
    b = _builder()
    raw = "```json\n" + json.dumps([{"tool_name": "get_datetime"}]) + "\n```"
    steps = b._parse_steps(raw, valid_tools={"get_datetime"})
    assert steps[0]["tool_name"] == "get_datetime"
    assert steps[0]["label"] == "get_datetime"  # defaults to tool_name
    assert steps[0]["params"] == {}


def test_parse_steps_extracts_json_embedded_in_prose():
    b = _builder()
    raw = f"Sure, here's the plan:\n{json.dumps([{'tool_name': 'get_datetime'}])}\nLet me know!"
    steps = b._parse_steps(raw, valid_tools={"get_datetime"})
    assert steps[0]["tool_name"] == "get_datetime"


def test_parse_steps_rejects_unknown_tool_names():
    b = _builder()
    raw = json.dumps([
        {"tool_name": "web_search"},
        {"tool_name": "made_up_tool"},
    ])
    steps = b._parse_steps(raw, valid_tools={"web_search"})
    assert steps is None


def test_parse_steps_all_unknown_returns_none():
    b = _builder()
    raw = json.dumps([{"tool_name": "made_up_tool"}])
    assert b._parse_steps(raw, valid_tools={"web_search"}) is None


def test_parse_steps_invalid_json_returns_none():
    b = _builder()
    assert b._parse_steps("not json at all { broken", valid_tools={"web_search"}) is None


def test_parse_steps_empty_string_returns_none():
    b = _builder()
    assert b._parse_steps("", valid_tools={"web_search"}) is None


def test_parse_steps_non_list_json_returns_none():
    b = _builder()
    assert b._parse_steps(json.dumps({"not": "a list"}), valid_tools={"web_search"}) is None


def test_parse_steps_rejects_non_dict_entries():
    b = _builder()
    raw = json.dumps(["just a string", {"tool_name": "web_search"}])
    steps = b._parse_steps(raw, valid_tools={"web_search"})
    assert steps is None


# --- draft(): async supervisor adapter, no model-server HTTP shortcuts -------- #


def adapter_for(text, finish_reason="stop"):
    return SimpleNamespace(generate=AsyncMock(return_value=SimpleNamespace(
        text=text, finish_reason=finish_reason,
    )))


@pytest.mark.asyncio
async def test_draft_empty_description_short_circuits():
    adapter = adapter_for("")
    result = await RoutineBuilder(adapter).draft("   ", CATALOG)
    assert not result["success"]
    assert "required" in result["error"].lower()
    adapter.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_request_failure_returns_clean_error():
    adapter = SimpleNamespace(generate=AsyncMock(side_effect=OSError("connection refused")))
    result = await RoutineBuilder(adapter).draft("search for cat pictures", CATALOG)
    assert not result["success"]
    assert "request failed" in result["error"]


@pytest.mark.asyncio
async def test_draft_unparseable_model_output_returns_clean_error():
    result = await RoutineBuilder(adapter_for("garbage, not json")).draft("search", CATALOG)
    assert not result["success"]
    assert result["steps"] == []


@pytest.mark.asyncio
async def test_draft_success_returns_validated_steps():
    output = json.dumps([{"tool_name": "web_search", "label": "Search", "params": {"queries": ["cats"]}}])
    result = await RoutineBuilder(adapter_for(output)).draft("search for cats", CATALOG)
    assert result["success"]
    assert result["steps"][0]["tool_name"] == "web_search"
    assert result["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["length", "error"])
async def test_draft_rejects_incomplete_or_failed_generation(reason):
    result = await RoutineBuilder(adapter_for('[{"tool_name":"get_datetime"}]', reason)).draft("time", CATALOG)
    assert not result["success"]
    assert result["steps"] == []


@pytest.mark.asyncio
async def test_draft_empty_catalog_does_not_call_model():
    adapter = adapter_for("")
    result = await RoutineBuilder(adapter).draft("search", [])
    assert not result["success"]
    adapter.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_cancellation_propagates():
    import asyncio
    adapter = SimpleNamespace(generate=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await RoutineBuilder(adapter).draft("search", CATALOG)
