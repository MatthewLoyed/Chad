
import asyncio
import logging
from typing import Dict, Any, List, Optional
from unittest.mock import MagicMock
from src.core.orchestration.langgraph_engine import LangGraphEngine, ChadState
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse
from langchain_core.messages import HumanMessage, AIMessage

# Mock Adapter
class MockAdapter(AIAdapter):
    async def generate(self, messages, tools=None, **kwargs):
        print(f"DEBUG: Adapter received {len(messages)} messages")
        for m in messages:
            print(f"  [{m['role']}]: {m.get('content', '')[:50]}...")
        
        return GenerateResponse(
            text="Hello from Mock Agent! TASK_COMPLETED",
            tool_calls=None,
            finish_reason="stop"
        )
    
    def convert_tool_schemas(self, schemas): return []
    def build_tool_result_message(self, id, name, res): return {}
    def build_assistant_tool_call_message(self, calls, content=None): return {}
    async def is_ready(self): return True

import pytest

@pytest.mark.asyncio
async def test_engine(monkeypatch):
    # monkeypatch (not a raw os.environ assignment) so CHAD_TESTING doesn't
    # leak "true" into later tests in the session.
    monkeypatch.setenv("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    from test_plan_execute import warmup_mock_registry
    warmup_mock_registry()
    adapter = MockAdapter()
    engine = LangGraphEngine(adapter=adapter, tool_executor=None)
    
    initial_state: ChadState = {
        "messages": [HumanMessage(content="hi")],
        "turn_id": "test",
        "session_id": "test",
        "category": "test",
        "platform": "pc",
        "is_simple": False,
        "active_plan": None,
        "plan_step": 1,
        "system_instruction": "You are a helpful assistant.",
        "tools": [],
        "total_tool_calls": 0,
        "tool_results": [],
        "max_tool_calls": 5,
        "final_response": None,
        "temperature": 0.7,
        "thinking_level": "medium",
        "on_token": None,
        "on_thinking": None,
        "variables": {}
    }
    
    graph = await engine.get_graph()
    node_keys = list(graph.nodes.keys())
    assert "conversational_responder" in node_keys
    assert "supervisor" in node_keys
    assert "tools" in node_keys
    assert "graphrag_injector" in node_keys
    assert "__error_handler__code_agent" in node_keys
    assert "__error_handler__research_agent" in node_keys
    
    print("Starting engine...")
    final_state = await engine.invoke(initial_state)
    print("--- RESULT ---")
    print(f"Final Response: {final_state.get('final_response')}")
    print(f"Messages: {len(final_state['messages'])}")

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_engine())


@pytest.mark.asyncio
async def test_variables_hardening(caplog):
    import logging
    from src.core.orchestration.langgraph_engine import (
        pre_checkpoint_validator,
        wrap_node_with_validator,
        UntrackedValue
    )
    
    # 1. Test pre_checkpoint_validator with serializable and non-serializable objects
    class NonSerializableClass:
        pass
        
    test_vars = {
        "valid_str": "hello",
        "valid_int": 42,
        "valid_dict": {"nested": "value"},
        "untracked_val": UntrackedValue("some_handle"),
        "invalid_obj": NonSerializableClass(),
        "invalid_lambda": lambda x: x
    }
    
    with caplog.at_level(logging.WARNING):
        sanitized = pre_checkpoint_validator(test_vars)
        
    # Assert serializable values are preserved
    assert sanitized["valid_str"] == "hello"
    assert sanitized["valid_int"] == 42
    assert sanitized["valid_dict"] == {"nested": "value"}
    
    # Assert UntrackedValue is stripped
    assert "untracked_val" not in sanitized
    
    # Assert non-serializable values are stripped
    assert "invalid_obj" not in sanitized
    assert "invalid_lambda" not in sanitized
    
    # Assert warning logs were generated for serialization failures
    warning_logs = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("invalid_obj" in msg for msg in warning_logs)
    assert any("invalid_lambda" in msg for msg in warning_logs)
    
    # Clear logs
    caplog.clear()
    
    # 2. Test wrap_node_with_validator
    async def mock_node(state, config=None):
        return {
            "variables": {
                "ok_var": "good",
                "bad_var": lambda x: x
            },
            "untracked_top": UntrackedValue("top_level_handle")
        }
        
    wrapped = wrap_node_with_validator(mock_node)
    
    state = {
        "variables": {
            "existing": "value"
        }
    }
    
    mock_runtime = MagicMock()
    mock_runtime.heartbeat = MagicMock()

    with caplog.at_level(logging.WARNING):
        result = await wrapped(state, runtime=mock_runtime)

    # Check that wrap_node_with_validator merged variables and ran the validator
    assert "variables" in result
    assert result["variables"]["existing"] == "value"
    assert result["variables"]["ok_var"] == "good"
    assert "bad_var" not in result["variables"]
    
    # Assert top-level UntrackedValue was stripped from returned updates
    assert "untracked_top" not in result
    
    # Assert warnings logged for bad_var
    warning_logs = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert any("bad_var" in msg for msg in warning_logs)
