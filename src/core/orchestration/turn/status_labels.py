"""Rotating status labels for the pre-first-token working indicator.

Picked randomly each turn so the UI stays fresh rather than showing the same
canned string every message.

These live here, not on ``turn_coordinator``, because both the coordinator and
``turn.tool_selector`` need them. While they were module-level constants on the
coordinator, ``tool_selector`` had to reach *back up* into its own parent with a
function-level ``from ...turn_coordinator import _SELECTING_LABELS`` to dodge a
circular import at module scope. A leaf module with no internal imports is the
fix: everyone imports downward.
"""

from __future__ import annotations

PROCESSING_LABELS = [
    "Processing prompt…",
    "Reading your message…",
    "Understanding request…",
    "Analyzing query…",
    "On it…",
    "Let me think…",
]

SELECTING_LABELS = [
    "Selecting tools…",
    "Choosing capabilities…",
    "Checking my toolkit…",
    "Figuring out the right tools…",
    "Picking my approach…",
]

FORMULATING_LABELS = [
    "Formulating response…",
    "Putting it together…",
    "Working on it…",
    "Writing it up…",
    "Crafting a reply…",
    "Almost there…",
]

CHAT_LABELS = [
    "Preparing reply…",
    "Getting ready…",
    "Quick chat mode…",
]

# Honest phase labels keyed to TurnPerfTracker marks — one random pick per step.
PHASE_LABELS = {
    "history": ["Loading conversation…", "Reading chat history…"],
    "intent": ["Choosing approach…", "Routing request…"],
    "mcp_wait": ["Starting tool servers…", "Preparing integrations…"],
    "tools": ["Capabilities ready…", "Toolkit prepared…"],
    "chat_tools": ["Preparing reply…", "Getting ready…"],
    "prompt": ["Assembling context…", "Building prompt…"],
    "tokens": ["Measuring context…", "Sizing prompt…"],
    "preflight": ["Optimizing context…", "Trimming history…"],
    "checkpoint": ["Restoring session…", "Loading thread state…"],
}

__all__ = [
    "PROCESSING_LABELS",
    "SELECTING_LABELS",
    "FORMULATING_LABELS",
    "CHAT_LABELS",
    "PHASE_LABELS",
]
