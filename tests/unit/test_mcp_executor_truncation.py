import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools.execution.executor_strategy import (
    MCPExecutor,
    _coerce_mcp_search_params,
    _extract_image_b64,
    _looks_like_drive_api_query,
    _normalize_lmstudio_model_id,
    _normalize_playwright_target_params,
    _sanitize_mcp_params,
    _vision_assist_resolve_target,
)


class MockCallToolResult:
    def __init__(self, text):
        self.text = text

    def model_copy(self, update):
        return MockCallToolResult(text=update["text"])

    def model_dump(self):
        return {"type": "text", "text": self.text}


class TestMCPExecutorTruncation:

    def test_sanitize_mcp_params_drops_nulls(self):
        assert _sanitize_mcp_params({"folderId": "abc", "pageSize": None, "pageToken": None}) == {
            "folderId": "abc",
        }
        assert _sanitize_mcp_params({"query": "wedding"}) == {"query": "wedding"}

    def test_coerce_search_drops_rawquery_for_plain_text(self):
        assert _coerce_mcp_search_params("search", {"query": "wedding", "rawQuery": True}) == {
            "query": "wedding",
        }
        assert _coerce_mcp_search_params("search", {"query": "wedding"}) == {"query": "wedding"}
        assert _looks_like_drive_api_query("name contains 'wedding'")

    def test_coerce_search_keeps_rawquery_for_structured_query(self):
        structured = {"query": "name contains 'wedding'", "rawQuery": True}
        assert _coerce_mcp_search_params("search", structured) == structured

    def test_normalize_playwright_target_strips_ref_prefix(self):
        assert _normalize_playwright_target_params("browser_click", {"target": "ref=e34"}) == {
            "target": "e34",
        }

    def test_normalize_playwright_target_readds_e_prefix(self):
        assert _normalize_playwright_target_params("browser_click", {"target": "34"}) == {
            "target": "e34",
        }

    def test_normalize_playwright_target_renames_selector(self):
        assert _normalize_playwright_target_params("browser_type", {"selector": "e12", "text": "hi"}) == {
            "target": "e12",
            "text": "hi",
        }

    def test_normalize_playwright_target_leaves_valid_target(self):
        assert _normalize_playwright_target_params("browser_click", {"target": "e34"}) == {
            "target": "e34",
        }

    def test_normalize_playwright_target_ignores_non_browser_tools(self):
        assert _normalize_playwright_target_params("search", {"target": "ref=e34"}) == {
            "target": "ref=e34",
        }

    @pytest.mark.asyncio
    async def test_execute_normalizes_browser_click_target(self):
        executor = MCPExecutor()
        mock_item = MockCallToolResult(text="clicked")
        mock_server = MagicMock()
        mock_server.name = "playwright"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        tool = {"name": "browser_click", "mcp_server": mock_server, "source": "mcp"}

        await executor.execute(tool, {"target": "ref=e34"})

        mock_server.call_tool.assert_awaited_once_with("browser_click", {"target": "e34"})

    def test_extract_image_b64_finds_image_item(self):
        assert _extract_image_b64([{"type": "text", "text": "hi"}, {"type": "image", "data": "abc", "mimeType": "image/png"}]) == (
            "abc",
            "image/png",
        )
        assert _extract_image_b64([{"type": "text", "text": "no image here"}]) is None

    def test_normalize_lmstudio_model_id_adds_openai_prefix(self):
        assert _normalize_lmstudio_model_id("qwen/qwen3-vl-8b", "http://localhost:1234/v1") == "openai/qwen/qwen3-vl-8b"
        assert _normalize_lmstudio_model_id("openai/gpt-oss-20b", "http://localhost:1234/v1") == "openai/gpt-oss-20b"

    @pytest.mark.asyncio
    async def test_vision_assist_returns_none_without_registered_profile(self):
        with patch("config.model_registry.get_profile", return_value=None):
            result = await _vision_assist_resolve_target(MagicMock(), "browser_click:{}")
        assert result is None

    @pytest.mark.asyncio
    async def test_vision_assist_swaps_models_and_returns_answer(self):
        mock_profile = MagicMock(base_url="http://localhost:1234/v1", thinking_param_style="qwen3")
        mock_server = MagicMock()
        mock_server.call_tool = AsyncMock(
            side_effect=[
                [{"type": "image", "data": "b64img", "mimeType": "image/png"}],
                [{"type": "text", "text": "```yaml\n- button \"Submit\" [ref=e12]\n```"}],
            ]
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='target="e12"'))]

        def mock_config_get(key, default=None):
            if key == "tools.browser_vision_assist_enabled":
                return True
            if key == "tools.browser_vision_assist_model":
                return "qwen/qwen3-vl-8b"
            return default

        with patch("config.model_registry.get_profile", return_value=mock_profile), \
             patch("src.shared.config_manager.ConfigManager.get", side_effect=mock_config_get), \
             patch("src.core.intelligence.model_lifecycle.ensure_resident", new=AsyncMock(return_value=True)) as mock_ensure_resident, \
             patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)) as mock_completion:
            result = await _vision_assist_resolve_target(mock_server, "browser_click:{'target': 'e34'}")

        assert result == 'target="e12"'
        mock_ensure_resident.assert_awaited_once()
        assert mock_ensure_resident.call_args.args[:2] == ("http://localhost:1234/v1", "qwen/qwen3-vl-8b")
        mock_completion.assert_awaited_once()
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "openai/qwen/qwen3-vl-8b"
        image_block = call_kwargs["messages"][1]["content"][1]
        assert image_block["image_url"]["url"] == "data:image/png;base64,b64img"

    @pytest.mark.asyncio
    async def test_vision_assist_disabled_returns_none_immediately(self):
        mock_server = MagicMock()
        with patch("src.shared.config_manager.ConfigManager.get", return_value=False):
            result = await _vision_assist_resolve_target(mock_server, "browser_click:{'target': 'e34'}")
        assert result is None
        assert not mock_server.call_tool.called

    @pytest.mark.asyncio
    async def test_loop_breaker_calls_vision_assist_for_target_calls(self):
        executor = MCPExecutor()
        mock_server = MagicMock()
        mock_server.name = "playwright"
        mock_server.call_tool = AsyncMock()
        tool = {"name": "browser_click", "mcp_server": mock_server, "source": "mcp"}
        params = {"target": "e34"}
        call_sig = f"browser_click:{__import__('json').dumps(params, sort_keys=True)}"
        executor._browser_call_failure_counts[call_sig] = 2

        with patch(
            "src.infrastructure.tools.execution.executor_strategy._vision_assist_resolve_target",
            new=AsyncMock(return_value='target="e12"'),
        ) as mock_assist:
            result = await executor.execute(tool, dict(params))

        mock_assist.assert_awaited_once()
        assert result["success"] is False
        # Rendered through the shared failure envelope now, so the marker is the
        # classified header rather than a bare "BLOCKED:" prefix.
        assert result["failure_class"] == "blocked"
        assert "TOOL_FAILED[blocked] browser_click" in result["error"]
        # Vision-assist's grounding must survive as the FIRST recovery step —
        # it's the most specific thing we can tell the model here.
        assert 'Vision-assist suggests: target="e12"' in result["error"]
        mock_server.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_loop_breaker_skips_vision_assist_without_target(self):
        executor = MCPExecutor()
        mock_server = MagicMock()
        mock_server.name = "playwright"
        mock_server.call_tool = AsyncMock()
        tool = {"name": "browser_navigate", "mcp_server": mock_server, "source": "mcp"}
        params = {"url": "https://example.com"}
        call_sig = f"browser_navigate:{__import__('json').dumps(params, sort_keys=True)}"
        executor._browser_call_failure_counts[call_sig] = 2

        with patch(
            "src.infrastructure.tools.execution.executor_strategy._vision_assist_resolve_target",
            new=AsyncMock(return_value='target="e12"'),
        ) as mock_assist:
            result = await executor.execute(tool, dict(params))

        mock_assist.assert_not_awaited()
        assert result["success"] is False
        assert "VISION-ASSIST" not in result["error"]

    @pytest.mark.asyncio
    async def test_execute_coerces_drive_search_rawquery(self):
        executor = MCPExecutor()
        mock_item = MockCallToolResult(text="Found 1 files")
        mock_server = MagicMock()
        mock_server.name = "google-drive"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        tool = {"name": "search", "mcp_server": mock_server, "source": "mcp"}

        await executor.execute(tool, {"query": "wedding", "rawQuery": True})

        mock_server.call_tool.assert_awaited_once_with("search", {"query": "wedding"})

    @pytest.mark.asyncio
    async def test_execute_strips_null_optional_params(self):
        executor = MCPExecutor()
        mock_item = MockCallToolResult(text='{"files": []}')
        mock_server = MagicMock()
        mock_server.name = "google-drive"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        tool = {"name": "listFolder", "mcp_server": mock_server, "source": "mcp"}

        await executor.execute(
            tool,
            {"folderId": "1abc", "pageSize": None, "pageToken": None},
        )

        mock_server.call_tool.assert_awaited_once_with("listFolder", {"folderId": "1abc"})

    @pytest.mark.asyncio
    async def test_general_text_truncation_object(self):
        executor = MCPExecutor()
        
        # Create a tool result exceeding 25,000 characters
        large_text = "A" * 30000
        mock_item = MockCallToolResult(text=large_text)
        
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        
        tool = {
            "name": "test_tool",
            "mcp_server": mock_server,
            "source": "mcp"
        }
        
        result = await executor.execute(tool, {})
        
        assert result["success"] is True
        assert len(result["result"]) == 1
        result_text = result["result"][0]["text"]
        
        # Verify the text was truncated and contains the truncation warning
        assert len(result_text) < 30000
        assert "[TRUNCATED" in result_text
        assert "5000 characters to prevent context window flooding" in result_text

    @pytest.mark.asyncio
    async def test_dict_text_truncation(self):
        executor = MCPExecutor()
        
        # Create a dict tool result exceeding 25,000 characters
        large_text = "B" * 30000
        mock_item = {"type": "text", "text": large_text}
        
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        
        tool = {
            "name": "test_tool",
            "mcp_server": mock_server,
            "source": "mcp"
        }
        
        result = await executor.execute(tool, {})
        
        assert result["success"] is True
        assert len(result["result"]) == 1
        result_text = result["result"][0]["text"]
        
        assert len(result_text) < 30000
        assert "[TRUNCATED" in result_text
        assert "5000 characters to prevent context window flooding" in result_text

    @pytest.mark.asyncio
    async def test_string_text_truncation(self):
        executor = MCPExecutor()
        
        # Create a string tool result exceeding 25,000 characters
        large_text = "C" * 30000
        
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_server.call_tool = AsyncMock(return_value=[large_text])
        
        tool = {
            "name": "test_tool",
            "mcp_server": mock_server,
            "source": "mcp"
        }
        
        result = await executor.execute(tool, {})
        
        assert result["success"] is True
        assert len(result["result"]) == 1
        result_text = result["result"][0]
        
        assert len(result_text) < 30000
        assert "[TRUNCATED" in result_text

    @pytest.mark.asyncio
    async def test_snapshot_bypass(self):
        executor = MCPExecutor()
        
        # Snapshot tool outputs should not be truncated by the general truncation limit
        large_text = "D" * 30000
        mock_item = MockCallToolResult(text=large_text)
        
        mock_server = MagicMock()
        mock_server.name = "test-server"
        mock_server.call_tool = AsyncMock(return_value=[mock_item])
        
        tool = {
            # Tool name is 'Snapshot'
            "name": "Snapshot",
            "mcp_server": mock_server,
            "source": "mcp"
        }
        
        # Patch the prune_uia_tree call to just return the string back (to isolate our test from prune_uia_tree behavior)
        with patch("src.infrastructure.tools.execution.snapshot_pruner.prune_uia_tree", return_value=(large_text, 1, 1)):
            result = await executor.execute(tool, {})
            
            assert result["success"] is True
            result_text = result["result"][0]["text"]
            
            # Should NOT contain truncation text
            assert "[TRUNCATED" not in result_text
            assert len(result_text) == 30000
