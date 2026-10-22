r"""
Regression tests for voice-lane routing (epic #291 Phase 3).

Live probe finding: every spoken turn ran the full agent lane. `category="voice"`
arrived correctly over the WebSocket and the 9B voice model was even selected,
but TurnCoordinator then re-derived the category from the resolved turn_mode --
and turn_mode only spans chat/agent/plan, so "voice" became "agent" right before
the graph state was built. `route_entry` therefore never reached
`voice_responder`, making the entire fast voice lane dead code in practice while
still looking half-wired in the logs.

Run:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_lane_routing.py -q
"""

import pytest

from src.core.orchestration.nodes.routing import route_entry
from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.state_builder import TurnStateBuilder


# --------------------------------------------------------------------------- #
# Category survival through intent resolution


def test_voice_category_survives_agent_turn_mode():
    """The actual bug: voice + agent turn_mode must stay 'voice'."""
    assert TurnModeResolver.category_for_turn("voice", "agent") == "voice"


@pytest.mark.parametrize("turn_mode", ["agent", "chat", "plan", "auto", ""])
def test_voice_category_survives_every_turn_mode(turn_mode):
    assert TurnModeResolver.category_for_turn("voice", turn_mode) == "voice"


def test_non_voice_category_is_still_derived_from_turn_mode():
    """Non-lane categories must keep deferring to the resolved mode."""
    for turn_mode in ("agent", "chat", "plan"):
        expected = TurnModeResolver.category_for_turn_mode(turn_mode)
        assert TurnModeResolver.category_for_turn("agent", turn_mode) == expected
        assert TurnModeResolver.category_for_turn(None, turn_mode) == expected


def test_turn_mode_alone_can_never_yield_voice():
    """Why the guard is required rather than fixing the mode->category map:
    turn_mode has no voice member, so the mapping can't produce it."""
    for turn_mode in ("agent", "chat", "plan", "auto", ""):
        assert TurnModeResolver.category_for_turn_mode(turn_mode) != "voice"


# --------------------------------------------------------------------------- #
# Graph entry routing


def _state(category, **extra):
    state = {"category": category, "turn_mode": "agent", "messages": []}
    state.update(extra)
    return state


def test_route_entry_sends_voice_to_voice_responder():
    assert route_entry(_state("voice")) == "voice_responder"


def test_route_entry_sends_agent_to_supervisor():
    assert route_entry(_state("agent")) == "supervisor"


def test_clobbered_category_routes_away_from_voice_lane():
    """End-to-end statement of the bug: had the category been rewritten to
    'agent', route_entry would bypass the voice responder entirely."""
    clobbered = TurnModeResolver.category_for_turn_mode("agent")
    assert route_entry(_state(clobbered)) != "voice_responder"

    preserved = TurnModeResolver.category_for_turn("voice", "agent")
    assert route_entry(_state(preserved)) == "voice_responder"


# --------------------------------------------------------------------------- #
# Full chain: the three real components a spoken turn actually passes through.
#
# The unit tests above each cover one link. This walks the whole path with the
# real implementations -- resolver -> state builder -> graph entry router --
# because the original bug lived *between* correct components: every piece did
# its own job right while the category was quietly rewritten in transit.


class _Telemetry:
    on_token = None
    on_thinking = None
    on_usage = None


def _build_state_for(incoming_category: str, turn_mode: str = "agent"):
    category = TurnModeResolver.category_for_turn(incoming_category, turn_mode)
    return TurnStateBuilder.build(
        lc_messages=[],
        turn_id="t1",
        session_id="main:default:voice:limb",
        category=category,
        turn_mode=turn_mode,
        platform="pc",
        is_simple=False,
        sys_instr="",
        tools=[],
        max_tool_calls=12,
        thinking_level="minimal",
        bundled_skill_content=None,
        bundled_skill_name=None,
        telemetry=_Telemetry(),
        metadata=None,
    )


def test_spoken_turn_reaches_voice_responder_end_to_end():
    """A WebSocket turn arriving as category='voice' with an agent turn_mode --
    exactly the live case that was silently running the full agent pipeline."""
    state = _build_state_for("voice")

    assert state["category"] == "voice"
    assert route_entry(state) == "voice_responder"
    # The voice lane also caps tool rounds; proves the category reached the
    # builder rather than just surviving the resolver.
    assert state["max_tool_calls"] == 4


def test_typed_turn_still_reaches_supervisor_end_to_end():
    state = _build_state_for("agent")

    assert state["category"] == "agent"
    assert route_entry(state) == "supervisor"
    assert state["max_tool_calls"] == 12
