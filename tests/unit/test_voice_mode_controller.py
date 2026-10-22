r"""
Unit tests for the discrete voice-mode pipeline.

Exercises VoiceModeController and UtteranceRecorder with fakes only — no audio
hardware, no Whisper model, no network. Run:

    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest tests/unit/test_voice_mode_controller.py -q
"""

import asyncio
import struct

import pytest

from src.core.audio.utterance_recorder import UtteranceRecorder
from src.core.orchestration.voice_mode_controller import VoiceModeController
from src.shared.voice_state import VoicePhase


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def _pcm(level: int, n: int = 320) -> bytes:
    """n samples of constant amplitude `level` as 16-bit PCM."""
    return struct.pack("<%dh" % n, *([level] * n))


class FakeAudioSource:
    """Yields a scripted sequence of (level, chunk) then stops."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def read_chunks(self):
        for c in self._chunks:
            yield c

    def get_audio_level(self, audio_data):
        # Mirror AudioInput.get_audio_level: RMS normalized to [0,1].
        import numpy as np

        arr = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        return float((arr**2).mean() ** 0.5 / 32768.0)


class FakeSTT:
    def __init__(self, text="turn on the lamp", available=True):
        self._text = text
        self._available = available
        self.received = None

    def is_available(self):
        return self._available

    async def transcribe(self, pcm, sample_rate=16000):
        self.received = pcm
        return self._text


# --------------------------------------------------------------------------- #
# UtteranceRecorder
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_recorder_captures_until_silence():
    loud = _pcm(8000)   # well above threshold
    quiet = _pcm(0)     # silence
    # speech for 3 chunks, then sustained silence to trip the timeout
    source = FakeAudioSource([loud, loud, loud] + [quiet] * 50)
    rec = UtteranceRecorder(
        source, silence_timeout=0.0, max_seconds=10.0, vad_threshold=0.01, start_timeout=5.0
    )
    pcm = await rec.record()
    assert len(pcm) > 0
    # Should include the speech frames (recording started).
    assert len(pcm) >= len(loud) * 3


@pytest.mark.asyncio
async def test_recorder_aborts_when_no_speech():
    quiet = _pcm(0)
    source = FakeAudioSource([quiet] * 5)
    rec = UtteranceRecorder(
        source, silence_timeout=1.0, max_seconds=10.0, vad_threshold=0.01, start_timeout=0.0
    )
    pcm = await rec.record()
    assert pcm == b""


# --------------------------------------------------------------------------- #
# VoiceModeController
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_full_turn_pipeline_runs_in_order():
    events = []

    async def wait_for_wake():
        events.append("wake")
        return True

    class Rec:
        async def record(self):
            events.append("record")
            return _pcm(8000)

    stt = FakeSTT(text="hello chad")

    async def run_turn(text, on_sentence):
        events.append(f"run:{text}")
        return "hi there"

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=stt,
        run_turn=run_turn,
        speak=speak,
        on_listening=lambda: events.append("earcon"),
        on_recording_end=lambda: events.append("stop"),
    )

    await ctrl._handle_one_turn()

    assert events == ["wake", "earcon", "record", "stop", "run:hello chad", "speak:hi there"]
    assert stt.received is not None


@pytest.mark.asyncio
async def test_empty_transcript_skips_run_and_speak():
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        events.append("run")
        return "should not happen"

    async def speak(text):
        events.append("speak")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text=""),  # empty transcript
        run_turn=run_turn,
        speak=speak,
    )
    await ctrl._handle_one_turn()
    assert events == []  # neither run nor speak fired


@pytest.mark.asyncio
async def test_slow_turn_speaks_filler_then_real_response():
    """run_turn slower than filler_timeout: filler is spoken first, then the
    real reply once run_turn resolves (epic #291 Phase 1)."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        events.append("run")
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,  # shorter than run_turn's sleep, so filler fires
        filler_phrases=["Let me think..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Let me think...", "run", "speak:the real answer"]


@pytest.mark.asyncio
async def test_fast_turn_skips_filler():
    """run_turn resolving within filler_timeout: no filler is spoken."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        return "fast answer"

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=5.0,
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:fast answer"]


@pytest.mark.asyncio
async def test_run_turn_exception_is_caught_after_filler():
    """A crashing run_turn still re-arms the loop instead of propagating."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["One sec..."],
    )
    await ctrl._handle_one_turn()  # must not raise

    assert events == ["speak:One sec..."]  # filler spoken, no final response


