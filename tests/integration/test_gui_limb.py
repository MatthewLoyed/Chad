import asyncio
import os
import json
from unittest.mock import MagicMock
import pytest
from src.remote.gui_limb import GUIChadLimb
from src.remote.protocol import ChadMessage, MessageType

@pytest.mark.asyncio
async def test_gui_limb_connectivity():
    # 1. Mock the MainWindow
    mock_window = MagicMock()
    mock_window.update_status = MagicMock()
    mock_window.push_log = MagicMock()
    mock_window.set_thinking_text = MagicMock()
    mock_window.push_thinking_event = MagicMock()
    
    # 2. Initialize the Limb
    # Ensure CHAD_SERVER_URL is set (use Render if available, else localhost)
    server_url = os.getenv("CHAD_SERVER_URL", "ws://localhost:8767")
    print(f"Testing GUIChadLimb with server: {server_url}")
    
    limb = GUIChadLimb(mock_window, server_url=server_url)
    
    # 3. Simulate receiving an AI_THOUGHT message
    msg = ChadMessage(
        MessageType.AI_THOUGHT, 
        payload={"state": "thinking", "text": "I am thinking about your request..."}
    )
    print("Simulating AI_THOUGHT message...")
    await limb.handle_message(msg)
    
    # Verify mock_window was updated
    mock_window.set_thinking_text.assert_called_with("I am thinking about your request...", append=True)
    print("Success: MainWindow.set_thinking_text was called correctly.")
    
    # 4. Simulate receiving an AI_RESPONSE message
    msg_resp = ChadMessage(
        MessageType.AI_RESPONSE,
        payload={"text": "Hello! I am your Cloud Brain."}
    )
    
    print("Simulating AI_RESPONSE message...")
    await limb.handle_message(msg_resp)
    
    # Verify push_ai_response was called with the right arguments
    mock_window.push_ai_response.assert_called_with("Hello! I am your Cloud Brain.", conversation_id=None, message_id=None, metadata={})
    print("Success: MainWindow.push_ai_response was called correctly.")
    
    print("\n[ TEST PASSED ] : GUIChadLimb correctly bridges messages to the MainWindow.")

@pytest.mark.asyncio
async def test_gui_limb_empty_response_with_plan():
    # 1. Mock the MainWindow
    mock_window = MagicMock()
    mock_window.update_status = MagicMock()
    mock_window.push_log = MagicMock()
    mock_window.set_thinking_text = MagicMock()
    mock_window.push_thinking_event = MagicMock()
    
    server_url = "ws://localhost:8767"
    limb = GUIChadLimb(mock_window, server_url=server_url)
    limb.pending_conversation_id = "test-conv-id"
    
    # 2. Simulate receiving execution plan first
    plan_msg = ChadMessage(
        MessageType.EXECUTION_PLAN,
        payload={"plan": [{"id": "step_1", "description": "test plan"}], "plan_id": "test-plan-id", "goal": "test goal"}
    )
    await limb.handle_message(plan_msg)
    
    # Verify thinking events was updated with execution plan
    assert len(limb._turn_thinking_events) == 1
    assert limb._turn_thinking_events[0]['type'] == 'activity_snapshot'
    
    # 3. Simulate receiving empty AI_RESPONSE (as plan stripped response)
    msg_resp = ChadMessage(
        MessageType.AI_RESPONSE,
        payload={"text": ""}
    )
    
    # Mock history_repo.append_message and get_conversation
    async def mock_append(*args, **kwargs):
        return {}
    limb.history_repo.append_message = mock_append
    
    async def mock_get_conv(*args):
        return "test-conv-id"
    limb.history_repo.get_conversation = mock_get_conv
    
    await limb.handle_message(msg_resp)
    
    # 4. Verify push_ai_response was called with empty text but metadata has thoughts
    mock_window.push_ai_response.assert_called_once()
    args, kwargs = mock_window.push_ai_response.call_args
    assert args[0] == ""
    assert kwargs.get("conversation_id") == "test-conv-id"
    assert "thoughts" in kwargs.get("metadata", {})
    
    # Should have execution plan and turn complete in thoughts
    thoughts = kwargs["metadata"]["thoughts"]
    assert len(thoughts) == 2
    assert thoughts[0]["type"] == "activity_snapshot"
    assert thoughts[0]["plan_id"] == "test-plan-id"
    assert thoughts[1]["type"] == "turn_complete"
    print("Success: MainWindow.push_ai_response called with plan metadata when response text is empty.")


