"""Tests for voice/GUI turn delivery channel isolation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.remote.limb_coordinator import LimbCoordinator
from src.remote.gui_limb import GUIChadLimb
from src.remote.protocol import ChadMessage, MessageType
from src.shared.turn_delivery import (
    DELIVERY_GUI,
    DELIVERY_VOICE,
    is_gui_delivery,
    lock_key,
    resolve_delivery,
)
from src.application.events import ThinkingUpdate, TurnCompleteEvent


class TestResolveDelivery:
    def test_explicit_delivery_wins(self):
        assert resolve_delivery("agent", "voice") == DELIVERY_VOICE
        assert resolve_delivery("voice", "gui") == DELIVERY_GUI

    def test_category_voice_defaults_voice(self):
        assert resolve_delivery("voice") == DELIVERY_VOICE

    def test_category_agent_defaults_gui(self):
        assert resolve_delivery("agent") == DELIVERY_GUI
        assert resolve_delivery(None) == DELIVERY_GUI

    def test_lock_key_scopes_by_delivery(self):
        assert lock_key("main:default:gui:abc", DELIVERY_GUI) != lock_key(
            "main:default:gui:abc", DELIVERY_VOICE
        )


class TestLimbCoordinatorGating:
    def test_delivery_from_event(self):
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        assert coord._delivery_from_event(
            ThinkingUpdate(thinking_text="x", delivery=DELIVERY_VOICE)
        ) == DELIVERY_VOICE
        assert is_gui_delivery(coord._delivery_from_event(
            ThinkingUpdate(thinking_text="x", delivery=DELIVERY_GUI)
        ))

    def test_voice_turn_complete_tracks_per_bucket(self):
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        voice = ("s1", DELIVERY_VOICE)
        gui = ("s1", DELIVERY_GUI)
        coord._completed_by_bucket[voice] = False
        coord._completed_by_bucket[gui] = False
        coord._mark_turn_complete(voice)
        assert coord._is_turn_complete(voice)
        assert not coord._is_turn_complete(gui)

    def test_autonomous_turn_does_not_clip_concurrent_user_turn(self):
        """Issue #208: an autonomous turn (own session_id) running concurrently with a
        user turn must not flip the user bucket's initiator or suppress its stream."""
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())

        user_bucket = ("main:default:gui:conv-1", DELIVERY_GUI)
        auto_bucket = ("main:default:heartbeat:pc", DELIVERY_GUI)

        # User turn starts (chat), then an autonomous turn starts on delivery=gui.
        coord._initiator_by_bucket[user_bucket] = "user"
        coord._initiator_by_bucket[auto_bucket] = "system"

        # The user's stream stays a chat turn; the autonomous stream is suppressed —
        # neither overwrote the other's bucket.
        assert coord._is_chat_turn(user_bucket) is True
        assert coord._is_chat_turn(auto_bucket) is False

    def test_is_streamable_turn_allows_voice_unlike_is_chat_turn(self):
        """Epic #291 Phase 2 regression: fragments (_on_event_token) must stream
        for voice turns too, not just GUI. Found live — voice replies never
        streamed sentence-by-sentence because _on_event_token gated on
        _is_chat_turn (GUI-only), so ResponseFragment was silently dropped for
        every voice-delivery turn."""
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())

        gui_bucket = ("s1", DELIVERY_GUI)
        voice_bucket = ("s1", DELIVERY_VOICE)
        coord._initiator_by_bucket[gui_bucket] = "user"
        coord._initiator_by_bucket[voice_bucket] = "user"

        assert coord._is_streamable_turn(gui_bucket) is True
        assert coord._is_streamable_turn(voice_bucket) is True
        # _is_chat_turn stays GUI-only (unchanged) — other call sites (tool
        # start/end, model loading, token usage) are UI-display-only concerns
        # that should NOT start firing for voice turns.
        assert coord._is_chat_turn(voice_bucket) is False

    def test_is_streamable_turn_excludes_autonomous_initiator(self):
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        auto_voice_bucket = ("s1", DELIVERY_VOICE)
        coord._initiator_by_bucket[auto_voice_bucket] = "system"
        assert coord._is_streamable_turn(auto_voice_bucket) is False

    @pytest.mark.asyncio
    async def test_broadcast_response_fragment_carries_turn_id_and_delivery(self):
        """Regression: the wire payload must carry turn_id (so GUIChadLimb can
        correlate the fragment to the right voice turn's sentence buffer) and
        delivery (so _is_voice_delivery routes it correctly)."""
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        ws = MagicMock()
        ws.send = AsyncMock()
        coord._active_websocket = ws

        await coord.ui_state._broadcast_response_fragment(
            "hello", initiator="user", bucket=("s1", DELIVERY_VOICE), client_turn_id="tid-42",
        )

        sent = ws.send.call_args[0][0]
        assert '"turn_id": "tid-42"' in sent or '"turn_id":"tid-42"' in sent
        assert '"delivery": "voice"' in sent or '"delivery":"voice"' in sent
        assert '"fragment": true' in sent or '"fragment":true' in sent

    @pytest.mark.asyncio
    async def test_on_event_status_allows_voice_unlike_previous_gui_only_gate(self):
        """Epic #293 Phase 3: a voice turn that escalates to the heavy agent
        pipeline gets no other feedback while it waits — status labels
        (TurnStatusEmitter.status) are the only available narration. _on_event_status used
        to hard-gate on is_gui_delivery, silently dropping every status for a
        voice turn (same class of bug as the ResponseFragment gating above)."""
        from src.application.events.events import StatusUpdate

        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        broadcasts = []

        async def fake_broadcast(label, delivery=None, client_turn_id=None):
            broadcasts.append((label, delivery, client_turn_id))

        coord.ui_state._broadcast_status = fake_broadcast
        coord.ui_state._on_event_status(StatusUpdate(
            status="Selecting tools…", delivery=DELIVERY_VOICE, client_turn_id="tid-1",
        ))
        await asyncio.sleep(0)  # let the scheduled task run
        assert broadcasts == [("Selecting tools…", DELIVERY_VOICE, "tid-1")]

    @pytest.mark.asyncio
    async def test_broadcast_status_carries_turn_id_and_delivery(self):
        """Regression: the wire payload must carry turn_id + delivery so
        GUIChadLimb can correlate the status label to the right voice turn and
        speak it as narration instead of dropping it."""
        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        ws = MagicMock()
        ws.send = AsyncMock()
        coord._active_websocket = ws

        await coord.ui_state._broadcast_status(
            "Selecting tools…", delivery=DELIVERY_VOICE, client_turn_id="tid-42",
        )

        sent = ws.send.call_args[0][0]
        assert '"turn_id": "tid-42"' in sent or '"turn_id":"tid-42"' in sent
        assert '"delivery": "voice"' in sent or '"delivery":"voice"' in sent
        assert '"state": "status"' in sent or '"state":"status"' in sent

    @pytest.mark.asyncio
    async def test_on_event_status_ignores_autonomous_delivery(self):
        """An unset/None delivery (e.g. a stray status not tagged to a turn)
        still passes through today's behavior — only a non-GUI/voice delivery
        should be dropped."""
        from src.application.events.events import StatusUpdate

        bus = MagicMock()
        coord = LimbCoordinator(bus, asyncio.Event())
        broadcasts = []

        async def fake_broadcast(label, delivery=None, client_turn_id=None):
            broadcasts.append((label, delivery, client_turn_id))

        coord.ui_state._broadcast_status = fake_broadcast
        coord.ui_state._on_event_status(StatusUpdate(status="Formulating…", delivery="silent"))
        await asyncio.sleep(0)  # let the scheduled task (if any) run
        assert broadcasts == []


