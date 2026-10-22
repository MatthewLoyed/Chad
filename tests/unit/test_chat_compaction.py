import sys
import os
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.utils.token_counter import TokenCounter
from src.core.utils.consolidation_manager import ConsolidationManager
from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
from src.core.orchestration.langgraph_engine import LangGraphEngine, SafeMemorySaver
from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.application.commands.config_commands import ForceConsolidationCommand
from src.application.commands.message_commands import SendMessageCommand
from src.shared.context import RequestContext
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter, GenerateResponse

class DummyMemoryEngine:
    def __init__(self):
        self.memories = []
        self.fail_mode = False

    def save_memory(self, content, category, importance=0.5):
        if self.fail_mode:
            raise RuntimeError("Simulated SQLite Write Failure")
        self.memories.append((content, category, importance))

    def log_turn(self, role, content):
        pass


class MockAIAdapter(AIAdapter):
    def __init__(self):
        self.generated_text = '{"project_state": "Testing compaction", "completed_tasks": [], "pending_tasks": ["Continue tests"], "user_data": {}, "system_changes": []}'

    async def generate(self, messages, tools=None, temperature=0.7, **kwargs):
        return GenerateResponse(
            text=self.generated_text,
            tool_calls=None,
            finish_reason="stop"
        )

    def convert_tool_schemas(self, schemas):
        return []

    def build_tool_result_message(self, tool_call_id, name, result):
        return {}

    def build_assistant_tool_call_message(self, tool_calls):
        return {}

    async def is_ready(self):
        return True


def test_token_counter_message_types():
    # 1. Gemini format
    gemini_msg = {"role": "user", "parts": [{"text": "Hello world"}]}
    assert TokenCounter.count_message(gemini_msg) == 5 + len("Hello world") // 4

    # 2. OpenAI string format
    openai_str_msg = {"role": "user", "content": "Hello world, this is OpenAI style."}
    assert TokenCounter.count_message(openai_str_msg) == 5 + len("Hello world, this is OpenAI style.") // 4

    # 3. OpenAI list format
    openai_list_msg = {"role": "user", "content": [{"type": "text", "text": "Hello block format"}]}
    assert TokenCounter.count_message(openai_list_msg) == 5 + len("Hello block format") // 4

    # 4. Content blocks format
    content_blocks_msg = {"role": "user", "content_blocks": [{"type": "text", "text": "Hello internal blocks"}]}
    assert TokenCounter.count_message(content_blocks_msg) == 5 + len("Hello internal blocks") // 4


def test_split_history_by_tokens():
    mem_engine = DummyMemoryEngine()
    manager = ConsolidationManager(memory_engine=mem_engine)

    # Create history with messages of known token size
    # Approx tokens per message: 5 (role overhead) + content_chars // 4
    history = [
        {"role": "user", "content": "A" * 40},       # ~15 tokens
        {"role": "assistant", "content": "B" * 80},  # ~25 tokens
        {"role": "user", "content": "C" * 120},     # ~35 tokens
        {"role": "assistant", "content": "D" * 160}, # ~45 tokens
    ]

    # Keep tokens = 50 -> should keep the last message (~45 + 5 = 50 tokens)
    # the second last will exceed keep_tokens boundary
    to_compact, to_keep = manager.split_history_by_tokens(history, keep_tokens=50)
    assert len(to_keep) == 1
    assert to_keep[0]["content"] == "D" * 160
    assert len(to_compact) == 3

    # Keep tokens = 200 -> should keep everything, compact nothing
    to_compact, to_keep = manager.split_history_by_tokens(history, keep_tokens=200)
    assert len(to_compact) == 0
    assert len(to_keep) == 4


@pytest.mark.asyncio
async def test_chat_history_repository_archiving():
    mock_supabase = MagicMock()
    mock_supabase.async_update = AsyncMock(return_value=True)
    mock_supabase.async_query = AsyncMock(return_value=[])

    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        repo = ChatHistoryRepository()
        
        # Test archive_messages
        res = await repo.archive_messages(["uuid-1", "uuid-2"])
        assert res is True
        
        # Verify PostgREST async_update filter
        mock_supabase.async_update.assert_called_once()
        args, kwargs = mock_supabase.async_update.call_args
        assert args[0] == "messages"
        assert kwargs["filters"] == {"id": "in.(uuid-1,uuid-2)"}
        assert kwargs["data"]["is_archived"] is True
        assert "compacted_at" in kwargs["data"]


