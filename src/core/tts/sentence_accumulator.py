"""
Buffers streamed text deltas and yields complete sentences as they close.

Used to pipe live-voice text-mode streaming fragments (AI_RESPONSE with
``fragment: true``) into TTS sentence-by-sentence, so Kokoro can start
speaking the first completed sentence while the rest of the reply is still
generating (epic #291 Phase 2), instead of waiting for the full string.
"""

import re
from typing import List, Optional

_SENTENCE_END_RE = re.compile(r"([.!?])(\s+)")


class SentenceAccumulator:
    """Stateful buffer: feed deltas in, get completed sentences out."""

    def __init__(self):
        self._buffer = ""

    def feed(self, delta: str) -> List[str]:
        """Append ``delta``; return any sentences that closed as a result."""
        if not delta:
            return []
        self._buffer += delta
        sentences: List[str] = []
        while True:
            match = _SENTENCE_END_RE.search(self._buffer)
            if not match:
                break
            end = match.end()
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:]
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> Optional[str]:
        """Return and clear any trailing partial sentence (call at stream end)."""
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None