@pytest.mark.asyncio
async def test_streaming_turn_speaks_sentences_as_they_arrive_no_filler():
    """A streaming run_turn that calls on_sentence promptly skips the filler
    entirely and speaks each sentence in order (epic #291 Phase 2)."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await on_sentence("First sentence.")
        await asyncio.sleep(0.05)  # slower second sentence, still no filler
        await on_sentence("Second sentence.")
        return "First sentence. Second sentence."

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,  # would fire if streaming hadn't already started
        filler_phrases=["Let me think..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:First sentence.", "speak:Second sentence."]


@pytest.mark.asyncio
async def test_slow_first_sentence_still_gets_filler():
    """If even the first sentence is slower than filler_timeout, the filler
    plays before it, same as the non-streaming fallback."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        await on_sentence("Delayed sentence.")
        return "Delayed sentence."

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["One sec..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:One sec...", "speak:Delayed sentence."]


@pytest.mark.asyncio
async def test_dynamic_filler_used_when_ready():
    """epic #291/#293 Phase 3: a fast generate_filler result is spoken instead of
    the canned rotation when it resolves before the filler-timeout race fires."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    async def generate_filler(text):
        return f"Dynamic filler for {text}"

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Let me think..."],
        generate_filler=generate_filler,
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Dynamic filler for hello chad", "speak:the real answer"]


@pytest.mark.asyncio
async def test_dynamic_filler_falls_back_to_canned_on_timeout(monkeypatch):
    """A generate_filler that hasn't resolved by the dynamic-first wait falls
    back to template/canned, not a hang."""
    import src.core.orchestration.voice_mode_controller as vmc

    monkeypatch.setattr(vmc, "DYNAMIC_FIRST_WAIT_S", 0.05)
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.2)
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    async def generate_filler(text):
        await asyncio.sleep(1.0)  # much slower than DYNAMIC_FIRST_WAIT_S
        return "too slow"

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Hang on..."],
        generate_filler=generate_filler,
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Hang on...", "speak:the real answer"]


@pytest.mark.asyncio
async def test_dynamic_filler_spoken_as_followup_once_ready(monkeypatch):
    """If a short template wins the first wait, dynamic still speaks as follow-up."""
    import src.core.orchestration.voice_mode_controller as vmc

    monkeypatch.setattr(vmc, "DYNAMIC_FIRST_WAIT_S", 0.02)
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.15)
        events.append("run")
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    async def generate_filler(text):
        await asyncio.sleep(0.06)  # slower than DYNAMIC_FIRST_WAIT_S, faster than run_turn
        return "Looking into that now"

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Hang on..."],
        generate_filler=generate_filler,
        dynamic_filler_wait=1.0,
    )
    await ctrl._handle_one_turn()

    assert events == [
        "speak:Hang on...",
        "speak:Looking into that now",
        "run",
        "speak:the real answer",
    ]


@pytest.mark.asyncio
async def test_no_followup_if_dynamic_filler_never_resolves(monkeypatch):
    """dynamic_filler_wait itself must not hang forever — if the dynamic filler
    also times out, the real answer still arrives with no extra filler spoken."""
    import src.core.orchestration.voice_mode_controller as vmc

    monkeypatch.setattr(vmc, "DYNAMIC_FIRST_WAIT_S", 0.05)
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.2)
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    async def generate_filler(text):
        await asyncio.sleep(10.0)  # never resolves within the test's budgets
        return "too slow"

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Hang on..."],
        generate_filler=generate_filler,
        dynamic_filler_wait=0.05,
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Hang on...", "speak:the real answer"]


@pytest.mark.asyncio
async def test_dynamic_filler_falls_back_to_canned_on_exception():
    """A crashing generate_filler must not blow up the turn — falls back to canned."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        return "the real answer"

    async def speak(text):
        events.append(f"speak:{text}")

    async def generate_filler(text):
        raise RuntimeError("LM Studio unreachable")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello chad"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Let me think..."],
        generate_filler=generate_filler,
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Let me think...", "speak:the real answer"]


