"""
Voice-mode state vocabulary shared by the Limb, the GUI bridge, and tests.

Two separate signals travel from the voice pipeline to the GUI, and they are
deliberately kept apart because they answer different questions:

``VoiceTurnState``
    *What is happening right now?* Emitted once per phase transition during a
    single wake -> record -> transcribe -> run -> speak cycle. Drives the
    transient voice pill and the ambient window glow.

``VoiceAvailability``
    *Is voice mode even working?* Emitted once at startup (and again whenever
    the user toggles voice mode) with a machine-readable reason when it is not.
    Drives the persistent mic indicator and the Settings panel. Before this
    existed, every failure path in ``VoiceModeHost.start()`` was a bare
    ``return False`` plus a log line, so a mic that never opened looked
    identical to a working setup (issue #654).

Both are plain dataclasses with a ``to_payload()`` dict, because they cross into
JavaScript through ``main_window.evaluate_js``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


class VoicePhase:
    """Phases of a single voice turn, in the order they occur.

    ``HEARD`` is the one that matters most for debugging: it carries the STT
    transcript, which previously never left the Limb process (it was only
    logged), leaving no way to tell a mis-transcription apart from a routing
    failure.
    """

    ARMED = "armed"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    HEARD = "heard"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class VoiceTurnState:
    """A single phase transition within one voice turn."""

    phase: str
    transcript: Optional[str] = None
    reply: Optional[str] = None
    error: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "transcript": self.transcript,
            "reply": self.reply,
            "error": self.error,
        }


class VoiceUnavailableReason:
    """Machine-readable codes for why voice mode is not armed.

    One code per ``return False`` path in ``VoiceModeHost.start()``, so the GUI
    can show the user what to actually fix instead of silently looking idle.
    """

    DISABLED = "disabled"
    AUDIO_INIT_FAILED = "audio_init_failed"
    MIC_UNAVAILABLE = "mic_unavailable"
    WAKE_WORD_UNAVAILABLE = "wake_word_unavailable"
    STT_UNAVAILABLE = "stt_unavailable"


# Human-readable message + the concrete next step, kept next to the codes so the
# GUI, the logs, and Settings all say the same thing.
REASON_TEXT: Dict[str, Dict[str, str]] = {
    VoiceUnavailableReason.DISABLED: {
        "message": "Voice mode is off",
        "remedy": "Turn it on in Settings -> Audio & Voice.",
    },
    VoiceUnavailableReason.AUDIO_INIT_FAILED: {
        "message": "Audio hardware failed to start",
        "remedy": "Check that no other app has exclusive control of the microphone.",
    },
    VoiceUnavailableReason.MIC_UNAVAILABLE: {
        "message": "Microphone did not open",
        "remedy": (
            "Set a default recording device in Windows sound settings, or set "
            "audio.preferred_device_name / input_device_index in config.yaml."
        ),
    },
    VoiceUnavailableReason.WAKE_WORD_UNAVAILABLE: {
        "message": "Wake word engine unavailable",
        "remedy": (
            "Install voice deps (pip install -r requirements-voice.txt) and check "
            "wake_word.keyword / keyword_path in config.yaml."
        ),
    },
    VoiceUnavailableReason.STT_UNAVAILABLE: {
        "message": "Speech-to-text unavailable",
        "remedy": "Install faster-whisper (pip install -r requirements-voice.txt).",
    },
}


@dataclass
class VoiceAvailability:
    """Whether voice mode is armed, and if not, why."""

    armed: bool
    reason: Optional[str] = None
    detail: Optional[str] = None

    @classmethod
    def ok(cls) -> "VoiceAvailability":
        return cls(armed=True)

    @classmethod
    def failed(cls, reason: str, detail: Optional[str] = None) -> "VoiceAvailability":
        return cls(armed=False, reason=reason, detail=detail)

    def to_payload(self) -> Dict[str, Any]:
        text = REASON_TEXT.get(self.reason or "", {})
        return {
            "armed": self.armed,
            "reason": self.reason,
            "message": text.get("message"),
            "remedy": text.get("remedy"),
            # Exception text / device name — the specific bit that varies per machine.
            "detail": self.detail,
        }
