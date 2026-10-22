"""Connect-preload UI mode: eval never warms intent-router gemma via auto."""

from __future__ import annotations

from src.core.intelligence.model_lifecycle import resolve_connect_preload_ui_mode


def test_eval_defaults_to_agent_not_auto() -> None:
    assert resolve_connect_preload_ui_mode("eval") == "agent"
    assert resolve_connect_preload_ui_mode("eval", None) == "agent"
    assert resolve_connect_preload_ui_mode("eval", "auto") == "agent"


def test_eval_honors_explicit_plan() -> None:
    assert resolve_connect_preload_ui_mode("eval", "plan") == "plan"
    assert resolve_connect_preload_ui_mode("eval", "planning") == "plan"
    assert resolve_connect_preload_ui_mode("eval", "planner") == "plan"


def test_desktop_keeps_requested_or_default() -> None:
    assert resolve_connect_preload_ui_mode("desktop", "agent") == "agent"
    assert resolve_connect_preload_ui_mode("desktop", "auto") == "auto"
    # Without request, falls through to default_ui_turn_mode (config-dependent);
    # only assert it returns a known lane string.
    assert resolve_connect_preload_ui_mode("desktop") in {"auto", "agent", "plan", "chat"}
