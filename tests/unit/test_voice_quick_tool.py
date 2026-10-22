r"""
Unit tests for the tier-2 voice quick-tool-call lane (issue #654 problem 3
follow-up). `try_quick_tool_call` asks the resident 9B voice model to call one of
a small fixed tool set with no other context, for phrasing tier 1's regex/embedding
router misses. These tests mock the LM Studio HTTP call — no real model needed.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_quick_tool.py -q
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.orchestration.nodes.voice_quick_tool import try_quick_tool_call


def _fake_response(tool_calls=None, content=""):
    body = {
        "choices": [{
            "message": {"content": content, "tool_calls": tool_calls or []},
        }],
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(body).encode("utf-8")

    return _Resp()


def _tool_call(name: str, args: dict):
    return [{"function": {"name": name, "arguments": json.dumps(args)}}]


def _registry_with(tool_name: str):
    tool = MagicMock()
    tool.name = tool_name
    tool.ainvoke = AsyncMock(return_value={})
    registry = MagicMock()
    registry.get_langchain_tool_by_name.side_effect = (
        lambda name: tool if name == tool_name else None
    )
    return registry, tool


@pytest.mark.asyncio
async def test_tool_call_response_executes_tool_and_builds_ack():
    registry, tool = _registry_with("open_browser")
    resp = _fake_response(tool_calls=_tool_call("open_browser", {"query": "youtube"}))

    with patch("urllib.request.urlopen", return_value=resp):
        ack = await try_quick_tool_call(
            "can you open youtube", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack == "Opening youtube."
    tool.ainvoke.assert_awaited_once_with({"query": "youtube"})


@pytest.mark.asyncio
async def test_no_tool_call_in_response_returns_none():
    registry, tool = _registry_with("open_browser")
    resp = _fake_response(tool_calls=[], content="NONE")

    with patch("urllib.request.urlopen", return_value=resp):
        ack = await try_quick_tool_call(
            "check my email for the invoice from last week", registry,
            model="qwen/qwen3.5-9b", base_url="http://localhost:1234/v1",
        )

    assert ack is None
    tool.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_tool_registry_returns_none_without_http_call():
    with patch("urllib.request.urlopen") as mock_urlopen:
        ack = await try_quick_tool_call(
            "open youtube", None, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack is None
    mock_urlopen.assert_not_called()


@pytest.mark.asyncio
async def test_tool_not_in_registry_returns_none():
    registry = MagicMock()
    registry.get_langchain_tool_by_name.return_value = None
    resp = _fake_response(tool_calls=_tool_call("open_browser", {"query": "youtube"}))

    with patch("urllib.request.urlopen", return_value=resp):
        ack = await try_quick_tool_call(
            "open youtube", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack is None


@pytest.mark.asyncio
async def test_tool_failure_returns_none_not_raises():
    registry, tool = _registry_with("web_search")
    tool.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    resp = _fake_response(tool_calls=_tool_call("web_search", {"queries": ["pizza"]}))

    with patch("urllib.request.urlopen", return_value=resp):
        ack = await try_quick_tool_call(
            "search for pizza", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack is None


# --------------------------------------------------------------------------- #
# web_search: NOT fire-and-forget. Live bug (issue #654 follow-up): a canned
# "Searching for X." ack left the turn ending with the user never hearing what
# was found. These prove the tool call gets fed back for a real synthesized
# answer, and that synthesis failures fail closed (-> tier 3) rather than
# speaking a broken partial response.
# --------------------------------------------------------------------------- #
def _search_result(sources):
    return {"results": [{"query": "best clash royale card", "sources": sources}]}


@pytest.mark.asyncio
async def test_web_search_hit_synthesizes_answer_from_results():
    registry, tool = _registry_with("web_search")
    tool.ainvoke = AsyncMock(return_value=_search_result([
        {"url": "https://example.com", "title": "Best Cards", "snippet": "The consensus pick is the Mega Knight."},
    ]))
    decide_resp = _fake_response(tool_calls=_tool_call("web_search", {"queries": ["best clash royale card"]}))
    synth_resp = _fake_response(content="The Mega Knight is widely considered the best card right now.")

    with patch("urllib.request.urlopen", side_effect=[decide_resp, synth_resp]):
        ack = await try_quick_tool_call(
            "what's the best card in clash royale", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack == "The Mega Knight is widely considered the best card right now."
    # fetch_content forced off for speed — snippets only, no full-page fetch.
    tool.ainvoke.assert_awaited_once_with({"queries": ["best clash royale card"], "fetch_content": False})


@pytest.mark.asyncio
async def test_web_search_no_usable_content_falls_through_without_synthesis_call():
    registry, tool = _registry_with("web_search")
    tool.ainvoke = AsyncMock(return_value=_search_result([]))  # no sources at all
    decide_resp = _fake_response(tool_calls=_tool_call("web_search", {"queries": ["obscure query"]}))

    with patch("urllib.request.urlopen", return_value=decide_resp) as mock_urlopen:
        ack = await try_quick_tool_call(
            "look up something obscure", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack is None
    assert mock_urlopen.call_count == 1  # synthesis never attempted


@pytest.mark.asyncio
async def test_web_search_synthesis_failure_falls_through():
    registry, tool = _registry_with("web_search")
    tool.ainvoke = AsyncMock(return_value=_search_result([
        {"url": "https://example.com", "title": "A Page", "snippet": "Some content."},
    ]))
    decide_resp = _fake_response(tool_calls=_tool_call("web_search", {"queries": ["something"]}))

    with patch("urllib.request.urlopen", side_effect=[decide_resp, OSError("connection reset")]):
        ack = await try_quick_tool_call(
            "search for something", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack is None


@pytest.mark.asyncio
async def test_get_datetime_acks_without_synthesis():
    registry, tool = _registry_with("get_datetime")
    tool.ainvoke = AsyncMock(return_value={"success": True, "result": "9:47 PM", "format": "time"})
    resp = _fake_response(tool_calls=_tool_call("get_datetime", {"format": "time"}))

    with patch("urllib.request.urlopen", return_value=resp) as mock_urlopen:
        ack = await try_quick_tool_call(
            "what time is it", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack == "It's 9:47 PM."
    assert mock_urlopen.call_count == 1
    tool.ainvoke.assert_awaited_once_with({"format": "time"})


@pytest.mark.asyncio
async def test_hubspace_acks_from_tool_result():
    registry, tool = _registry_with("hubspace_control")
    tool.ainvoke = AsyncMock(
        return_value={"success": True, "result": "Turned on Living Room (light)."}
    )
    resp = _fake_response(tool_calls=_tool_call(
        "hubspace_control", {"action": "turn_on", "device_name": "living room lights"}
    ))

    with patch("urllib.request.urlopen", return_value=resp):
        ack = await try_quick_tool_call(
            "turn on the living room lights", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack == "Turned on Living Room (light)."


@pytest.mark.asyncio
async def test_search_gmail_synthesizes_from_emails():
    registry, tool = _registry_with("search_gmail")
    tool.ainvoke = AsyncMock(return_value={
        "success": True,
        "emails": [{
            "from_name": "Rentvine",
            "subject": "Lease Charges",
            "snippet": "Rent for 200 Hartness",
        }],
        "result": "1 email",
    })
    decide_resp = _fake_response(tool_calls=_tool_call("search_gmail", {"query": "lease"}))
    synth_resp = _fake_response(content="I found a Rentvine email about lease charges.")

    with patch("urllib.request.urlopen", side_effect=[decide_resp, synth_resp]):
        ack = await try_quick_tool_call(
            "check my email for the lease", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert "Rentvine" in ack
    tool.ainvoke.assert_awaited_once_with({"query": "lease", "limit": 5})


@pytest.mark.asyncio
async def test_last_trace_records_tool_and_content():
    from src.core.orchestration.nodes.voice_quick_tool import get_last_trace

    registry, tool = _registry_with("open_app")
    resp = _fake_response(
        tool_calls=_tool_call("open_app", {"name": "Discord"}),
        content="",
    )
    # Simulate provider reasoning field
    body = json.loads(resp.read().decode())
    body["choices"][0]["message"]["reasoning_content"] = "User wants Discord opened."

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(body).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_Resp()):
        ack = await try_quick_tool_call(
            "launch discord", registry, model="qwen/qwen3.5-9b",
            base_url="http://localhost:1234/v1",
        )

    assert ack == "Opening Discord."
    trace = get_last_trace()
    assert trace["tool"] == "open_app"
    assert trace["args"] == {"name": "Discord"}
    assert "Discord" in (trace["reasoning"] or "")