@pytest.mark.asyncio
async def test_start_noops_when_stt_unavailable():
    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return b""

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(available=False),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
    )
    task = ctrl.start()
    assert task is None
    assert ctrl.is_running is False


# --------------------------------------------------------------------------- #
# Loop-level crash safety (issue #216: an unexpected failure must never leave
# the mic silently "armed" with a dead background task).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stt_crash_stops_the_loop_instead_of_dying_silently():
    """Unlike run_turn (wrapped by _run_turn_safe), a raise from stt.transcribe()
    itself is NOT caught per-turn — _run_loop's outer handler is the safety net.
    Confirms it actually engages: is_running flips False rather than the
    background task just vanishing with an unhandled exception."""

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    class CrashingSTT:
        def is_available(self):
            return True

        async def transcribe(self, pcm, sample_rate=16000):
            raise RuntimeError("whisper model crashed")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=CrashingSTT(),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
    )
    task = ctrl.start()
    assert task is not None

    for _ in range(50):
        if not ctrl.is_running:
            break
        await asyncio.sleep(0.01)

    assert ctrl.is_running is False, "the loop's outer guard must catch the crash and disarm"
    await ctrl.stop()


@pytest.mark.asyncio
async def test_wait_for_wake_false_rearms_without_recording():
    """wait_for_wake() returning False means 'no detection this cycle' (or
    shutdown) — must re-loop without ever touching the recorder/STT."""

    calls = {"wake": 0}

    async def wait_for_wake():
        # Real awaits (not a bare async no-op) so the loop actually yields to the
        # event loop each cycle instead of spinning synchronously forever.
        await asyncio.sleep(0)
        calls["wake"] += 1
        if calls["wake"] == 3:
            return True  # one detection...
        if calls["wake"] > 3:
            return False  # ...then no more detections; nothing left to do but idle
        return False  # False twice first

    class Rec:
        async def record(self):
            await asyncio.sleep(0)
            return b""  # empty utterance -> re-arms too, ending the scenario

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
    )
    task = ctrl.start()
    await asyncio.sleep(0.05)
    await ctrl.stop()

    assert calls["wake"] >= 3, "must keep re-arming across False wake results, not stop early"


# --------------------------------------------------------------------------- #
# Phase notifications (issue #654 — GUI feedback for a voice turn)
# --------------------------------------------------------------------------- #
def _phase_collector():
    """Collect VoiceTurnState objects emitted via on_state."""
    seen = []
    return seen, seen.append


@pytest.mark.asyncio
async def test_phases_report_the_full_turn_including_transcript():
    """A normal turn walks listening -> transcribing -> heard -> thinking ->
    speaking, and the transcript rides along from 'heard' onward.

    The transcript is the point: before this, it was only logged, so the GUI had
    no way to show what Chad actually understood."""
    seen, on_state = _phase_collector()

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await on_sentence("Turning it on.")
        return "Turning it on."

    ctrl = VoiceModeController(
        wait_for_wake=lambda: asyncio.sleep(0, result=True),
        recorder=Rec(),
        stt_engine=FakeSTT(text="turn on the lamp"),
        run_turn=run_turn,
        speak=lambda t: asyncio.sleep(0),
        on_state=on_state,
        filler_timeout=0,  # no filler, so the phase order is unambiguous
    )
    await ctrl._handle_one_turn()

    phases = [s.phase for s in seen]
    assert phases == [
        VoicePhase.ARMED,
        VoicePhase.LISTENING,
        VoicePhase.TRANSCRIBING,
        VoicePhase.HEARD,
        VoicePhase.THINKING,
        VoicePhase.SPEAKING,
    ]

    heard = seen[3]
    assert heard.transcript == "turn on the lamp"
    # Transcript stays attached through the rest of the turn so the pill can keep
    # showing it while Chad thinks and speaks.
    assert seen[4].transcript == "turn on the lamp"
    assert seen[5].transcript == "turn on the lamp"
    assert seen[5].reply == "Turning it on."