from typing import TypedDict, List, Annotated
from langgraph.types import RetryPolicy, TimeoutPolicy, Command
from langgraph.errors import NodeError
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

@pytest.mark.asyncio
async def test_native_fault_tolerance_routing():
    # 1. Define a simple State with add_messages reducer
    class TestState(TypedDict):
        messages: Annotated[List[BaseMessage], add_messages]
        route_to: str

    # 2. Define supervisor node
    def supervisor_node(state: TestState):
        return {"messages": [AIMessage(content="Supervisor active")]}

    # 3. Define a failing specialist node that raises an exception
    call_count = 0
    async def failing_specialist(state: TestState):
        nonlocal call_count
        call_count += 1
        print(f"DEBUG TEST: failing_specialist called. Count is now {call_count}")
        raise ValueError("Simulated transient failure")

    # 4. Define the unified error handler
    def error_handler(state: TestState, error: NodeError) -> Command:
        msg = AIMessage(
            content=f"Trapped error: {str(error.error)}",
            name="error_boundary"
        )
        return Command(
            update={"messages": [msg]},
            goto="supervisor"
        )

    # 5. Build test graph
    builder = StateGraph(TestState)
    builder.add_node("supervisor", supervisor_node)
    
    # Mount failing node with a RetryPolicy (constant backoff, 0 delay for speed!) and our native error_handler!
    builder.add_node(
        "failing_specialist",
        failing_specialist,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_interval=0.01,
            backoff_factor=1.0,
            retry_on=ValueError
        ),
        error_handler=error_handler
    )
    
    builder.set_entry_point("failing_specialist")
    builder.add_edge("supervisor", END)
    
    graph = builder.compile()
    
    # Run the graph
    initial_state = {"messages": [HumanMessage(content="Start")], "route_to": ""}
    result = await graph.ainvoke(initial_state)
    
    print(f"DEBUG TEST: final call_count is {call_count}")
    print(f"DEBUG TEST: final messages: {[m.content for m in result['messages']]}")

    # 6. Assertions
    # The failing_specialist should have been called exactly 3 times (due to RetryPolicy max_attempts=3)
    assert call_count == 3
    # The error_handler should have intercepted the failure, updated the messages, and routed to supervisor
    assert len(result["messages"]) == 3
    assert "Trapped error" in result["messages"][1].content
    assert result["messages"][2].content == "Supervisor active"
    print("test_native_fault_tolerance_routing passed successfully!")


