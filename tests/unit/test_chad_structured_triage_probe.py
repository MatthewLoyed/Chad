import copy
import json
import io

import jsonschema
import pytest

from evals.chad_structured_triage_probe import build_request, assess_response, run_probe
from evals.chad_triage_gate import SCHEMA, triage_json_schema


def response(content=None, finish="stop", reasoning=0):
    return {"choices": [{"finish_reason": finish, "message": {
        "content": json.dumps(SCHEMA) if content is None else content}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": reasoning}}}


def test_request_uses_real_schema_and_endpoint_reasoning_control():
    request = build_request("test-model", {"prompt": "test"}, {"response": "bad"})
    assert request["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in request
    schema = request["response_format"]["json_schema"]["schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(SCHEMA, schema)
    assert request["max_tokens"] == 1800
    with pytest.raises(ValueError):
        build_request("test-model", {"prompt": "test"}, {}, 4400)


@pytest.mark.parametrize("observation", [
    {"id": "o1", "kind": "tool_call", "index": 0, "name": "web_search"},
    {"id": "o1", "kind": "response_equals", "value": "bad"},
    {"id": "o1", "kind": "response_not_json"},
])
def test_schema_accepts_all_gate_observation_shapes(observation):
    value = copy.deepcopy(SCHEMA)
    value["observations"] = [observation]
    jsonschema.validate(value, triage_json_schema())


@pytest.mark.parametrize("data", [response(content=""), response(finish="length"),
                                  response(reasoning=20), response(content="```json\n{}\n```"),
                                  response(content='{"schema_version":1}')])
def test_incomplete_reasoning_or_invalid_output_never_passes(data):
    result = assess_response(data, {"response": "bad"}, {})
    assert not result["transport_passed"] and not result["coding_dispatched"]


def test_investigate_passes_transport_without_coding_authorization():
    result = assess_response(response(), {"response": "bad"}, {})
    assert result["transport_passed"]
    assert result["gate"]["valid"] and not result["gate"]["dispatch_allowed"]


def test_reasoning_content_is_never_promoted_to_final_answer():
    data = response(content="")
    data["choices"][0]["message"]["reasoning_content"] = json.dumps(SCHEMA)
    assert not assess_response(data, {"response": "bad"}, {})["transport_passed"]


def test_probe_does_not_generate_or_load_when_model_is_not_resident(tmp_path, monkeypatch):
    calls = []
    def urlopen(url, **kwargs):
        calls.append(url)
        assert url.endswith('/api/v1/models')
        return io.BytesIO(b'{"models":[]}')
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    path = tmp_path / 'report.json'
    result = run_probe({'prompt': 'test'}, {}, model='unloaded', output=path)
    assert 'not already loaded' in result['error']
    assert len(calls) == 2  # initial/final inventory only
    assert json.loads(path.read_text())['coding_dispatched'] is False