@pytest.mark.asyncio
async def test_empty_utterance_reports_an_error_phase():
    """Silence after the earcon must be visible, not a silent re-arm — a silent
    re-arm is indistinguishable from a broken mic."""
    seen, on_state = _phase_collector()

    class Rec:
        async def record(self):
            return b""

    ctrl = VoiceModeController(
        wait_for_wake=lambda: asyncio.sleep(0, result=True),
        recorder=Rec(),
        stt_engine=FakeSTT(),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
        on_state=on_state,
    )
    await ctrl._handle_one_turn()

    assert [s.phase for s in seen] == [VoicePhase.ARMED, VoicePhase.LISTENING, VoicePhase.ERROR]
    assert seen[-1].error


@pytest.mark.asyncio
async def test_empty_transcript_reports_an_error_phase():
    """Audio captured but STT produced nothing — also surfaced, not swallowed."""
    seen, on_state = _phase_collector()

    class Rec:
        async def record(self):
            return _pcm(8000)

    ctrl = VoiceModeController(
        wait_for_wake=lambda: asyncio.sleep(0, result=True),
        recorder=Rec(),
        stt_engine=FakeSTT(text=""),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
        on_state=on_state,
    )
    await ctrl._handle_one_turn()

    assert seen[-1].phase == VoicePhase.ERROR
    assert seen[-1].error


@pytest.mark.asyncio
async def test_no_reply_from_brain_reports_an_error_phase():
    """run_turn returning "" (timeout / disconnected uplink / crashed turn) is a
    failure the user can see, rather than a turn that just evaporates."""
    seen, on_state = _phase_collector()

    class Rec:
        async def record(self):
            return _pcm(8000)

    ctrl = VoiceModeController(
        wait_for_wake=lambda: asyncio.sleep(0, result=True),
        recorder=Rec(),
        stt_engine=FakeSTT(text="turn on the lamp"),
        run_turn=lambda t, o: asyncio.sleep(0, result=""),
        speak=lambda t: asyncio.sleep(0),
        on_state=on_state,
        filler_timeout=0,
    )
    await ctrl._handle_one_turn()

    assert seen[-1].phase == VoicePhase.ERROR
    assert seen[-1].transcript == "turn on the lamp"


@pytest.mark.asyncio
async def test_broken_on_state_listener_does_not_break_the_turn():
    """A throwing GUI listener must never take the voice loop down with it."""
    spoken = []

    def bad_listener(_state):
        raise RuntimeError("GUI bridge is gone")

    class Rec:
        async def record(self):
            return _pcm(8000)

    ctrl = VoiceModeController(
        wait_for_wake=lambda: asyncio.sleep(0, result=True),
        recorder=Rec(),
        stt_engine=FakeSTT(text="hello"),
        run_turn=lambda t, o: asyncio.sleep(0, result="hi"),
        speak=lambda t: spoken.append(t) or asyncio.sleep(0),
        on_state=bad_listener,
        filler_timeout=0,
    )
    await ctrl._handle_one_turn()

    assert spoken == ["hi"]


# --------------------------------------------------------------------------- #
# Earcon lead-in guard (#654 — "wake -> beep -> immediately closes" loop)
# --------------------------------------------------------------------------- #
class _StepClock:
    """Deterministic monotonic clock advancing a fixed step per call."""

    def __init__(self, step: float = 0.1):
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        now = self._t
        self._t += self._step
        return now


@pytest.mark.asyncio
async def test_earcon_alone_does_not_produce_an_utterance():
    """THE wake-spam regression.

    The activation earcon plays on a background thread, so it is still sounding
    once record() starts, and at vad_threshold it reads as speech. Without a
    lead-in guard the recorder end-points on the beep and hands STT a clip
    containing no command — which transcribes to nothing, re-arms, and beeps
    again. With the guard, the beep is discarded and the recorder correctly
    reports that nobody spoke.
    """
    beep = _pcm(8000)
    quiet = _pcm(0)
    # First call is start_time (t=0.0); chunks are then seen at 0.1, 0.2, ...
    source = FakeAudioSource([beep] * 3 + [quiet] * 40)

    rec = UtteranceRecorder(
        source,
        silence_timeout=0.5,
        max_seconds=10.0,
        vad_threshold=0.01,
        start_timeout=1.0,
        lead_in_seconds=0.35,  # covers the 3 beep chunks at t=0.1/0.2/0.3
        time_source=_StepClock(0.1),
    )
    assert await rec.record() == b""


