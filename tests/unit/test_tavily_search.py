"""TavilySearchTool — optional, opt-in Tavily backend for web_search.

Mirrors test_serper_search.py. Covers: no-key no-op (must not break the
zero-config default), successful query -> Brave-shaped envelope mapping, and
upstream-failure handling. See docs/features/WEB_SEARCH.md.
"""
from __future__ import annotations

import json

import httpx
import pytest

from src.tools.web.tavily_search import TavilySearchTool, tavily_configured
from src.shared.config_manager import ConfigManager

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: default))


def test_tavily_configured_false_without_key():
    assert tavily_configured() is False


@pytest.mark.asyncio
async def test_execute_without_key_is_a_graceful_noop():
    tool = TavilySearchTool()
    result = await tool.execute(query="anything")
    assert result["success"] is False
    assert "TAVILY_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_execute_with_key_maps_results(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "fake-key"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer fake-key"
        body = json.loads(request.content)
        assert body["query"] == "Dream Events & Catering Nashville TN"
        return httpx.Response(200, json={
            "results": [
                {"url": "https://dreameventsandcatering.com", "title": "Dream Events & Catering",
                 "content": "Nashville catering company"},
            ]
        })

    monkeypatch.setattr(
        "src.tools.web.tavily_search.httpx.AsyncClient",
        lambda *a, **kw: _RealAsyncClient(transport=httpx.MockTransport(handler)),
    )

    tool = TavilySearchTool()
    result = await tool.execute(query="Dream Events & Catering Nashville TN")

    assert result["success"] is True
    payload = json.loads(result["result"])
    items = payload["web"]["results"]
    assert items[0]["url"] == "https://dreameventsandcatering.com"
    assert items[0]["title"] == "Dream Events & Catering"


@pytest.mark.asyncio
async def test_execute_upstream_error_returns_failure(monkeypatch):
    monkeypatch.setattr(ConfigManager, "get_secret", staticmethod(lambda key, default=None: "fake-key"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    monkeypatch.setattr(
        "src.tools.web.tavily_search.httpx.AsyncClient",
        lambda *a, **kw: _RealAsyncClient(transport=httpx.MockTransport(handler)),
    )

    tool = TavilySearchTool()
    result = await tool.execute(query="anything")
    assert result["success"] is False
    assert "Tavily unreachable" in result["error"]


def test_validate_params_rejects_empty_query():
    tool = TavilySearchTool()
    ok, err = tool.validate_params(query="")
    assert not ok and "non-empty" in err
