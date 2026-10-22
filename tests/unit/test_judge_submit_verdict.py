"""Judge _ask prefers submit_verdict tool_calls over JSON-in-content."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.judges import _ask, _verdict_from_tool_calls


def test_verdict_from_tool_calls_reads_submit_verdict_args():
    args = _verdict_from_tool_calls(
        [
            {
                "name": "submit_verdict",
                "args": {
                    "score": 9,
                    "pass": True,
                    "reasons": ["Grounded in Drive reads."],
                },
            }
        ]
    )
    assert args["score"] == 9
    assert args["pass"] is True
    assert args["reasons"] == ["Grounded in Drive reads."]


def test_verdict_from_tool_calls_ignores_other_tools():
    assert _verdict_from_tool_calls([{"name": "other", "args": {"score": 1}}]) is None
    assert _verdict_from_tool_calls(None) is None


@pytest.mark.asyncio
async def test_ask_prefers_submit_verdict_tool_call():
    adapter = SimpleNamespace(
        _eval_judge_num_ctx=8192,
        generate=AsyncMock(
            return_value=SimpleNamespace(
                text="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "submit_verdict",
                        "args": {
                            "score": 10,
                            "pass": True,
                            "reasons": ["Plan matches tool evidence."],
                        },
                    }
                ],
            )
        ),
    )
    out = await _ask(adapter, system="Judge the plan.", user="USER:\nhello")
    assert out["score"] == 10.0
    assert out["pass"] is True
    assert out["reasons"] == ["Plan matches tool evidence."]
    kwargs = adapter.generate.await_args.kwargs
    assert kwargs["tools"][0]["name"] == "submit_verdict"
    assert kwargs["tool_choice"] == "required"
    assert "0–10" in kwargs["tools"][0]["parameters"]["properties"]["score"]["description"]


@pytest.mark.asyncio
async def test_ask_docks_perfect_score_when_inefficiencies_listed():
    adapter = SimpleNamespace(
        _eval_judge_num_ctx=8192,
        generate=AsyncMock(
            return_value=SimpleNamespace(
                text="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "submit_verdict",
                        "args": {
                            "score": 10,
                            "pass": True,
                            "reasons": ["Plan ok"],
                            "inefficiencies": ["redundant Drive reads of same file_id"],
                        },
                    }
                ],
            )
        ),
    )
    out = await _ask(adapter, system="Judge.", user="x")
    assert out["score"] == 9.0
    assert out["pass"] is True
    assert out["inefficiencies"] == ["redundant Drive reads of same file_id"]


@pytest.mark.asyncio
async def test_ask_scales_legacy_0_1_fraction():
    adapter = SimpleNamespace(
        _eval_judge_num_ctx=8192,
        generate=AsyncMock(
            return_value=SimpleNamespace(
                text='{"score": 0.7, "pass": true, "reasons": ["ok"]}',
                tool_calls=None,
            )
        ),
    )
    out = await _ask(adapter, system="Judge.", user="x")
    assert out["score"] == 7.0
    assert out["pass"] is True
    assert out["reasons"] == ["ok"]