@pytest.mark.asyncio
async def test_checkpointer_clear_checkpoints(monkeypatch):
    # monkeypatch (not a raw os.environ assignment) so CHAD_TESTING doesn't
    # leak "true" into later tests in the session (it gates real-model/
    # real-connection codepaths — see litellm_adapter.py, model_lifecycle.py).
    monkeypatch.setenv("CHAD_TESTING", "true")
    adapter = MockAIAdapter()
    engine = LangGraphEngine(adapter=adapter, tool_executor=None)
    
    # Pre-populate saver
    saver = SafeMemorySaver()
    engine._checkpointer = saver
    
    # Mock adelete_thread method
    saver.adelete_thread = AsyncMock()
    
    await engine.clear_checkpoints("test-thread-id")
    saver.adelete_thread.assert_called_once_with("test-thread-id")


@pytest.mark.asyncio
async def test_sqlite_sync_failure_is_non_blocking(monkeypatch):
    # Setup mocks
    mem_engine = DummyMemoryEngine()
    mem_engine.fail_mode = True # Make it raise an exception on write
    
    event_bus = MagicMock()
    consolidation_manager = ConsolidationManager(memory_engine=mem_engine)
    adapter = MockAIAdapter()
    
    turn_coordinator = TurnCoordinator(
        memory_engine=mem_engine,
        event_bus=event_bus,
        consolidation_manager=consolidation_manager,
        default_adapter=adapter
    )
    
    # Enable test mode checkpointer (monkeypatch-scoped — see comment on
    # test_checkpointer_clear_checkpoints above).
    monkeypatch.setenv("CHAD_TESTING", "true")
    engine = LangGraphEngine(adapter=adapter, tool_executor=None)
    turn_coordinator._langgraph_engine = engine
    
    # Create sample messages that need compaction (IDs included)
    history = [
        {"role": "user", "content": "A" * 40, "id": "uuid-1"},
        {"role": "assistant", "content": "B" * 80, "id": "uuid-2"},
        {"role": "user", "content": "C" * 120, "id": "uuid-3"},
    ]
    
    # Mock Supabase operations
    mock_supabase = MagicMock()
    mock_supabase.async_update = AsyncMock(return_value=True)
    mock_supabase.async_query = AsyncMock(return_value=[])
    mock_supabase.async_insert = AsyncMock(return_value={"id": "new-uuid"})
    
    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        # Patch split_history_by_tokens to ensure some messages are compacted
        with patch.object(consolidation_manager, "split_history_by_tokens", return_value=(history[:2], history[2:])):
            # Run compaction with failing SQLite. It should still return True!
            success = await turn_coordinator._run_compaction("session-123", history, adapter)
            assert success is True
        
        # Verify event bus received failure alert
        from src.application.events.events import ErrorOccurred, LogMessage
        event_bus.publish.assert_any_call(
            ErrorOccurred(
                error_message="Local memory engine synchronization failed: Simulated SQLite Write Failure",
                error_type="SQLiteSync",
                source="Consolidation",
                recoverable=True
            )
        )


@pytest.mark.asyncio
async def test_compact_slash_command_routing():
    facade = MagicMock()
    facade.force_consolidation = AsyncMock(return_value=True)
    
    command = SendMessageCommand(text="/compact", conversation_id="session-123")
    
    # Mock DB appending
    mock_supabase = MagicMock()
    mock_supabase.async_insert = AsyncMock(return_value={"id": "new-id"})
    
    with patch("src.infrastructure.repositories.chat_history_repository.get_supabase_manager", return_value=mock_supabase):
        result = await command.execute(facade)
        
        # Verify /compact command was routed to facade.force_consolidation
        facade.force_consolidation.assert_called_once_with("session-123")
        assert result["success"] is True
        assert "compaction" in result["response"].lower()
