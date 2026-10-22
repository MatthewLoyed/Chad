"""
Voice Mode Host (Limb-side).

Owns the hardware + wiring for the discrete "voice mode" pipeline in the desktop
client. The headless Brain has no microphone, so all audio lives here:

    openWakeWord "Hey Chad" -> earcon -> faster-whisper STT
        -> GUIChadLimb.run_voice_turn(transcript)  ── WebSocket ──▶ headless Brain
        ◀── reply text ──  Kokoro TTS (local)  -> re-arm

This is the *only* place voice mode is wired. The orchestration-layer
`VoiceModeController` stays dependency-free; this host injects the concrete
collaborators (audio, wake word, STT, the Brain turn, TTS, earcon, state hooks).

Opt-in via ``voice_mode.enabled``. Degrades gracefully: missing faster-whisper,
a missing/unloadable wake word model, or no audio hardware all log and no-op.
"""

import asyncio
from typing import Any, Optional

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.shared.voice_state import (
    REASON_TEXT,
    VoiceAvailability,
    VoicePhase,
    VoiceTurnState,
    VoiceUnavailableReason,
)

logger = setup_logger("VoiceModeHost")


class VoiceModeHost:
    """Builds and supervises the Limb-side voice mode pipeline."""

    def __init__(self, uplink_limb: Any, window: Any, loop: asyncio.AbstractEventLoop,
                 config: Optional[dict] = None, on_state: Optional[Any] = None):
        """
        Args:
            uplink_limb: GUIChadLimb — provides
                ``run_voice_turn(text, on_sentence) -> reply``.
            window: MainWindow — used for lightweight status feedback.
            loop: the Limb's asyncio loop (AudioHandler binds its callbacks to it).
            config: full config dict (defaults to ConfigManager.load_config()).
            on_state: optional extra hook (state: VoiceTurnState) -> None, e.g. to
                drive the buddy. Called alongside the built-in GUI push.
        """
        self._uplink = uplink_limb
        self._window = window
        self._loop = loop
        self._config = config or ConfigManager.load_config()
        self._extra_on_state = on_state

        self._audio_handler = None
        self._wake_word = None
        self._controller = None
        self._sound_manager = None
        # Last value sent to the buddy, so identical repeats aren't re-sent.
        self._last_buddy_listening: Optional[bool] = None

    async def start(self) -> bool:
        """Build and start voice mode. Returns True if armed, else False.

        Every failure path also publishes a ``VoiceAvailability`` with a
        machine-readable reason (issue #654) so the GUI can tell the user which
        piece is broken instead of just looking idle.
        """
        vm = self._config.get("voice_mode", {})
        if not vm.get("enabled", False):
            logger.debug("[ VOICE MODE ] : Disabled in config; not starting.")
            return self._publish_unavailable(VoiceUnavailableReason.DISABLED)

        # --- Audio hardware (mic + speaker for the earcon) ---
        try:
            from src.core.audio.audio_handler import AudioHandler

            audio_config = self._config.get("audio", {})
            self._audio_handler = AudioHandler(audio_config, loop=self._loop)
            await self._audio_handler.start_input()
            await self._audio_handler.start_output()
            if not self._audio_handler.input.is_active:
                await self._cleanup_audio()
                return self._publish_unavailable(
                    VoiceUnavailableReason.MIC_UNAVAILABLE,
                    detail=audio_config.get("preferred_device_name") or None,
                )
        except Exception as e:
            await self._cleanup_audio()
            return self._publish_unavailable(
                VoiceUnavailableReason.AUDIO_INIT_FAILED, detail=str(e)
            )

        # --- Wake word ("Hey Chad", openWakeWord by default) ---
        try:
            from src.core.audio.wake_word import WakeWordDetector

            wake_config = self._config.get("wake_word", {})
            sample_rate = audio_config.get("input_sample_rate") or audio_config.get("sample_rate", 16000)
            self._wake_word = WakeWordDetector(config=wake_config, audio_sample_rate=sample_rate)
        except Exception as e:
            await self._cleanup_audio()
            return self._publish_unavailable(
                VoiceUnavailableReason.WAKE_WORD_UNAVAILABLE, detail=str(e)
            )

        from src.shared.sound_manager import SoundManager

        self._sound_manager = SoundManager(config=self._config.get("sounds", {}))
        self._sound_manager.set_audio_output(self._audio_handler.output)

        # --- STT + recorder + controller ---
        from src.core.stt.stt_factory import get_stt_engine
        from src.core.audio.utterance_recorder import UtteranceRecorder
        from src.core.orchestration.voice_mode_controller import VoiceModeController
        from src.core.tts.tts_player import get_tts_player

        stt_engine = get_stt_engine()
        sample_rate = audio_config.get("input_sample_rate", 16000)

        play_sounds = vm.get("earcon", True)

        recorder = UtteranceRecorder(
            audio_source=self._audio_handler.input,
            silence_timeout=vm.get("silence_timeout", 1.2),
            max_seconds=vm.get("max_utterance_seconds", 15.0),
            vad_threshold=vm.get("vad_threshold", 0.01),
            start_timeout=vm.get("start_timeout", 6.0),
            # Only needed when the earcon is actually sounding into the mic.
            lead_in_seconds=(vm.get("earcon_guard_seconds", 0.4) if play_sounds else 0.0),
        )

        self._controller = VoiceModeController(
            wait_for_wake=self._make_wake_waiter(),
            recorder=recorder,
            stt_engine=stt_engine,
            run_turn=self._uplink.run_voice_turn,
            speak=get_tts_player().speak,
            on_listening=(self._play_listening_sound if play_sounds else None),
            on_recording_end=(self._play_stop_listening_sound if play_sounds else None),
            on_state=self._on_state,
            sample_rate=sample_rate,
            filler_timeout=vm.get("filler_timeout", 0.6),
            filler_phrases=vm.get("filler_phrases"),
            generate_filler=(self._make_dynamic_filler() if vm.get("dynamic_filler", True) else None),
            dynamic_filler_wait=vm.get("dynamic_filler_wait", 8.0),
        )
        if self._controller.start() is None:
            await self._cleanup_audio()
            return self._publish_unavailable(VoiceUnavailableReason.STT_UNAVAILABLE)

        self._publish_availability(VoiceAvailability.ok())
        return True

    def _publish_unavailable(self, reason: str, detail: Optional[str] = None) -> bool:
        """Log + publish a failure reason. Always returns False, so callers can
        ``return self._publish_unavailable(...)`` at each bail-out point."""
        text = REASON_TEXT.get(reason, {})
        suffix = f" ({detail})" if detail else ""
        logger.warning(
            f"[ VOICE MODE ] : Not armed - {text.get('message', reason)}{suffix}. "
            f"{text.get('remedy', '')}"
        )
        self._publish_availability(VoiceAvailability.failed(reason, detail))
        return False

    def _publish_availability(self, availability: VoiceAvailability) -> None:
        """Push arm/failure status to the GUI's persistent mic indicator."""
        push = getattr(self._window, "push_voice_availability", None)
        if not push:
            return
        try:
            push(availability.to_payload())
        except Exception as e:
            logger.debug(f"[ VOICE MODE ] : availability push failed: {e}")

    async def apply_enabled(self, enabled: bool) -> bool:
        """Arm or disarm voice mode at runtime (Settings toggle).

        Re-reads config on the way up so a freshly-persisted ``voice_mode.enabled``
        (and any device/threshold edits made alongside it) take effect without a
        restart. Returns the resulting armed state.
        """
        await self.stop()
        if not enabled:
            self._publish_availability(
                VoiceAvailability.failed(VoiceUnavailableReason.DISABLED)
            )
            return False
        self._config = ConfigManager.load_config()
        return await self.start()

    async def stop(self) -> None:
        """Stop the controller and release audio hardware."""
        if self._controller:
            await self._controller.stop()
            self._controller = None
        if self._wake_word:
            try:
                await self._wake_word.cleanup()
            except Exception:
                pass
            self._wake_word = None
        await self._cleanup_audio()

    async def _cleanup_audio(self) -> None:
        if self._audio_handler:
            try:
                await self._audio_handler.cleanup()
            except Exception:
                pass
            self._audio_handler = None

    def _make_dynamic_filler(self):
        """Build the dynamic-filler generator (epic #291 Phase 3): a quick call to
        the same fast local model used for the voice lane, so the filler phrase is
        tailored to the query instead of a canned rotation. Falls back to canned
        phrases automatically (see VoiceModeController) if LM Studio, the model, or
        this config is unavailable.

        Issue #654 problem 3 follow-up: a regex-only fast-lane pre-check (no model,
        can't double-load the embedding model) skips this call entirely for a
        confident match — only for `open` though. `open X` truly IS instant (tier 1
        executes it directly with no model call), so asking the model for a filler
        it'll never get to speak was pure waste, fighting LM Studio's VRAM budget for
        no reason. `search` used to be tier-1-instant too, but isn't anymore: search
        needs a real answer, not just an ack, so it now always goes through tier 2
        (~3-8s) or tier 3 — exactly the kind of wait a filler exists for. Gating it
        out the same way `open` is gated caused search commands to fall back to the
        generic canned rotation ("thinking about it...") instead of a query-tailored
        phrase, right when the wait is longest and a tailored filler matters most.
        """
        local_models = self._config.get("local_models", {})
        model = local_models.get("category_models", {}).get("voice")
        if not model:
            return None
        base_url = local_models.get("providers", {}).get("lmstudio", {}).get(
            "base_url", "http://localhost:1234/v1"
        )

        async def _generate(text: str):
            from src.core.orchestration.nodes.voice_fast_lane import is_instant_open_command
            from src.core.tts.dynamic_filler import generate_dynamic_filler

            # Instant open: real ack is first speech — don't burn a 9B call on a filler.
            if is_instant_open_command(text):
                return None
            return await generate_dynamic_filler(text, model=model, base_url=base_url)

        return _generate

    def _make_wake_waiter(self):
        """Build the wake-word awaiter the controller polls each turn."""
        async def _wait_for_wake() -> bool:
            try:
                async for chunk in self._audio_handler.input.read_chunks():
                    if self._wake_word.process_audio(chunk):
                        return True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[ VOICE MODE ] : Wake-word listen failed: {e}")
                await asyncio.sleep(0.5)
            return False  # stream ended; loop re-evaluates

        return _wait_for_wake

    def _play_listening_sound(self) -> None:
        """Play the configured activation sound when the wake word fires."""
        if self._sound_manager:
            self._sound_manager.play_sound("activation")

    def _play_stop_listening_sound(self) -> None:
        """Play the configured deactivate sound when utterance capture ends."""
        if self._sound_manager:
            self._sound_manager.play_sound("deactivate")

    def _on_state(self, state: VoiceTurnState) -> None:
        """Push the voice phase to the GUI, drive the buddy, and fan out to any extra hook.

        This deliberately does *not* go through ``window.update_status``. That
        channel is shared with turn lifecycle, and ``brainSocket`` pushes
        ``handleStateUpdate('ready')`` at several turn-end points, so any voice
        state written there is wiped by unrelated chat activity (issue #654).
        The voice pill reads its own store slice instead.
        """
        push = getattr(self._window, "push_voice_state", None)
        if push:
            try:
                push(state.to_payload())
            except Exception as e:
                logger.debug(f"[ VOICE MODE ] : voice state push failed: {e}")

        # Drive the desktop buddy: "listening" on wake, cleared once the turn begins
        # (the Brain's turn/tool events take over the buddy animation from there).
        self._signal_buddy(listening=(state.phase == VoicePhase.LISTENING))

        if self._extra_on_state:
            try:
                self._extra_on_state(state)
            except Exception as e:
                logger.debug(f"[ VOICE MODE ] : extra on_state hook failed: {e}")

    def _signal_buddy(self, listening: bool) -> None:
        """Fire-and-forget the listening state to the Brain (drives the buddy via SSE)."""
        # Phases now fire more often than the old four-state notifier — 'speaking'
        # alone repeats once per streamed sentence — and all but 'listening' send
        # the identical False. Only send on an actual change.
        if listening == self._last_buddy_listening:
            return
        self._last_buddy_listening = listening
        send = getattr(self._uplink, "send_voice_state", None)
        if not send:
            return
        try:
            self._loop.create_task(send(listening))
        except Exception as e:
            logger.debug(f"[ VOICE MODE ] : buddy signal failed: {e}")
