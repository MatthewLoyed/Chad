"""Trips when a model NARRATES an action instead of taking it.

The failure this catches
------------------------
A local model ends its turn with "I'll now search for the venues and put the
comparison together" — and stops. It has handed back to a user who, on an
autonomous plan run, is not there. The plan then dies with zero steps complete,
and the transcript looks like the model simply quit.

The persona already forbids this (supervisor_node's anti-deferral block: "DO NOT
end your turn with a progress update or a promise to continue"), but a prompt
rule is a preference, not a guarantee — and the failure is silent when it slips.
This guard makes it self-correcting: catch the deferral mid-stream, cancel, and
resend with a direct instruction to act. One extra generation, which on local
inference costs wall-clock and nothing else.

Why mid-stream rather than after the fact: once the turn is banked, recovering
means another full orchestration round-trip. Cancelling at the moment the
sentence forms is far cheaper and the model has all the same context.

Deliberately conservative — a false positive costs a wasted generation, so:
  * only fires when tools were actually bound (a chat turn may promise freely),
  * only when NO tool call has been seen in this generation,
  * only past a minimum content length, so a fragment mid-sentence can't trip it,
  * once per generation (the adapter's `_nudge_retried` guard also caps resends).
"""

import re
from typing import Optional

from .base import Nudge, NudgeContext, NudgeResult
from src.shared.logger import setup_logger

logger = setup_logger("NarrationGuard")

# Future-tense commitments to do the work, rather than doing it. Anchored to
# first-person subjects so a description of someone else's action ("the script
# will fetch...") or a summary of results ("this will help you decide") is safe.
_DEFERRAL_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bi(?:'| a)?m going to\b",
        r"\bi will (?:now|next|go ahead|start|begin|proceed)\b",
        r"\bi'?ll (?:now|next|go ahead|start|begin|proceed)\b",
        r"\blet me (?:now |go ahead and )?(?:search|check|look|fetch|run|start|begin|create|build|write)\b",
        r"\bnext,? i(?:'| wi)ll\b",
        r"\b(?:now )?proceeding (?:to|with)\b",
        r"\bi'?ll get started\b",
        r"\bshall i (?:go ahead|proceed|start)\b",
        r"\bstarting the (?:search|research|analysis) now\b",
        r"\bwhat would you like (?:me )?to do\b",
        r"\bwhich (?:path|option|approach|action) (?:would you like|do you prefer)\b",
        r"\bwould you like me to (?:go ahead|proceed|upload|download|sync|start|add)\b",
        r"\bdo you want me to (?:go ahead|proceed|upload|download|sync|start|add)\b",
        r"\blet me know (?:if you(?:'d| would) like me to|how you(?:'d| would) like to proceed)\b",
        r"\bjust say (?:which|the word) and i(?:'| wi)ll\b",
    )
]

# Below this, the buffer is too short to judge — "I'll now" could be the start of
# "I'll now show you the results", which is a perfectly good preamble to content.
MIN_CONTENT_CHARS = 120

NUDGE_MESSAGE = (
    "You described what you were ABOUT to do or asked the user for confirmation/permission "
    "instead of taking action. Nobody is waiting to approve — you are running autonomously, "
    "so a promise to act or asking 'what would you like to do' ends the task with nothing delivered.\n\n"
    "Take the action NOW in this reply: emit the tool call(s) (e.g. downloading, uploading, "
    "adding, or delegating) needed to fulfill the request. If the work is genuinely finished, "
    "report the completed outcome directly without asking for confirmation."
)


class NarrationWithoutActionGuard(Nudge):
    """Fires when visible content promises an action but no tool call is emitted."""

    name = "narration_without_action"

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._content = ""
        self._tool_call_seen = False
        self._tools_available = False
        self._fired = False

    def observe(self, context: NudgeContext) -> None:
        if context.content_chunk:
            self._content += context.content_chunk
        if context.has_tool_call:
            self._tool_call_seen = True
        if context.tools_available:
            self._tools_available = True

    def check(self) -> Optional[NudgeResult]:
        if not self._enabled or self._fired:
            return None
        # A tool call anywhere in this generation means the model DID act; the
        # narration was just commentary alongside it.
        if self._tool_call_seen or not self._tools_available:
            return None
        if len(self._content) < MIN_CONTENT_CHARS:
            return None
        if not any(p.search(self._content) for p in _DEFERRAL_PATTERNS):
            return None

        self._fired = True
        logger.warning(
            "[ NARRATION_GUARD ] Model promised an action with no tool call "
            f"({len(self._content)} chars of content) — nudging it to act."
        )
        return NudgeResult(
            message=NUDGE_MESSAGE,
            disable_thinking=True,
            reason="narration_without_tool_call",
        )

    def reset(self) -> None:
        self._content = ""
        self._tool_call_seen = False
        self._tools_available = False
        self._fired = False