class TestGUIChadLimbFilter:
    def _limb(self):
        window = MagicMock()
        limb = GUIChadLimb(window)
        return limb

    def test_voice_delivery_detected_by_tag(self):
        limb = self._limb()
        msg = ChadMessage(MessageType.AI_THOUGHT, {"delivery": "voice", "state": "thinking"})
        assert limb._is_voice_delivery(msg) is True

    def test_gui_delivery_not_voice(self):
        limb = self._limb()
        msg = ChadMessage(MessageType.AI_THOUGHT, {"delivery": "gui", "state": "thinking"})
        assert limb._is_voice_delivery(msg) is False

    def test_pending_voice_future_detected(self):
        limb = self._limb()
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        limb._voice_turn_futures["tid-1"] = fut
        msg = ChadMessage(MessageType.AI_RESPONSE, {"turn_id": "tid-1", "text": "hi"})
        assert limb._is_voice_delivery(msg) is True
        loop.close()


class TestGUIChadLimbVoiceStreaming:
    """Epic #291 Phase 2: streaming AI_RESPONSE fragments feed on_sentence."""

    def _limb(self):
        window = MagicMock()
        return GUIChadLimb(window)

    @pytest.mark.asyncio
    async def test_fragments_speak_sentences_as_they_close(self):
        limb = self._limb()
        turn_id = "tid-stream"
        loop = asyncio.get_running_loop()
        limb._voice_turn_futures[turn_id] = loop.create_future()

        sentences = []

        async def on_sentence(s):
            sentences.append(s)

        from src.core.tts.sentence_accumulator import SentenceAccumulator
        limb._voice_turn_streams[turn_id] = (SentenceAccumulator(), on_sentence)

        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "First one. ", "fragment": True, "delivery": "voice"},
        ))
        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "Second one. Tra", "fragment": True, "delivery": "voice"},
        ))
        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "iling bit.", "fragment": True, "delivery": "voice"},
        ))
        assert sentences == ["First one.", "Second one."]

        # Final (non-fragment) message flushes the trailing partial sentence
        # and resolves the future, but doesn't re-speak already-streamed text.
        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "First one. Second one. Trailing bit.", "delivery": "voice"},
        ))
        assert sentences == ["First one.", "Second one.", "Trailing bit."]
        assert limb._voice_turn_futures[turn_id].result() == "First one. Second one. Trailing bit."
        assert turn_id not in limb._voice_turn_streams

    @pytest.mark.asyncio
    async def test_no_stream_registered_falls_back_to_final_text_only(self):
        """Voice turns without an on_sentence callback (Phase 1 behavior) still
        just resolve on the final message; fragments are dropped."""
        limb = self._limb()
        turn_id = "tid-nostream"
        loop = asyncio.get_running_loop()
        limb._voice_turn_futures[turn_id] = loop.create_future()

        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "partial", "fragment": True, "delivery": "voice"},
        ))
        await limb.handle_message(ChadMessage(
            MessageType.AI_RESPONSE,
            {"turn_id": turn_id, "text": "full reply", "delivery": "voice"},
        ))
        assert limb._voice_turn_futures[turn_id].result() == "full reply"


