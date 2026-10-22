import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from evals import live


@pytest.mark.asyncio
async def test_interactive_resume_resolves_then_starts_same_conversation(monkeypatch):
    ws = AsyncMock()
    async def collect(socket, trace, reasoning, results, **kwargs):
        sent = [json.loads(call.args[0]) for call in socket.send.await_args_list]
        assert [m["type"] for m in sent] == ["tool_approval_response", "user_input"]
        assert sent[0]["payload"]["token"] == "pending-question"
        assert sent[0]["payload"]["decision"] == sent[1]["payload"]["text"] == "Meet join code"
        assert all(m["payload"]["conversation_id"] == "existing-chat" for m in sent)
        assert sent[1]["payload"]["platform"] == live.EVAL_PLATFORM
        assert sent[1]["payload"]["category"] == "agent"
        trace.status = "success"
        trace.stop_reason = "completed"
    monkeypatch.setattr(live, "_collect", collect)
    monkeypatch.setattr(live, "drain_ws", AsyncMock())
    monkeypatch.setenv("LANGSMITH_LIVE", "0")
    trace = await live.probe_workflow("original task", category="agent", auth_token="test", ws=ws,
        conv_id="existing-chat", resume_token="pending-question", resume_answer="Meet join code",
        max_rounds=1, interactive=True)
    assert not trace.error and trace.completed


@pytest.mark.asyncio
async def test_workflow_timeout_retains_partial_round(monkeypatch):
    async def collect(ws, trace, reasoning, results, **kwargs):
        trace.tool_calls.append({"name": "read_file", "params": {"file_path": "fixture"}})
        trace.tool_names.append("read_file")
        trace.events.append({"type": "tool_start", "name": "read_file"})
        reasoning.append("partial reasoning")
        results.append("read_file -> evidence")
        await asyncio.sleep(1)
    monkeypatch.setattr(live, "_collect", collect)
    monkeypatch.setattr(live, "RESPONSE_TIMEOUT", .01)
    monkeypatch.setattr(live, "drain_ws", AsyncMock())
    monkeypatch.setenv("LANGSMITH_LIVE", "0")
    trace = await live.probe_workflow("test", auth_token="test", ws=AsyncMock())
    assert trace.stop_reason == "wall_timeout"
    assert trace.tool_calls[0]["name"] == "read_file"
    assert "partial reasoning" in trace.reasoning_full
    assert "evidence" in trace.tool_results
    assert trace.events and trace.rounds_detail


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"type": "heartbeat", "payload": {}},
    {"type": "ai_thought", "payload": {"state": "thinking", "text": "", "conversation_id": "own"}},
    {"type": "ai_thought", "payload": {"state": "thinking", "text": "other", "conversation_id": "other"}},
    {"type": "execution_plan", "payload": {"plan": [{"status": "running"}], "conversation_id": "own"}},
])
async def test_irrelevant_events_do_not_mask_stall(monkeypatch, payload):
    monkeypatch.setattr(live, "ROUND_IDLE_CHECK_S", .02)
    monkeypatch.setattr(live, "MAX_IDLE_CHECKS", 2)
    class Socket:
        async def recv(self):
            await asyncio.sleep(.002)
            return json.dumps(payload)
    trace = live.LiveTrace(prompt="test", category="agent")
    await asyncio.wait_for(live._collect(Socket(), trace, [], [], expected_conv="own"), .3)
    assert trace.stop_reason == "stalled"


@pytest.mark.asyncio
async def test_reasoning_progress_extends_idle_not_wall_clock(monkeypatch):
    monkeypatch.setattr(live, "ROUND_IDLE_CHECK_S", .1)
    monkeypatch.setattr(live, "MAX_IDLE_CHECKS", 2)
    class Socket:
        def __init__(self): self.count = 0
        async def recv(self):
            await asyncio.sleep(.03)
            self.count += 1
            return json.dumps({"type": "ai_thought" if self.count < 8 else "ai_response",
                "payload": {"conversation_id": "own", "state": "thinking", "text": "progress"}})
    trace = live.LiveTrace(prompt="test", category="agent")
    await live._collect(Socket(), trace, [], [], expected_conv="own")
    assert trace.status == "success" and not trace.error
