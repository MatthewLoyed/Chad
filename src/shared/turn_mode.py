"""
Canonical turn modes — single vocabulary across router, Brain, and UI.

| Mode   | UI dropdown | Backend category | Pipeline                          |
|--------|-------------|------------------|-----------------------------------|
| chat   | Chat        | chat             | Fast lane: no RAG/tools           |
| agent  | Agent       | agent            | Supervisor + hybrid tool selection|
| plan   | Plan        | planning         | Planner → approval → supervisor   |
| auto   | Auto        | auto             | Router classifies → one of above  |

Legacy labels (conversation, command, planning) normalize into the canonical lanes.
"""

from __future__ import annotations

from typing import Literal, Optional

TurnMode = Literal["chat", "agent", "plan"]
UiTurnMode = Literal["auto", "agent", "chat", "plan"]

CANONICAL_MODES = frozenset({"chat", "agent", "plan"})

# Router / legacy classifier output → canonical lane
_ROUTER_ALIASES = {
    "chat": "chat",
    "conversation": "chat",
    "command": "agent",
    "agent": "agent",
    "planning": "plan",
    "plan": "plan",
}

UI_TO_CANONICAL: dict[str, Optional[TurnMode]] = {
    "auto": None,
    "agent": "agent",
    "chat": "chat",
    "plan": "plan",
}

CANONICAL_TO_UI: dict[TurnMode, UiTurnMode] = {
    "chat": "chat",
    "agent": "agent",
    "plan": "plan",
}

CANONICAL_TO_CATEGORY: dict[TurnMode, str] = {
    "chat": "chat",
    "agent": "agent",
    "plan": "planning",
}


def normalize_turn_mode(raw: str, *, fallback: TurnMode = "agent") -> TurnMode:
    """Map router output or category string to a canonical turn mode."""
    key = (raw or "").strip().lower()
    if key in CANONICAL_MODES:
        return key  # type: ignore[return-value]
    mapped = _ROUTER_ALIASES.get(key)
    if mapped:
        return mapped  # type: ignore[return-value]
    if key == "auto":
        return fallback
    return fallback


def category_for_mode(mode: TurnMode) -> str:
    return CANONICAL_TO_CATEGORY[mode]


def ui_mode_for_canonical(mode: TurnMode) -> UiTurnMode:
    return CANONICAL_TO_UI[mode]


def canonical_from_ui(ui_mode: str) -> Optional[TurnMode]:
    return UI_TO_CANONICAL.get(ui_mode)  # type: ignore[return-value]
