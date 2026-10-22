"""
Voice Mode Controller — discrete, turn-based hands-free voice pipeline.

This is the discrete "voice mode": a one-shot loop that is always armed for the
wake word, then transcribes a single command to text, runs it through a
*normal* Chad text turn, and speaks the reply. Per epic #291 Phase 1, the turn
no longer blocks speech silently: ``run_turn`` runs as a background task and,
if it hasn't resolved within ``filler_timeout``, a canned filler phrase is
spoken immediately so the interaction feels live rather than frozen. Per
Phase 2, ``run_turn`` also receives an ``on_sentence`` callback so a streaming
turn implementation can speak each sentence as it completes instead of
waiting for the full reply; the filler is skipped once the first sentence
starts (no need to interject once real speech has begun).

    [armed] wake word ("hey chad")
        -> earcon ("I'm listening")
        -> record one utterance (ended by silence)
        -> transcribe to text (STT)
        -> run a normal text turn (run_turn), speaking each sentence as it
           streams in and racing a filler-phrase timeout against the first one
        -> re-arm

Design: every collaborator is injected as a small interface or callable, so the
controller has no hard dependency on any wake word engine, the audio hardware, the STT
engine, or the orchestrator. The concrete wiring lives in the Limb
(``src/clients/desktop/voice_mode_host.py``), which owns the mic/speaker and the
WebSocket uplink to the headless Brain. This keeps the loop trivial to unit-test
with fakes.
"""

import asyncio
import inspect
import itertools
from typing import Awaitable, Callable, List, Optional

from src.core.stt.base_stt_engine import BaseSTTEngine
from src.shared.logger import setup_logger
from src.shared.voice_state import VoicePhase, VoiceTurnState

logger = setup_logger("VoiceModeController")

DEFAULT_FILLER_PHRASES = [
    "Hang on...",
    "Just a moment...",
]

# How long an instant-open turn may take before we speak a late fallback filler
# (tool missing / fall-through). Kept short of tier-2 latency but long enough
# for a normal open_browser ack.
OPEN_FILLER_GRACE_S = 2.0

# Wait this long for the LLM dynamic filler (or first real sentence) before any
# first-speech fallback. Canned "Working on it"/"Let me think" must not win the
# early race — local dynamic filler floors at ~1–4s.
DYNAMIC_FIRST_WAIT_S = 3.0

# Instant template fallbacks when regex classifies a wait-worthy intent but no
# generate_filler is wired. Open commands skip early fillers (see
# ``is_instant_open_command``).


def _first_speech_filler(text: str, canned_fallback: str) -> str:
    """Prefer a cheap intent template over the generic canned rotation."""
    try:
        from src.core.orchestration.nodes.voice_fast_lane import template_filler_for

        templated = template_filler_for(text)
        if templated:
            return templated
    except Exception:
        pass
    return canned_fallback


def _skip_filler_race(text: str) -> bool:
    """Open matches: real ack is first speech — no canned/dynamic race."""
    try:
        from src.core.orchestration.nodes.voice_fast_lane import is_instant_open_command

        return is_instant_open_command(text)
    except Exception:
        return False


class UtteranceRecorderProtocol:
    """Structural hint: anything with an async ``record() -> bytes``."""

    async def record(self) -> bytes:  # pragma: no cover - documentation only
        raise NotImplementedError


