"""Unit tests for generate_presentation tool (issue #350)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.tools.visual.generate_presentation import (
    GeneratePresentationTool,
    _presenton_http_error,
    normalize_template,
)


@pytest.fixture
def tool():
    return GeneratePresentationTool()


@pytest.mark.asyncio
async def test_execute_missing_config(tool):
    with patch(
        "src.tools.visual.generate_presentation.ConfigManager.get",
        return_value=None,
    ):
        result = await tool.execute(content="Quarterly review deck")
    assert result["success"] is False
    assert "presenton_url" in result["error"].lower()


@pytest.mark.asyncio
async def test_execute_sync_v1_downloads_artifact(tool, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pptx_bytes = b"fake-pptx-content"

    async def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "presentation_id": "abc-123",
                "path": "/static/user_data/abc-123/deck.pptx",
                "edit_path": "/presentation?id=abc-123",
            },
            request=request,
        )

    async def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=pptx_bytes, request=request)

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def config_get(key, default=None):
        values = {
            "visual.presenton_url": "http://127.0.0.1:5000",
            "visual.presenton_use_async": False,
            "visual.presenton_generation_timeout_seconds": 60,
            "visual.presenton_poll_interval_seconds": 1,
        }
        return values.get(key, default)

    with patch(
        "src.tools.visual.generate_presentation.ConfigManager.get",
        side_effect=config_get,
    ), patch(
        "src.tools.visual.generate_presentation.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await tool.execute(
            content="Q4 business review",
            n_slides=6,
            template="modern",
            export_as="pptx",
            title="q4-review",
        )

    assert result["success"] is True
    assert result["export_as"] == "pptx"
    assert result["edit_url"] == "http://127.0.0.1:5000/presentation?id=abc-123"
    assert "download_token" in result


@pytest.mark.asyncio
async def test_execute_async_v3_polls_until_complete(tool, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    poll_count = {"n": 0}

    async def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        if url.endswith("/async"):
            return httpx.Response(
                200,
                json={"id": "task-1", "status": "pending"},
                request=request,
            )
        return httpx.Response(200, json={}, request=request)

    async def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return httpx.Response(
                200,
                json={"id": "task-1", "status": "pending"},
                request=request,
            )
        if "deck.pdf" in url:
            return httpx.Response(200, content=b"%PDF-fake", request=request)
        return httpx.Response(
            200,
            json={
                "id": "task-1",
                "status": "completed",
                "data": {"path": "/static/user_data/task-1/deck.pdf"},
            },
            request=request,
        )

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def config_get(key, default=None):
        values = {
            "visual.presenton_url": "http://127.0.0.1:5000",
            "visual.presenton_use_async": True,
            "visual.presenton_generation_timeout_seconds": 30,
            "visual.presenton_poll_interval_seconds": 0.01,
        }
        return values.get(key, default)

    with patch(
        "src.tools.visual.generate_presentation.ConfigManager.get",
        side_effect=config_get,
    ), patch(
        "src.tools.visual.generate_presentation.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "src.tools.visual.generate_presentation.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        result = await tool.execute(content="Async deck", export_as="pdf")

    assert result["success"] is True
    assert result["export_as"] == "pdf"
    assert poll_count["n"] >= 2


def test_format_action(tool):
    label = tool.format_action(
        {"content": "Investor pitch", "template": "pitch-deck", "export_as": "pdf"}
    )
    assert "Investor pitch" in label
    assert "pitch-deck" in label
    assert "PDF" in label


def test_presenton_http_error_outline_is_actionable(monkeypatch):
    monkeypatch.setattr(
        "src.tools.visual.generate_presentation.ConfigManager.get",
        lambda key, default=None: "qwen/qwen3.5-9b"
        if key == "visual.presenton_model"
        else default,
    )
    result = _presenton_http_error(
        status_code=400,
        body='{"detail":"Failed to generate presentation outlines. Please try again."}',
        template="general",
        n_slides=8,
        base_url="http://127.0.0.1:5000",
    )
    assert result["success"] is False
    assert result["failure_stage"] == "llm_outline"
    assert result["diagnostics"]["presenton_connected"] is True
    assert "not a Docker/connectivity issue" in result["hint"]
    assert "qwen/qwen3.5-9b" in result["hint"]
    assert "load_skill('marp')" in result["hint"]


def test_presenton_http_error_template_is_actionable():
    result = _presenton_http_error(
        status_code=400,
        body='{"detail":"Template not found. Please use a valid template."}',
        template="professional",
        n_slides=8,
        base_url="http://127.0.0.1:5000",
    )
    assert result["failure_stage"] == "template"
    assert "pitch-deck" in result["hint"]
    assert normalize_template("classic") == "general"
    assert normalize_template("professional") == "standard"
    assert normalize_template("modern") == "modern"


@pytest.mark.asyncio
async def test_execute_maps_legacy_template_before_api_call(tool, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen: dict[str, str] = {}

    async def fake_post(url, **kwargs):
        seen["template"] = kwargs.get("json", {}).get("template")
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "presentation_id": "abc-123",
                "path": "/static/user_data/abc-123/deck.pptx",
            },
            request=request,
        )

    async def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=b"fake-pptx", request=request)

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def config_get(key, default=None):
        values = {
            "visual.presenton_url": "http://127.0.0.1:5000",
            "visual.presenton_use_async": False,
            "visual.presenton_generation_timeout_seconds": 60,
            "visual.presenton_poll_interval_seconds": 1,
        }
        return values.get(key, default)

    with patch(
        "src.tools.visual.generate_presentation.ConfigManager.get",
        side_effect=config_get,
    ), patch(
        "src.tools.visual.generate_presentation.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await tool.execute(
            content="Executive summary deck",
            template="professional",
            export_as="pptx",
        )

    assert result["success"] is True
    assert seen["template"] == "standard"
