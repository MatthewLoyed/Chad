import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import numpy as np
import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.context import current_turn_tool_queries_var
from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.infrastructure.tools.selection.semantic_selector import SemanticToolSelector
from src.infrastructure.tools.event_wrapper import ToolEventWrapper
from scripts.oats_refine import l2_normalize

@pytest.mark.asyncio
async def test_oats_context_vars_propagation():
    """Verify that current_turn_tool_queries_var is set and reset correctly in TurnCoordinator."""
    coordinator = TurnCoordinator()
    coordinator.default_adapter = MagicMock()
    coordinator.default_adapter.model_name = "test-model"
    coordinator.event_bus = MagicMock()
    
    # Mock ChatHistoryRepository and other inner modules to avoid database calls
    mock_history_repo = MagicMock()
    mock_history_repo.get_history.return_value = []
    
    # Stub out the turn itself and assert the context is active while it runs.
    from src.core.orchestration.turn.pipeline import TurnPipeline

    async def mock_run(self):
        ctx = current_turn_tool_queries_var.get()
        assert ctx is not None
        assert ctx["__query__"] == "Find my receipts"
        return "success"

    assert current_turn_tool_queries_var.get() is None

    with patch.object(TurnPipeline, "run", mock_run):
        res = await coordinator._execute_turn_internal(
            prompt="Find my receipts",
            context=MagicMock(session_id="session-123")
        )

    assert res == "success"
    # Ensure ContextVar was reset back to None
    assert current_turn_tool_queries_var.get() is None


@pytest.mark.asyncio
async def test_semantic_selector_context_population():
    """Verify that SemanticToolSelector._query_supabase_rag populates ContextVar with sub-queries and embeddings."""
    # Setup context var map
    token = current_turn_tool_queries_var.set({"__query__": "hello"})
    try:
        selector = SemanticToolSelector()
        selector._ensure_model = MagicMock()
        mock_model = MagicMock()
        mock_array = MagicMock()
        query_embedding = [0.1] * 768
        mock_array.tolist.return_value = query_embedding
        mock_model.encode.return_value = mock_array
        selector._model = mock_model

        mock_supabase = MagicMock()
        mock_results = [
            {"name": "fetch_emails", "description": "Fetches emails"},
            {"name": "send_email", "description": "Sends an email"}
        ]
        mock_supabase.rpc.return_value = mock_results
        
        from src.shared.config_manager import ConfigManager
        original_get = ConfigManager.get
        with patch("src.infrastructure.tools.selection.semantic_selector.get_supabase_manager", return_value=mock_supabase), patch.object(
            ConfigManager, "get", side_effect=lambda key, default=None: "supabase" if key == "tools.rag_backend" else original_get(key, default)
        ):
            res = selector._query_supabase_rag("search emails", k=5, available_tools={row["name"]: row for row in mock_results})
            
            assert len(res) == 2
            ctx = current_turn_tool_queries_var.get()
            assert ctx is not None
            # Verify the tool mappings and queries/embeddings were correctly stored in the ContextVar map
            assert "fetch_emails" in ctx
            assert "send_email" in ctx
            assert ctx["fetch_emails"] == ("search emails", query_embedding)
            assert ctx["send_email"] == ("search emails", query_embedding)
            
    finally:
        current_turn_tool_queries_var.reset(token)


@pytest.mark.asyncio
async def test_event_wrapper_background_logging_success():
    """Verify that ToolEventWrapper.wrap_as_langchain_tool intercepts execution and triggers background supabase logging."""
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    mock_supabase.async_insert = AsyncMock(return_value={"success": True})
    
    # Set context variables
    query_emb = [0.05] * 768
    token = current_turn_tool_queries_var.set({
        "__query__": "user input",
        "my_tool": ("sub-query text", query_emb)
    })
    
    async def original_executor(tool_name, kwargs):
        return "tool execution output"
        
    event_bus = MagicMock()
    
    try:
        wrapper = ToolEventWrapper.wrap_as_langchain_tool("my_tool", original_executor, event_bus, None)
        
        with patch("src.infrastructure.database.supabase_manager.get_supabase_manager", return_value=mock_supabase):
            result = await wrapper()
            
            assert result == "tool execution output"
            # Ensure database log was scheduled asynchronously in background task
            await asyncio.sleep(0.01) # Yield to background tasks
            mock_supabase.async_insert.assert_called_once_with(
                "tool_outcome_log",
                {
                    "tool_name": "my_tool",
                    "query_text": "sub-query text",
                    "query_embedding": query_emb,
                    "outcome": "success"
                }
            )
    finally:
        current_turn_tool_queries_var.reset(token)


@pytest.mark.asyncio
async def test_event_wrapper_background_logging_failure():
    """Verify that ToolEventWrapper.wrap_as_langchain_tool intercepts exception and logs outcome as failure."""
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    mock_supabase.async_insert = AsyncMock(return_value={"success": True})
    
    # Set context variables
    query_emb = [0.05] * 768
    token = current_turn_tool_queries_var.set({
        "__query__": "user input",
        "my_tool": ("sub-query text", query_emb)
    })
    
    async def original_executor(tool_name, kwargs):
        raise ValueError("Something went wrong")
        
    event_bus = MagicMock()
    
    try:
        wrapper = ToolEventWrapper.wrap_as_langchain_tool("my_tool", original_executor, event_bus, None)
        
        with patch("src.infrastructure.database.supabase_manager.get_supabase_manager", return_value=mock_supabase):
            with pytest.raises(ValueError, match="Something went wrong"):
                await wrapper()
            
            # Ensure database failure log was scheduled
            await asyncio.sleep(0.01)
            mock_supabase.async_insert.assert_called_once_with(
                "tool_outcome_log",
                {
                    "tool_name": "my_tool",
                    "query_text": "sub-query text",
                    "query_embedding": query_emb,
                    "outcome": "failure"
                }
            )
    finally:
        current_turn_tool_queries_var.reset(token)


def test_oats_refine_math_normalization():
    """Verify the L2 vector normalization formula and mathematical outcome updates."""
    # Test L2 normalization
    vec = np.array([3.0, 4.0], dtype=np.float32)
    norm = l2_normalize(vec)
    np.testing.assert_allclose(norm, np.array([0.6, 0.8], dtype=np.float32))
    
    # Test normalization of zero vector does not throw division by zero
    zero_vec = np.array([0.0, 0.0], dtype=np.float32)
    norm_zero = l2_normalize(zero_vec)
    np.testing.assert_allclose(norm_zero, np.array([0.0, 0.0], dtype=np.float32))
