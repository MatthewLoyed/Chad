"""Current-date injection into the assembled system prompt.

Regression for a real bug caught by a live eval run: the model (Gemma) reasoned "June 2024"
as "today" — its training-cutoff prior — while the real date was 2026-07-24, throwing a
wedding-budget calculation off by ~3x (assumed ~30 months of income runway instead of ~10).
Nothing told it otherwise: get_datetime isn't a mandatory core tool, and no date string was
ever injected into the system prompt. Standard practice (Claude/Gemini/ChatGPT) is to inject
the date into the system prompt on every turn rather than rely on the model choosing to call
a tool — these tests lock that in.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.orchestration.turn.state_builder import TurnStateBuilder


def test_current_date_block_matches_todays_real_date():
    block = SystemPromptBuilder.current_date_block()
    assert datetime.now().strftime("%Y-%m-%d") in block
    assert "today" in block.lower()


def test_current_date_block_warns_against_the_training_prior():
    """The failure mode wasn't "no date available" — it was the model preferring its own
    training-data belief over reality. The instruction has to say so explicitly."""
    block = SystemPromptBuilder.current_date_block().lower()
    assert "training data" in block


def test_build_always_includes_the_date_block(monkeypatch):
    monkeypatch.setattr(SystemPromptBuilder, "_read_agent_files",
                        staticmethod(lambda **kwargs: "persona stuff"))
    prompt = SystemPromptBuilder.build(base_instruction="", scope="supervisor")
    assert "### CURRENT DATE" in prompt


def test_build_includes_date_even_with_no_agent_files_and_no_memory(monkeypatch):
    """The date line must not depend on the persona-file path or memory_engine being present
    — it has to survive every fallback branch, since a plain chat turn hits this path too."""
    monkeypatch.setattr(SystemPromptBuilder, "_read_agent_files",
                        staticmethod(lambda **kwargs: ""))
    prompt = SystemPromptBuilder.build(base_instruction="You are CHAD.", scope="conversational")
    assert "### CURRENT DATE" in prompt


def test_plan_lane_gets_the_date_block_too():
    """Plan-mode turns (trip itineraries, deadline-driven goals) bypass SystemPromptBuilder
    entirely (scout/planner nodes add their own role prompts) — this path needs its own
    injection, which is exactly what regressed before this fix."""
    engine = MagicMock()
    engine.get_memory_block.return_value = "some memory context"
    sys_instr = TurnStateBuilder.build_system_instruction(
        category="planning", turn_mode="plan", initiator="user", platform="pc",
        prompt="plan a wedding-adjacent trip", history=[], memory_engine=engine,
    )
    assert "### CURRENT DATE" in sys_instr
    assert "some memory context" in sys_instr


def test_plan_lane_gets_the_date_block_even_when_memory_engine_fails():
    engine = MagicMock()
    engine.get_memory_block.side_effect = RuntimeError("memory backend down")
    sys_instr = TurnStateBuilder.build_system_instruction(
        category="planning", turn_mode="plan", initiator="user", platform="pc",
        prompt="plan something", history=[], memory_engine=engine,
    )
    assert "### CURRENT DATE" in sys_instr
