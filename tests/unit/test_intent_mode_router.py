import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.intelligence.intent_mode_router import IntentModeRouter, IntentClassification


@pytest.fixture
def router():
    return IntentModeRouter(model_id="google/gemma-3-1b", strict_model=True)


def test_parse_json_modes(router):
    assert router._parse('{"mode":"chat","confidence":0.97}') == ("chat", 0.97)
    assert router._parse('{"mode":"agent","confidence":0.5}') == ("agent", 0.5)
    assert router._parse('```json\n{"mode":"planning","confidence":0.8}\n```') == ("planning", 0.8)


def test_parse_rejects_invalid_mode(router):
    assert router._parse('{"mode":"banana","confidence":0.9}') is None
    assert router._parse("") is None


def test_classify_falls_back_when_model_unavailable(router):
    with patch.object(router, "_ensure_model_loaded", return_value=False):
        result = router.classify("hi")
    assert result.mode == "agent"
    assert result.confidence == 0.0


def test_classify_parses_lm_studio_response(router):
    body = {
        "choices": [
            {"message": {"content": '{"mode":"chat","confidence":0.99}'}, "finish_reason": "stop"}
        ]
    }

    def fake_urlopen(req, timeout=30.0):
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch.object(router, "_ensure_model_loaded", return_value=True):
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = router.classify("hey there")

    assert isinstance(result, IntentClassification)
    assert result.mode == "chat"
    assert result.confidence == 0.99


def test_from_config_disabled():
    with patch("src.shared.config_manager.ConfigManager.get") as mock_get:
        mock_get.side_effect = lambda key, default=None: (
            {"enabled": False} if key == "intent_router" else default
        )
        assert IntentModeRouter.from_config() is None