@pytest.mark.asyncio
async def test_guard_discards_the_earcon_but_keeps_the_command():
    """Audio after the guard is captured normally — the guard must not eat the
    user's actual command, only the beep in front of it."""
    beep = _pcm(8000)
    speech = _pcm(9000)
    quiet = _pcm(0)
    source = FakeAudioSource([beep] * 3 + [speech] * 4 + [quiet] * 20)

    rec = UtteranceRecorder(
        source,
        silence_timeout=0.0,  # end on the first silent chunk after speech
        max_seconds=10.0,
        vad_threshold=0.01,
        start_timeout=2.0,
        lead_in_seconds=0.35,
        time_source=_StepClock(0.1),
    )
    pcm = await rec.record()

    # 4 speech chunks + the single silent chunk that ended it — the 3 beeps are gone.
    assert len(pcm) == len(speech) * 4 + len(quiet)


@pytest.mark.asyncio
async def test_without_guard_the_earcon_is_captured_as_speech():
    """Characterises the pre-fix behaviour, so a regression is unambiguous:
    with no guard the same beep-only audio yields a non-empty 'utterance'."""
    beep = _pcm(8000)
    quiet = _pcm(0)
    source = FakeAudioSource([beep] * 3 + [quiet] * 40)

    rec = UtteranceRecorder(
        source,
        silence_timeout=0.0,
        max_seconds=10.0,
        vad_threshold=0.01,
        start_timeout=1.0,
        lead_in_seconds=0.0,
        time_source=_StepClock(0.1),
    )
    assert await rec.record() != b""


@pytest.mark.asyncio
async def test_start_timeout_budget_excludes_the_guard():
    """start_timeout is the patience for the *speaker*, so the guard must not
    eat into it — otherwise enabling the earcon silently shortens the window."""
    quiet = _pcm(0)
    source = FakeAudioSource([quiet] * 40)

    clock = _StepClock(0.1)
    rec = UtteranceRecorder(
        source,
        silence_timeout=1.0,
        max_seconds=10.0,
        vad_threshold=0.01,
        start_timeout=1.0,
        lead_in_seconds=0.5,
        time_source=clock,
    )
    assert await rec.record() == b""
    # Gave up at guard (0.5) + start_timeout (1.0) ≈ 1.6 on the step grid,
    # not at 1.0 — i.e. the guard was excluded from the budget.
    assert clock() >= 1.6


@pytest.mark.asyncio
async def test_open_command_skips_canned_filler_even_when_turn_is_slow():
    """Instant opens must not speak "Let me think..." before "Opening youtube."
    within the early filler window; a short grace covers normal open_browser latency."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)  # slower than filler_timeout, within open grace
        return "Opening youtube."

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="open youtube"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Let me think..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Opening youtube."]
    assert not any("Let me think" in e for e in events)


@pytest.mark.asyncio
async def test_open_fallback_filler_if_ack_never_arrives_quickly(monkeypatch):
    """If tier-1 open falls through (tool fail → slow path), speak a late filler
    after the open grace window instead of dead air."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.12)
        return "Couldn't open that."

    async def speak(text):
        events.append(f"speak:{text}")

    import src.core.orchestration.voice_mode_controller as vmc

    monkeypatch.setattr(vmc, "OPEN_FILLER_GRACE_S", 0.05)

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="open youtube"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Let me think..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:One sec.", "speak:Couldn't open that."]
    assert not any("Let me think" in e for e in events)


@pytest.mark.asyncio
async def test_search_uses_template_filler_before_answer():
    """Non-open waits prefer an intent template over the generic canned rotation."""
    events = []

    async def wait_for_wake():
        return True

    class Rec:
        async def record(self):
            return _pcm(8000)

    async def run_turn(text, on_sentence):
        await asyncio.sleep(0.05)
        return "It's going to rain."

    async def speak(text):
        events.append(f"speak:{text}")

    ctrl = VoiceModeController(
        wait_for_wake=wait_for_wake,
        recorder=Rec(),
        stt_engine=FakeSTT(text="search for the weather tomorrow"),
        run_turn=run_turn,
        speak=speak,
        filler_timeout=0.01,
        filler_phrases=["Let me think..."],
    )
    await ctrl._handle_one_turn()

    assert events == ["speak:Looking that up.", "speak:It's going to rain."]