@pytest.mark.asyncio
async def test_safe_serializer_checkpointer():
    from src.core.orchestration.langgraph_engine import SafeMemorySaver
    import logging

    # Create a SafeMemorySaver
    checkpointer = SafeMemorySaver()

    # Capture logs to verify warning is emitted
    class LogCaptureHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.warnings = []
        def emit(self, record):
            if record.levelno == logging.WARNING:
                self.warnings.append(record.getMessage())

    logger = logging.getLogger("LangGraphEngine")
    handler = LogCaptureHandler()
    logger.addHandler(handler)

    try:
        # A valid serializable checkpoint write
        config = {"configurable": {"thread_id": "test", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "test_id",
            "channel_values": {"messages": [{"content": "hello"}]}
        }
        await checkpointer.aput(config, checkpoint, {}, {})
        assert len(handler.warnings) == 0

        # An invalid non-serializable checkpoint write (e.g. contains a lambda function)
        bad_checkpoint = {
            "v": 1,
            "id": "test_id_bad",
            "channel_values": {"messages": [{"content": "hello", "callback": lambda x: x}]}
        }
        await checkpointer.aput(config, bad_checkpoint, {}, {})
        assert len(handler.warnings) > 0
        assert "Non-serializable data detected in channel" in handler.warnings[0]
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_ask_user_question_termination(monkeypatch):
    monkeypatch.setenv("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    ConfigManager.get_config()["disabled_subagents"] = []
    from test_plan_execute import warmup_mock_registry
    warmup_mock_registry()

    from src.tools.system.utilities.ask_user_question import AskUserQuestionTool

    class ClarifyMockAdapter(AIAdapter):
        async def generate(self, messages, tools=None, **kwargs):
            system_prompt = ""
            for m in messages:
                if m.get("role") == "system":
                    system_prompt = m.get("content", "")

            if "ORCHESTRATION & ROUTING" in system_prompt:
                return GenerateResponse(
                    text="Wait, I need to ask you something.",
                    tool_calls=[{
                        "id": "call_clarify_test",
                        "name": "ask_user_question",
                        "args": {
                            "questions": [{
                                "question": "Do you want blue or red?",
                                "header": "Color",
                                "options": [
                                    {"label": "Blue", "description": "Cool tone"},
                                    {"label": "Red", "description": "Warm tone"},
                                ],
                            }],
                        },
                    }],
                    finish_reason="tool_calls"
                )

            return GenerateResponse(
                text="Conversational response",
                tool_calls=None,
                finish_reason="stop"
            )

        def convert_tool_schemas(self, schemas): return []
        def build_tool_result_message(self, id, name, res): return {}
        def build_assistant_tool_call_message(self, calls, content=None): return {}
        async def is_ready(self): return True

    from src.infrastructure.tools.registry import ToolRegistry
    registry = ToolRegistry(event_bus=None)
    registry.register_tool(AskUserQuestionTool(), category='system', source='local')

    engine = LangGraphEngine(adapter=ClarifyMockAdapter(), tool_executor=registry)

    initial_state: ChadState = {
        "messages": [HumanMessage(content="Run the secret process please.")],
        "turn_id": "test_clarify_invoke",
        "session_id": "test_clarify_invoke",
        "category": "agent",
        "platform": "pc",
        "is_simple": False,
        "active_plan": [
            {
                "id": "step_1",
                "description": "Run the secret process",
                "expected_output": "Process complete",
                "assigned_to": "supervisor",
                "status": "pending"
            }
        ],
        "plan_step": "step_1",
        "system_instruction": "You are a helpful assistant. ORCHESTRATION & ROUTING",
        "tools": ["ask_user_question"],
        "total_tool_calls": 0,
        "tool_results": [],
        "max_tool_calls": 5,
        "final_response": None,
        "temperature": 0.7,
        "thinking_level": "medium",
        "on_token": None,
        "on_thinking": None,
        "variables": {}
    }

    final_state = await engine.invoke(initial_state)
    assert final_state.get("final_response") == "Do you want blue or red?"
    assert final_state.get("pending_approval") is not None
    assert final_state["pending_approval"].get("decision_type") == "questions"

