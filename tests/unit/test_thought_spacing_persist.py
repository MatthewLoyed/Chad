"""
Reproduce: persisted thought text loses word spacing after chat reload.

Root cause chain (proven below):
  1. Live reasoning streams as many tiny `thought` deltas whose leading space
     lives ON the delta (e.g. "The", " user", " wants").
  2. On turn complete, GUIChadLimb._consolidate_and_sanitize_thoughts runs
     sanitize_response_text() on EACH delta independently.
  3. sanitize_response_text → strip_orchestration_markers → .strip(), which
     eats that leading space.
  4. History reload + ThinkingWidget concatenates the stripped deltas with
     `+`, so "The"+"user"+"wants" becomes "Theuserwants".

Live turns look fine because the FE merges deltas BEFORE any sanitize.
Navigating away/back loads the already-stripped metadata.thoughts.
"""

from src.remote.gui_limb import GUIChadLimb
from src.shared.plan_markers import sanitize_response_text


def _limb():
    return GUIChadLimb.__new__(GUIChadLimb)


def test_sanitize_strips_leading_space_on_token_deltas():
    """Unit of the bug: per-delta sanitize eats the space that lives on the delta."""
    chunks = ["The", " user", " wants", " me", " to", " find"]
    stripped = [sanitize_response_text(c) for c in chunks]
    assert stripped == ["The", "user", "wants", "me", "to", "find"]
    assert "".join(stripped) == "Theuserwantsmetofind"
    assert "".join(chunks) == "The user wants me to find"


def test_consolidate_thoughts_preserves_word_spacing_across_deltas():
    """
    Persist path must keep spaces when consolidating streamed thought tokens.

    Mirrors what ThinkingWidget does on history load: concatenate consecutive
    thought texts. After consolidate+sanitize, that join must still be readable.
    """
    limb = _limb()
    streamed = [
        {"type": "thought", "text": "The", "append": True, "timestamp": 1},
        {"type": "thought", "text": " user", "append": True, "timestamp": 2},
        {"type": "thought", "text": " wants", "append": True, "timestamp": 3},
        {"type": "thought", "text": " me", "append": True, "timestamp": 4},
        {"type": "thought", "text": " to", "append": True, "timestamp": 5},
        {"type": "thought", "text": " find", "append": True, "timestamp": 6},
        {"type": "thought", "text": " a", "append": True, "timestamp": 7},
        {"type": "thought", "text": " wedding", "append": True, "timestamp": 8},
        {"type": "thought", "text": " roadmap.", "append": True, "timestamp": 9},
    ]

    cleaned = limb._consolidate_and_sanitize_thoughts(streamed)
    thought_texts = [e["text"] for e in cleaned if e.get("type") in ("thought", "thinking", "reasoning")]
    joined = "".join(thought_texts)

    assert "The user wants me to find a wedding roadmap." in joined, (
        f"spaces were eaten during persist sanitize; got: {joined!r}"
    )
    assert "Theuserwants" not in joined
    # Coalesce into a single thought event (like speech), not N stripped crumbs.
    assert len(thought_texts) == 1


def test_consolidate_thoughts_flushes_across_tool_boundary():
    """Thoughts on either side of a tool stay separate but each keeps its spaces."""
    limb = _limb()
    streamed = [
        {"type": "thought", "text": "Looking", "append": True, "timestamp": 1},
        {"type": "thought", "text": " up", "append": True, "timestamp": 2},
        {"type": "thought", "text": " Drive.", "append": True, "timestamp": 3},
        {"type": "tool_start", "name": "google_drive_search", "timestamp": 4},
        {"type": "tool_end", "name": "google_drive_search", "success": True, "timestamp": 5},
        {"type": "thought", "text": "Found", "append": True, "timestamp": 6},
        {"type": "thought", "text": " the", "append": True, "timestamp": 7},
        {"type": "thought", "text": " file.", "append": True, "timestamp": 8},
    ]
    cleaned = limb._consolidate_and_sanitize_thoughts(streamed)
    thoughts = [e["text"] for e in cleaned if e.get("type") == "thought"]
    assert thoughts == ["Looking up Drive.", "Found the file."]
    assert [e["type"] for e in cleaned] == ["thought", "tool_start", "tool_end", "thought"]


def test_thinking_duration_calculation_from_events():
    """Verify thinking duration is measured from first thought to speech."""
    events = [
        {"type": "thought", "text": "Analyzing query", "timestamp": 1000},
        {"type": "tool_start", "name": "web_search", "timestamp": 2000},
        {"type": "tool_end", "name": "web_search", "timestamp": 3500},
        {"type": "speech", "text": "Here is the answer", "timestamp": 5000},
    ]
    first_thought = next((e for e in events if e.get("type") in ("thought", "thinking", "reasoning")), None)
    end_evt = next((e for e in events if e.get("type") == "speech"), None)
    end_ts = end_evt.get("timestamp") if (end_evt and end_evt.get("timestamp")) else 5000
    assert first_thought is not None
    assert end_ts - first_thought["timestamp"] == 4000


def test_consolidate_thoughts_calculates_individual_durations_across_tools():
    """Verify distinct durationMs is computed per thought slice rather than lumped."""
    limb = _limb()
    streamed = [
        # Block 1: 15s thought before tool (1000 to 16000)
        {"type": "thought", "text": "Searching", "append": True, "timestamp": 1000},
        {"type": "thought", "text": " notes", "append": True, "timestamp": 5000},
        {"type": "tool_start", "name": "notes_search", "timestamp": 16000},
        {"type": "tool_end", "name": "notes_search", "success": True, "timestamp": 18000},
        # Block 2: 3s thought after tool before speech (18000 to 21000)
        {"type": "thought", "text": "Formatting", "append": True, "timestamp": 18000},
        {"type": "thought", "text": " output", "append": True, "timestamp": 20000},
        {"type": "speech", "text": "Here is what I found.", "timestamp": 21000},
    ]
    cleaned = limb._consolidate_and_sanitize_thoughts(streamed)
    thoughts = [e for e in cleaned if e.get("type") == "thought"]
    assert len(thoughts) == 2
    assert thoughts[0]["text"] == "Searching notes"
    assert thoughts[0]["durationMs"] == 15000
    assert thoughts[1]["text"] == "Formatting output"
    assert thoughts[1]["durationMs"] == 3000

