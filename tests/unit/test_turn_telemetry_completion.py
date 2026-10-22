from types import SimpleNamespace

from src.application.events.events import TurnCompleteEvent, TokenUsageUpdate
from src.core.orchestration.turn.telemetry import TurnTelemetry


def test_completion_preserves_response_and_publishes_final_usage():
    events = []
    telemetry = TurnTelemetry(
        event_bus=SimpleNamespace(publish=events.append), turn_start_time=0,
        metrics={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    )
    outcome = SimpleNamespace(response="Ready", awaiting_approval=False, awaiting_input=False,
                              pending_approval=None, stop_reason="task_completed")
    telemetry.complete(outcome, session_id="session-1", client_turn_id="turn-1")
    assert isinstance(events[0], TurnCompleteEvent)
    assert events[0].response == "Ready"
    assert events[0].turn_id == "session-1"
    assert events[0].client_turn_id == "turn-1"
    assert isinstance(events[1], TokenUsageUpdate)
    assert events[1].total_tokens == 20
    assert events[1].stop_reason == "task_completed"