class VoiceModeController:
    """Orchestrates the always-armed, one-shot voice command loop."""

    def __init__(
        self,
        wait_for_wake: Callable[[], Awaitable[bool]],
        recorder: UtteranceRecorderProtocol,
        stt_engine: BaseSTTEngine,
        run_turn: Callable[[str, Optional[Callable[[str], Awaitable[None]]]], Awaitable[str]],
        speak: Callable[[str], Awaitable[None]],
        on_listening: Optional[Callable[[], None]] = None,
        on_recording_end: Optional[Callable[[], None]] = None,
        on_state: Optional[Callable[[VoiceTurnState], None]] = None,
        sample_rate: int = 16000,
        filler_timeout: float = 0.6,
        filler_phrases: Optional[List[str]] = None,
        generate_filler: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
        dynamic_filler_wait: float = 8.0,
    ):
        """
        Args:
            wait_for_wake: Awaits the wake word; resolves True on detection, or
                False to signal the loop should stop (e.g. shutdown).
            recorder: Captures one utterance, returning raw PCM bytes.
            stt_engine: Transcribes the captured PCM to text.
            run_turn: Runs a normal Chad text turn; takes the transcript and an
                ``on_sentence(sentence) -> Awaitable[None]`` callback, returns
                the full reply text. Implementations that support streaming
                should call ``on_sentence`` once per completed sentence
                (including any trailing partial sentence at stream end) so it
                can be spoken as it arrives; the returned string is otherwise
                only used as a fallback when no sentence was streamed.
            speak: Speaks the reply text (TTS).
            on_listening: Optional earcon hook fired right after wake detection.
            on_recording_end: Optional hook fired when utterance capture finishes.
            on_state: Optional notifier called with a ``VoiceTurnState`` on every
                phase transition (see ``src.shared.voice_state.VoicePhase``).
                Drives the GUI voice pill / ambient glow and the HUD. The
                ``heard`` phase carries the STT transcript so the UI can show
                what Chad actually understood.
            sample_rate: PCM sample rate handed to the STT engine.
            filler_timeout: Seconds to wait for ``run_turn`` before speaking a
                filler phrase instead of blocking silently (see
                docs epic #291 Phase 1). Set <= 0 to disable fillers.
            filler_phrases: Rotation of canned filler phrases spoken when
                ``run_turn`` hasn't finished within ``filler_timeout``.
                Defaults to ``DEFAULT_FILLER_PHRASES``.
            generate_filler: Optional async callable that takes the transcript and
                returns a short, query-tailored filler phrase (epic #291 Phase 3
                dynamic filler), e.g. a quick call to the same fast local model
                used for the voice lane. Started concurrently with ``run_turn``.
                On real hardware this essentially never resolves within
                ``filler_timeout`` (issue #654 follow-up: any local model call
                floors at several seconds regardless of prompt size, dwarfing
                that budget) — so the FIRST filler spoken is almost always
                canned. If ``run_turn`` is still pending once this later
                resolves, it's spoken as a follow-up (see ``dynamic_filler_wait``)
                instead of being wasted. On ``None``/exception, falls back to the
                canned rotation with no follow-up.
            dynamic_filler_wait: Seconds to additionally wait, after the first
                (likely canned) filler, for ``generate_filler`` to resolve so it
                can be spoken as a follow-up before the real answer — turns a
                long tier-2/tier-3 wait into "canned ack, then an actual
                tailored update" instead of dead air. No-ops if ``run_turn``
                or the dynamic filler is already done by then.
        """
        self._wait_for_wake = wait_for_wake
        self._recorder = recorder
        self._stt = stt_engine
        self._run_turn = run_turn
        self._speak = speak
        self._on_listening = on_listening
        self._on_recording_end = on_recording_end
        self._on_state = on_state
        self._sample_rate = sample_rate
        self._filler_timeout = filler_timeout
        self._filler_cycle = itertools.cycle(filler_phrases or DEFAULT_FILLER_PHRASES)
        self._generate_filler = generate_filler
        self._dynamic_filler_wait = dynamic_filler_wait

        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> Optional[asyncio.Task]:
        """Launch the loop as a background task. No-op if already running."""
        if self._running:
            logger.debug("Voice mode already running; start() ignored.")
            return self._task
        if not self._stt.is_available():
            logger.warning(
                "[ VOICE MODE ] : Not started - STT engine unavailable "
                "(pip install -r requirements-voice.txt)."
            )
            return None
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.warning(
            "[ VOICE MODE ] : Armed - listening for 'Hey Chad' "
            "(local wake word engine, not Gemini Live)."
        )
        return self._task

    async def stop(self) -> None:
        """Stop the loop and await task teardown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[ VOICE MODE ] : Stopped.")

    def _notify(self, phase: str, **fields) -> None:
        """Emit a phase transition. Never lets a bad listener break the turn."""
        if self._on_state:
            try:
                self._on_state(VoiceTurnState(phase=phase, **fields))
            except Exception as e:
                logger.debug(f"on_state callback failed: {e}")

    async def _run_loop(self) -> None:
        """One-shot loop: re-arms after every completed turn."""
        try:
            while self._running:
                await self._handle_one_turn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Never let the loop die silently — log and exit so a supervisor can
            # restart it, rather than leaving the mic falsely "armed".
            logger.error(f"Voice mode loop crashed: {e}", exc_info=True)
            self._running = False

    async def _handle_one_turn(self) -> None:
        """Run a single wake -> record -> transcribe -> run -> speak cycle."""
        self._notify(VoicePhase.ARMED)
        woke = await self._wait_for_wake()
        if not woke:
            # wait_for_wake returns False to signal shutdown / no detection.
            return

        # Earcon: audible confirmation that Chad is now listening.
        if self._on_listening:
            try:
                self._on_listening()
            except Exception as e:
                logger.debug(f"on_listening (earcon) failed: {e}")

        self._notify(VoicePhase.LISTENING)
        pcm = await self._recorder.record()
        if self._on_recording_end:
            try:
                self._on_recording_end()
            except Exception as e:
                logger.debug(f"on_recording_end (deactivate) failed: {e}")
        if not pcm:
            # Surfaced rather than swallowed: from the user's side a silent
            # re-arm is indistinguishable from the mic being broken.
            logger.debug("Empty utterance; re-arming.")
            self._notify(VoicePhase.ERROR, error="Didn't hear anything")
            return

        self._notify(VoicePhase.TRANSCRIBING)
        text = await self._stt.transcribe(pcm, sample_rate=self._sample_rate)
        if not text:
            logger.info("[ VOICE MODE ] : Transcript empty; re-arming.")
            self._notify(VoicePhase.ERROR, error="Couldn't make that out")
            return
        logger.info(f"[ VOICE MODE ] : Heard: {text!r}")
        self._notify(VoicePhase.HEARD, transcript=text)

        await self._run_turn_with_filler(text)

    async def _run_turn_with_filler(self, text: str) -> None:
        """Run ``run_turn`` in the background, speaking sentences as they stream.

        Never blocks silently on the full harness (RAG + tool selection +
        model). ``run_turn`` is given an ``on_sentence`` callback that speaks
        each completed sentence as soon as it streams in. If no sentence has
        started within ``filler_timeout``, a short filler is spoken first so
        the turn feels live; once a sentence starts, the filler is skipped.
        If ``run_turn`` never streamed anything (no ``on_sentence`` support),
        its full return value is spoken as a fallback.

        Instant open commands (regex tier-1) skip the filler race entirely —
        first speech is the real "Opening X." ack. Other waits prefer a cheap
        intent template ("Looking that up.") over the generic canned rotation;
        LLM dynamic filler remains a mid-wait follow-up only.
        """
        first_sentence = asyncio.Event()
        self._notify(VoicePhase.THINKING, transcript=text)

        async def on_sentence(sentence: str) -> None:
            first_sentence.set()
            self._notify(VoicePhase.SPEAKING, transcript=text, reply=sentence)
            await self._maybe_await(self._speak(sentence))

        task = asyncio.ensure_future(self._run_turn_safe(text, on_sentence))

        # Confident open: expect a near-instant ack. Skip the early canned race,
        # but if the Brain falls through (tool missing/fail) still speak a short
        # filler after a longer grace window rather than sitting in silence.
        if _skip_filler_race(text):
            open_grace = (
                max(self._filler_timeout, OPEN_FILLER_GRACE_S)
                if self._filler_timeout > 0
                else OPEN_FILLER_GRACE_S
            )
            waiter = asyncio.ensure_future(first_sentence.wait())
            try:
                await asyncio.wait(
                    {task, waiter}, timeout=open_grace, return_when=asyncio.FIRST_COMPLETED
                )
                if not first_sentence.is_set() and not task.done():
                    filler = "One sec."
                    self._notify(VoicePhase.SPEAKING, transcript=text, reply=filler)
                    await self._maybe_await(self._speak(filler))
            finally:
                if not waiter.done():
                    waiter.cancel()

            response = await task
            if response and not first_sentence.is_set():
                self._notify(VoicePhase.SPEAKING, transcript=text, reply=response)
                await self._maybe_await(self._speak(response))
            elif not response and not first_sentence.is_set():
                self._notify(
                    VoicePhase.ERROR, transcript=text, error="No reply from Chad"
                )
            return

        if self._filler_timeout <= 0:
            response = await task
            if response and not first_sentence.is_set():
                self._notify(VoicePhase.SPEAKING, transcript=text, reply=response)
                await self._maybe_await(self._speak(response))
            elif not response and not first_sentence.is_set():
                self._notify(
                    VoicePhase.ERROR, transcript=text, error="No reply from Chad"
                )
            return

        if self._filler_timeout > 0:
            waiter = asyncio.ensure_future(first_sentence.wait())
            filler_task = (
                asyncio.ensure_future(self._generate_filler_safe(text))
                if self._generate_filler
                else None
            )
            spoke_dynamic_filler = False
            # Prefer dynamic filler over canned when a generator is wired: wait up
            # to DYNAMIC_FIRST_WAIT_S for the LLM phrase (or first real sentence)
            # before any fallback. Without a generator, keep the short filler_timeout.
            first_wait = (
                max(float(self._filler_timeout), DYNAMIC_FIRST_WAIT_S)
                if filler_task is not None
                else float(self._filler_timeout)
            )
            try:
                wait_set = {task, waiter}
                if filler_task is not None:
                    wait_set.add(filler_task)
                await asyncio.wait(
                    wait_set, timeout=first_wait, return_when=asyncio.FIRST_COMPLETED
                )
                if not first_sentence.is_set() and not task.done():
                    filler = None
                    if filler_task is not None and filler_task.done():
                        try:
                            filler = filler_task.result()
                        except Exception:
                            filler = None
                        spoke_dynamic_filler = filler is not None
                    if not filler:
                        # Intent template only — never "Working on it" / "Let me think".
                        filler = _first_speech_filler(text, next(self._filler_cycle))
                    self._notify(VoicePhase.SPEAKING, transcript=text, reply=filler)
                    await self._maybe_await(self._speak(filler))

                # Follow-up: dynamic still cooking after a template was spoken.
                if (
                    not first_sentence.is_set()
                    and not task.done()
                    and filler_task is not None
                    and not filler_task.done()
                    and not spoke_dynamic_filler
                    and self._dynamic_filler_wait > 0
                ):
                    await asyncio.wait(
                        {task, waiter, filler_task},
                        timeout=self._dynamic_filler_wait,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not first_sentence.is_set() and not task.done() and filler_task.done():
                        followup = filler_task.result()
                        if followup:
                            self._notify(VoicePhase.SPEAKING, transcript=text, reply=followup)
                            await self._maybe_await(self._speak(followup))
            finally:
                if not waiter.done():
                    waiter.cancel()
                if filler_task is not None and not filler_task.done():
                    filler_task.cancel()

        response = await task
        if response and not first_sentence.is_set():
            # Nothing was streamed (non-streaming run_turn) — speak the full
            # reply as a fallback so behavior degrades gracefully.
            self._notify(VoicePhase.SPEAKING, transcript=text, reply=response)
            await self._maybe_await(self._speak(response))
        elif not response and not first_sentence.is_set():
            # The Brain returned nothing at all (timeout, disconnected uplink, or
            # a crashed turn). Previously this just re-armed in silence, which is
            # exactly the "I can't tell what went wrong" case from issue #654.
            self._notify(
                VoicePhase.ERROR, transcript=text, error="No reply from Chad"
            )

    async def _run_turn_safe(
        self, text: str, on_sentence: Callable[[str], Awaitable[None]]
    ) -> Optional[str]:
        try:
            return await self._run_turn(text, on_sentence)
        except Exception as e:
            logger.error(f"Turn execution failed: {e}", exc_info=True)
            return None

    async def _generate_filler_safe(self, text: str) -> Optional[str]:
        """Wrap ``generate_filler`` so a slow/broken generator never blocks or
        crashes the filler race — falls back to the canned rotation on any failure."""
        try:
            return await self._generate_filler(text)
        except Exception as e:
            logger.debug(f"Dynamic filler generation failed, using canned phrase: {e}")
            return None

    @staticmethod
    async def _maybe_await(result) -> None:
        """Await ``result`` if it is awaitable; otherwise return immediately."""
        if inspect.isawaitable(result):
            await result
