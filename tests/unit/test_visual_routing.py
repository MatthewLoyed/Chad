"""Guardrails for supervisor visual-routing copy (tool naming / Canva path)."""

from src.infrastructure.tools.visual_routing import VISUAL_ROUTING_SUPERVISOR


def test_visual_routing_uses_call_mcp_tool_for_canva_and_excalidraw():
    text = VISUAL_ROUTING_SUPERVISOR
    assert "call_mcp_tool(server_name='canva'" in text
    assert "call_mcp_tool(server_name='excalidraw'" in text
    # Injected prompt must not plant fake mcp__* callable names (models copy them).
    assert "mcp__canva__" not in text
    assert "mcp__excalidraw__" not in text


def test_visual_routing_direct_request_executes_not_reoffers():
    text = VISUAL_ROUTING_SUPERVISOR.lower()
    assert "do not re-offer" in text
    assert "load_skill" in text and "does **not** test connectivity" in VISUAL_ROUTING_SUPERVISOR
