"""Unit tests for the modular nudge framework (issue #245) and its adapter wiring."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.intelligence.nudges import NudgeManager, NudgeResult, ReasoningRunawayGuard


# ── ReasoningRunawayGuard / NudgeManager unit tests ──────────────────────────

def test_guard_does_not_trigger_below_cap():
    guard = ReasoningRunawayGuard(token_cap=100)
    manager = NudgeManager([guard])
    assert manager.observe(reasoning_chunk="short") is None


def test_guard_triggers_once_cap_exceeded_with_no_tool_call():
    guard = ReasoningRunawayGuard(token_cap=10)
    manager = NudgeManager([guard])
    result = manager.observe(reasoning_chunk="x" * 100)
    assert isinstance(result, NudgeResult)
    assert result.disable_thinking is True
    assert "stop reasoning" in result.message.lower()


def test_guard_does_not_trigger_once_tool_call_seen():
    guard = ReasoningRunawayGuard(token_cap=10)
    manager = NudgeManager([guard])
    manager.observe(reasoning_chunk="x" * 100, has_tool_call=True)
    assert manager.observe(reasoning_chunk="") is None


def test_guard_disabled_when_cap_non_positive():
    guard = ReasoningRunawayGuard(token_cap=0)
    manager = NudgeManager([guard])
    assert manager.observe(reasoning_chunk="x" * 10_000) is None


def test_guard_reset_clears_accumulated_state():
    guard = ReasoningRunawayGuard(token_cap=10)
    manager = NudgeManager([guard])
    assert manager.observe(reasoning_chunk="x" * 100) is not None
    guard.reset()
    assert guard.check() is None


def test_tiny_stream_deltas_do_not_round_away_reasoning():
    manager = NudgeManager([ReasoningRunawayGuard(token_cap=10)])
    for _ in range(39):
        assert manager.observe(reasoning_chunk="x") is None
    assert manager.observe(reasoning_chunk="x") is not None


def test_extra_nudges_compose_without_touching_existing_ones():
    """A second, unrelated nudge type can be added without changing the guard above."""

    class AlwaysFires(ReasoningRunawayGuard):
        def check(self):
            return NudgeResult(message="custom nudge", reason="always")

    manager = NudgeManager([ReasoningRunawayGuard(token_cap=999999), AlwaysFires(token_cap=999999)])
    result = manager.observe(reasoning_chunk="tiny")
    assert result.reason == "always"


# ── Adapter wiring: stream cancel + resend on trigger ────────────────────────

def _make_chunk(reasoning=None, tool_call_name=None, finish_reason=None, content=None):
    delta = SimpleNamespace(
        reasoning_content=reasoning,
        content=content,
        tool_calls=(
            [SimpleNamespace(index=0, id="call_1", function=SimpleNamespace(name=tool_call_name, arguments="{}"))]
            if tool_call_name
            else None
        ),
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeStream:
    """Minimal async-iterable mimicking litellm's streaming response."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


@pytest.fixture
def adapter():
    return LiteLLMAdapter(model_name="openai/google/gemma-4-31b-it-qat-q4_0", base_url="http://localhost:1234/v1")


@pytest.mark.asyncio
async def test_runaway_reasoning_triggers_resend_with_thinking_disabled(adapter):
    """First call reasons forever (no tool call) → adapter cancels + resends once, thinking off."""
    runaway_chunks = [_make_chunk(reasoning="x" * 4000) for _ in range(4)]  # ~4000 estimated tokens each
    resend_chunks = [_make_chunk(content="final answer", finish_reason="stop")]

    call_log = []

    async def fake_acompletion(**req):
        call_log.append(req)
        if len(call_log) == 1:
            return _FakeStream(runaway_chunks)
        return _FakeStream(resend_chunks)

    with patch("src.core.intelligence.adapters.litellm_adapter.litellm.acompletion", side_effect=fake_acompletion), \
         patch("src.core.intelligence.adapters.litellm_adapter.ConfigManager.get", return_value=100):
        response = await adapter.generate(messages=[{"role": "user", "content": "loop forever"}])

    assert len(call_log) == 2, "expected exactly one cancel + one resend"
    resent_last_message = call_log[1]["messages"][-1]
    assert resent_last_message["role"] == "user"
    assert "stop reasoning" in resent_last_message["content"].lower()
    assert response.text == "final answer"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_nudge_resend_is_not_retried_twice(adapter):
    """Even if the resend also runs away, the adapter does not nudge a second time (no infinite loop)."""
    runaway_chunks = [_make_chunk(reasoning="x" * 4000) for _ in range(4)]

    async def fake_acompletion(**req):
        return _FakeStream(list(runaway_chunks))

    with patch("src.core.intelligence.adapters.litellm_adapter.litellm.acompletion", side_effect=fake_acompletion), \
         patch("src.core.intelligence.adapters.litellm_adapter.ConfigManager.get", return_value=100):
        response = await adapter.generate(messages=[{"role": "user", "content": "loop forever"}])

    # The watchdog remains active on retry, but never starts a third generation.
    assert response.finish_reason == "error"
    assert "after corrective retry" in response.raw


@pytest.mark.asyncio
async def test_reasoning_guard_survives_narration_retry(adapter):
    calls = []
    async def fake_acompletion(**req):
        calls.append(req)
        if len(calls) == 1:
            return _FakeStream([_make_chunk(content="I'll now search for the requested information and perform all the actions you asked for. " * 2)])
        return _FakeStream([_make_chunk(reasoning="x") for _ in range(1000)])
    tools = [{"name": "read_file", "parameters": {"type": "object", "properties": {}}}]
    with patch("src.core.intelligence.adapters.litellm_adapter.litellm.acompletion", side_effect=fake_acompletion), \
         patch("src.core.intelligence.adapters.litellm_adapter.ConfigManager.get", return_value=100):
        response = await adapter.generate(messages=[{"role": "user", "content": "act"}], tools=tools)
    assert len(calls) == 2
    assert response.finish_reason == "error"
    assert "after corrective retry" in response.raw