@pytest.mark.asyncio
async def test_gui_limb_persists_tool_menu_artifacts_and_user_metadata():
    mock_window = MagicMock()
    mock_window.update_status = MagicMock()
    mock_window.push_log = MagicMock()
    mock_window.set_thinking_text = MagicMock()
    mock_window.push_thinking_event = MagicMock()
    mock_window.push_tool_approval_request = MagicMock()
    mock_window.push_ai_response = MagicMock()

    limb = GUIChadLimb(mock_window, server_url="ws://localhost:8767")
    limb.pending_conversation_id = "conv-persisted"

    # 1. Send input with user metadata (attachments)
    user_appended = []
    async def mock_append(conv_id, role, content, metadata=None):
        user_appended.append({"conv_id": conv_id, "role": role, "content": content, "metadata": metadata})
        return {}
    limb.history_repo.append_message = mock_append

    async def mock_get_conv(*args):
        return {"id": "conv-persisted", "title": "Existing Chat"}
    limb.history_repo.get_conversation = mock_get_conv

    # Stub uplink send
    limb.send = MagicMock()
    async def mock_send(msg):
        return True
    limb.send = mock_send

    await limb.send_input(
        "Generate chart",
        conversation_id="conv-persisted",
        metadata={"attachments": [{"name": "data.csv", "type": "file"}]}
    )
    assert limb._pending_user_metadata == {"attachments": [{"name": "data.csv", "type": "file"}]}
    assert limb._turn_is_mine is True

    # 2. Simulate AI_THOUGHT with tools_menu
    tools_payload = {
        "state": "tools_menu",
        "tools": [{"name": "plot_data", "description": "Plots data"}],
        "shortlist_size": 10,
        "selection_path": "hybrid",
    }
    await limb.handle_message(ChadMessage(MessageType.AI_THOUGHT, payload=tools_payload))
    assert any(e.get("type") == "tool_menu" for e in limb._turn_thinking_events)

    # 3. Simulate tool_end returning artifact
    tool_end_payload = {
        "state": "tool_end",
        "name": "generate_plot",
        "success": True,
        "result": {"download_token": "token-xyz-123", "filename": "chart.png"},
    }
    await limb.handle_message(ChadMessage(MessageType.AI_THOUGHT, payload=tool_end_payload))

    # 4. Simulate TOOL_APPROVAL_REQUEST
    approval_payload = {
        "token": "tok-777",
        "decision_type": "accept_decline",
        "prompt": "Approve chart render?",
        "conversation_id": "conv-persisted",
    }
    await limb.handle_message(ChadMessage(MessageType.TOOL_APPROVAL_REQUEST, payload=approval_payload))
    assert limb._pending_tool_approval is not None

    # 5. Simulate AI_RESPONSE
    resp_msg = ChadMessage(MessageType.AI_RESPONSE, payload={"text": "Here is the chart."})
    await limb.handle_message(resp_msg)

    # Verify user message was persisted with attachments metadata
    assert len(user_appended) >= 1
    user_msg = user_appended[0]
    assert user_msg["role"] == "user"
    assert user_msg["metadata"] == {"attachments": [{"name": "data.csv", "type": "file"}]}

    # Verify AI response metadata contains thoughts (with tool_menu), artifacts, and actions
    mock_window.push_ai_response.assert_called_once()
    args, kwargs = mock_window.push_ai_response.call_args
    metadata = kwargs.get("metadata", {})

    thoughts = metadata.get("thoughts", [])
    assert any(t.get("type") == "tool_menu" for t in thoughts)
    menu_thought = next(t for t in thoughts if t.get("type") == "tool_menu")
    assert menu_thought["tools"][0]["name"] == "plot_data"

    artifacts = metadata.get("artifacts", [])
    assert len(artifacts) == 1
    assert artifacts[0]["download_token"] == "token-xyz-123"

    actions = metadata.get("actions", {})
    assert actions.get("type") == "tool_approval"
    assert actions.get("token") == "tok-777"


if __name__ == "__main__":
    asyncio.run(test_gui_limb_connectivity())
    asyncio.run(test_gui_limb_empty_response_with_plan())
    asyncio.run(test_gui_limb_persists_tool_menu_artifacts_and_user_metadata())