class TestGUIChadLimbVoiceStatusNarration:
    """Epic #293 Phase 3: status labels narrate the escalate-to-agent wait."""

    def _limb(self):
        window = MagicMock()
        return GUIChadLimb(window)

    def _register_stream(self, limb, turn_id):
        spoken = []

        async def on_sentence(s):
            spoken.append(s)

        from src.core.tts.sentence_accumulator import SentenceAccumulator
        limb._voice_turn_streams[turn_id] = (SentenceAccumulator(), on_sentence)
        return spoken

    @pytest.mark.asyncio
    async def test_status_label_spoken_for_voice_turn(self):
        """Harness status labels are never TTS'd on voice (canned-filler bug)."""
        limb = self._limb()
        turn_id = "tid-status"
        spoken = self._register_stream(limb, turn_id)

        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Selecting tools…", "turn_id": turn_id, "delivery": "voice"},
        ))
        assert spoken == []

    @pytest.mark.asyncio
    async def test_early_processing_status_not_spoken_for_voice(self):
        """PROCESSING_LABELS must not TTS on voice — they were false fillers on opens."""
        limb = self._limb()
        turn_id = "tid-proc"
        spoken = self._register_stream(limb, turn_id)

        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Let me think…", "turn_id": turn_id, "delivery": "voice"},
        ))
        assert spoken == []

    @pytest.mark.asyncio
    async def test_formulating_status_not_spoken_for_voice(self):
        limb = self._limb()
        turn_id = "tid-form"
        spoken = self._register_stream(limb, turn_id)

        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Working on it…", "turn_id": turn_id, "delivery": "voice"},
        ))
        assert spoken == []

    @pytest.mark.asyncio
    async def test_status_label_debounced_if_spoken_recently(self):
        """Status TTS is fully disabled for voice — nothing spoken even twice."""
        limb = self._limb()
        turn_id = "tid-debounce"
        spoken = self._register_stream(limb, turn_id)

        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Selecting tools…", "turn_id": turn_id, "delivery": "voice"},
        ))
        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Formulating response…", "turn_id": turn_id, "delivery": "voice"},
        ))
        assert spoken == []

    @pytest.mark.asyncio
    async def test_status_label_dropped_without_registered_stream(self):
        """A voice turn with no on_sentence callback (Phase 1-only caller)
        must not raise — the status is silently skipped, same as before."""
        limb = self._limb()
        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Selecting tools…", "turn_id": "tid-none", "delivery": "voice"},
        ))  # must not raise

    @pytest.mark.asyncio
    async def test_gui_status_unaffected_by_voice_narration_path(self):
        """A GUI-delivery status message is untouched — still routed to the
        window status update, not treated as voice narration."""
        limb = self._limb()
        await limb.handle_message(ChadMessage(
            MessageType.AI_THOUGHT,
            {"state": "status", "text": "Selecting tools…", "initiator": "user"},
        ))
        limb.window.update_status.assert_called_once_with(state="thinking", text="Selecting tools…")
