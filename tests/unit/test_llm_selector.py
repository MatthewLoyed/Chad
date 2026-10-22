"""Unit tests for LLMToolSelector parsing and model-id matching."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.tools.selection.llm_selector import LLMToolSelector


@pytest.fixture
def selector():
    return LLMToolSelector(model_id="qwen/qwen3.5-9b")


class TestMatchModelId:
    def test_exact_match(self, selector):
        assert selector._match_model_id(
            "qwen/qwen3.5-9b",
            ["qwen/qwen3.5-9b", "other/model"],
        ) == "qwen/qwen3.5-9b"

    def test_quant_suffix_match(self, selector):
        assert selector._match_model_id(
            "qwen/qwen3.5-9b",
            ["qwen/qwen3.5-9b:2"],
        ) == "qwen/qwen3.5-9b:2"

    def test_no_match(self, selector):
        assert selector._match_model_id(
            "qwen/qwen3.5-9b",
            ["qwen/qwen3.6-35b-a3b"],
        ) is None


class TestParse:
    def test_plain_json_array(self, selector):
        valid = {"search_gmail", "open_app"}
        assert selector._parse('["search_gmail"]', valid) == ["search_gmail"]

    def test_markdown_fences(self, selector):
        valid = {"search_gmail"}
        raw = '```json\n["search_gmail"]\n```'
        assert selector._parse(raw, valid) == ["search_gmail"]

    def test_redacted_thinking_stripped(self, selector):
        valid = {"open_app"}
        raw = '<think>pick a tool</think>["open_app"]'
        assert selector._parse(raw, valid) == ["open_app"]

    def test_think_tags_stripped(self, selector):
        valid = {"open_app"}
        think_open = chr(60) + "think" + chr(62)
        think_close = chr(60) + "/" + "think" + chr(62)
        raw = f"{think_open}reasoning here{think_close}[\"open_app\"]"
        assert selector._parse(raw, valid) == ["open_app"]

    def test_json_embedded_in_prose(self, selector):
        valid = {"open_app", "get_datetime"}
        raw = 'Here are the tools: ["open_app", "get_datetime"]'
        assert selector._parse(raw, valid) == ["open_app", "get_datetime"]

    def test_drops_unknown_tools(self, selector):
        valid = {"open_app"}
        assert selector._parse('["open_app", "fake_tool"]', valid) == ["open_app"]

    def test_empty_array(self, selector):
        assert selector._parse("[]", set()) == []

    def test_invalid_json_returns_none(self, selector):
        assert selector._parse("not json at all", {"open_app"}) is None


class TestSelectRetriesOnMidFlightUnload:
    """LM Studio can report a model resident and then evict it (VRAM-budget eviction,
    idle TTL) before the completions call actually lands — see model_lifecycle.py's
    documented "last resort, evict a wanted model" eviction policy. select() must
    self-heal from that instead of aborting the whole turn."""

    def _ok_response(self, tool_names):
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps(tool_names)}, "finish_reason": "stop"}]
        }).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_retries_and_succeeds_after_forced_reload(self, selector):
        selector._server_reachable = True
        selector._resolved_model_id = "qwen/qwen3.5-9b"

        unload_error = urllib.error.HTTPError(
            url="http://localhost:1234/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"Model is unloaded."}'),
        )
        ok_response = self._ok_response(["open_app"])

        with patch.object(selector, "_ensure_model_loaded", return_value=True), \
             patch("urllib.request.urlopen", side_effect=[unload_error, ok_response]) as mock_urlopen:
            result = selector.select("open the app", {"open_app": MagicMock(description="opens an app")})

        assert result == ["open_app"]
        assert mock_urlopen.call_count == 2
        assert selector.last_select_meta.get("status") == "ok"

    def test_gives_up_after_max_attempts(self, selector):
        selector._server_reachable = True
        selector._resolved_model_id = "qwen/qwen3.5-9b"

        def make_unload_error():
            return urllib.error.HTTPError(
                url="http://localhost:1234/v1/chat/completions",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"Model is unloaded."}'),
            )

        with patch.object(selector, "_ensure_model_loaded", return_value=True), \
             patch("urllib.request.urlopen", side_effect=[make_unload_error(), make_unload_error()]) as mock_urlopen:
            result = selector.select("open the app", {"open_app": MagicMock(description="opens an app")})

        assert result is None
        assert mock_urlopen.call_count == 2
        assert selector.last_select_meta.get("status") == "failed"


class TestProviderRouting:
    """The selector must go through the shared provider abstraction for backend concerns
    (resident-model listing, auth-key placeholder) rather than sniffing URLs or GETting
    /v1/models directly — so it works against LM Studio, Ollama, or a static endpoint."""

    def test_list_loaded_delegates_to_provider(self):
        """Resident-model listing routes through model_lifecycle.list_loaded_sync (which
        resolves the ProviderLifecycle) instead of a raw /v1/models GET — that raw listing
        is wrong for Ollama (pulled vs resident)."""
        selector = LLMToolSelector(model_id="qwen/qwen3.5-9b")
        with patch(
            "src.infrastructure.tools.selection.llm_selector.list_loaded_sync",
            return_value=["qwen/qwen3.5-9b"],
        ) as mock_list:
            assert selector._list_loaded_models() == ["qwen/qwen3.5-9b"]
        mock_list.assert_called_once_with(selector._base_url)

    def test_list_loaded_is_cached(self):
        """A 5s cache avoids hammering the backend across reachability/select/unload in one turn."""
        selector = LLMToolSelector(model_id="qwen/qwen3.5-9b")
        with patch(
            "src.infrastructure.tools.selection.llm_selector.list_loaded_sync",
            return_value=["m"],
        ) as mock_list:
            selector._list_loaded_models()
            selector._list_loaded_models()
        assert mock_list.call_count == 1

    def test_lmstudio_gets_placeholder_auth_header(self):
        """LM Studio's capability supplies the `lm-studio` placeholder key (harmless — it's
        ignored — but matches what the adapters send)."""
        selector = LLMToolSelector(model_id="qwen/qwen3.5-9b", base_url="http://localhost:1234/v1")
        headers = selector._request_headers()
        assert headers["Authorization"] == "Bearer lm-studio"

    def test_ollama_sends_no_auth_header(self):
        """Ollama (and other backends with no default_api_key) send no Authorization header."""
        selector = LLMToolSelector(model_id="qwen3", base_url="http://localhost:11434/v1")
        assert "Authorization" not in selector._request_headers()


class TestExtractMessageContent:
    def test_reads_content_field(self, selector):
        body = {
            "choices": [{"message": {"content": '["open_app"]'}}],
        }
        assert selector._extract_message_content(body) == '["open_app"]'

    def test_empty_when_only_reasoning(self, selector):
        body = {
            "choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}],
        }
        assert selector._extract_message_content(body) == ""


class TestKnowledgeContextInSelectPrompt:
    """Personal knowledge boost must reach the 9B so it can infer e.g. Gmail tools
    when the bare query never says 'email' (stipend / activity-index miss)."""

    def _ok_response(self, tool_names):
        body = json.dumps({
            "choices": [{"message": {"content": json.dumps(tool_names), "finish_reason": "stop"}}]
        }).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_knowledge_context_appears_in_user_message(self, selector):
        selector._server_reachable = True
        selector._resolved_model_id = "qwen/qwen3.5-9b"
        captured = {}

        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            captured["user"] = payload["messages"][1]["content"]
            captured["system"] = payload["messages"][0]["content"]
            return self._ok_response(["search_gmail"])

        knowledge = (
            "\n\n### ACTIVITY CONTEXT (PERSONAL INDEX)\n"
            "- Email: 'Stipend Rate Increase Request' from Smart Scholar\n"
        )
        with patch.object(selector, "_ensure_model_loaded", return_value=True), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = selector.select(
                "find what the next step for stipend increase request is",
                {"search_gmail": MagicMock(description="Search Gmail"),
                 "open_app": MagicMock(description="Open an app")},
                knowledge_context=knowledge,
            )

        assert result == ["search_gmail"]
        assert selector.last_select_meta.get("had_knowledge_context") is True
        assert "PERSONAL CONTEXT" in captured["user"]
        assert "Stipend Rate Increase Request" in captured["user"]
        assert "candidate tools" in captured["system"].lower()
        assert "core tools" in captured["system"].lower()

    def test_no_knowledge_flag_when_absent(self, selector):
        selector._server_reachable = True
        selector._resolved_model_id = "qwen/qwen3.5-9b"
        with patch.object(selector, "_ensure_model_loaded", return_value=True), \
             patch("urllib.request.urlopen", return_value=self._ok_response([])):
            selector.select("hello", {"open_app": MagicMock(description="opens")})
        assert selector.last_select_meta.get("had_knowledge_context") is False

    def test_full_knowledge_context_passed_uncapped(self, selector):
        """9B selector gets the full knowledge boost — no char truncation."""
        selector._server_reachable = True
        selector._resolved_model_id = "qwen/qwen3.5-9b"
        captured = {}
        long_knowledge = "Email hit line\n" + ("x" * 4000)

        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            captured["user"] = payload["messages"][1]["content"]
            return self._ok_response(["search_gmail"])

        with patch.object(selector, "_ensure_model_loaded", return_value=True), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            selector.select(
                "stipend next step",
                {"search_gmail": MagicMock(description="Search Gmail")},
                knowledge_context=long_knowledge,
            )

        assert long_knowledge in captured["user"]
        assert selector.last_select_meta.get("had_knowledge_context") is True
