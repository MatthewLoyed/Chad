"""ACTIVE SKILL stub: supervisor-owned vs specialist-owned skills."""

from src.core.orchestration.nodes.supervisor_node import format_active_skill_stub


def test_ownerless_skill_stays_on_supervisor():
    stub = format_active_skill_stub("canva", None)
    assert "ACTIVE SKILL: canva" in stub
    assert "Follow the SOP" in stub
    assert "call_mcp_tool" in stub
    assert "delegate_to_code_agent" not in stub
    assert "Do NOT implement this yourself" not in stub


def test_explicit_subagent_skill_delegates():
    stub = format_active_skill_stub("frontend-design", "code_agent")
    assert "delegate_to_code_agent" in stub
    assert "Do NOT implement this yourself" in stub


def test_subagent_name_without_agent_suffix_is_normalized():
    stub = format_active_skill_stub("homework_assistant", "homework_assistant_agent")
    assert "delegate_to_homework_assistant_agent" in stub
    stub2 = format_active_skill_stub("organize_folder", "organize_folder")
    assert "delegate_to_organize_folder_agent" in stub2
