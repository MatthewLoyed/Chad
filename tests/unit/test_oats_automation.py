import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.oats_refiner import OATSRefiner

@pytest.fixture(autouse=True)
def reset_refiner():
    """Reset the OATSRefiner class state before each test."""
    OATSRefiner._counter = 0
    OATSRefiner._is_refining = False
    # Create a fresh lock just in case
    OATSRefiner._lock = asyncio.Lock()


@pytest.mark.asyncio
async def test_oats_refiner_increments_and_triggers():
    """Verify that OATSRefiner increments the counter and triggers refinement when the target is met."""
    mock_refine = MagicMock()
    
    with patch("scripts.oats_refine.refine_embeddings", mock_refine):
        # Trigger 19 times, should not trigger refinement
        for i in range(19):
            await OATSRefiner.increment_and_check(target_count=20)
            assert OATSRefiner._counter == i + 1
            assert not OATSRefiner._is_refining
            
        mock_refine.assert_not_called()
        
        # Trigger the 20th time
        await OATSRefiner.increment_and_check(target_count=20)
        assert OATSRefiner._counter == 0
        
        # Yield to allow background task to run
        await asyncio.sleep(0.05)
        
        # Verify it was triggered
        mock_refine.assert_called_once_with(alpha=0.3, min_outcomes=10, dry_run=False)


@pytest.mark.asyncio
async def test_oats_refiner_prevents_concurrent_refinement():
    """Verify that OATSRefiner prevents starting concurrent refinement runs if one is already active."""
    # We want a mock refine_embeddings that sleeps to simulate a running process
    refine_called_count = 0
    import time
    
    def mock_refine_embeddings(*args, **kwargs):
        nonlocal refine_called_count
        refine_called_count += 1
        time.sleep(0.2)
        return True
        
    with patch("scripts.oats_refine.refine_embeddings", mock_refine_embeddings):
        # Trigger first refinement (target = 2)
        await OATSRefiner.increment_and_check(target_count=2)
        await OATSRefiner.increment_and_check(target_count=2)
        await asyncio.sleep(0.01) # Yield to start background task
        
        assert OATSRefiner._is_refining
        assert refine_called_count == 1
        
        # Trigger second refinement while first is still running
        await OATSRefiner.increment_and_check(target_count=2)
        await OATSRefiner.increment_and_check(target_count=2)
        await asyncio.sleep(0.01) # Yield
        
        # Refine should NOT have been called a second time because _is_refining is True
        assert refine_called_count == 1
        
        # Wait for the first refinement to finish
        await asyncio.sleep(0.25)
        assert not OATSRefiner._is_refining


@pytest.mark.asyncio
async def test_event_wrapper_integration():
    """Verify that ToolEventWrapper._log_oats_outcome correctly triggers OATSRefiner on successful insert."""
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    
    # Mock async_insert to return a valid dictionary (simulate successful db insert)
    mock_supabase.async_insert = AsyncMock(return_value={"id": 1})
    
    mock_increment = AsyncMock()
    
    from src.core.orchestration.context import current_turn_tool_queries_var
    from src.infrastructure.tools.event_wrapper import ToolEventWrapper
    
    query_emb = [0.05] * 768
    token = current_turn_tool_queries_var.set({
        "my_tool": ("sub-query text", query_emb)
    })
    
    try:
        async def original_executor(tool_name, kwargs):
            return "ok"
            
        event_bus = MagicMock()
        wrapper = ToolEventWrapper.wrap_as_langchain_tool("my_tool", original_executor, event_bus, None)
        
        with patch("src.infrastructure.database.supabase_manager.get_supabase_manager", return_value=mock_supabase), \
             patch("src.core.orchestration.oats_refiner.OATSRefiner.increment_and_check", mock_increment):
             
            await wrapper()
            
            # Yield to background tasks
            await asyncio.sleep(0.05)
            
            # Assert Supabase insert was called
            mock_supabase.async_insert.assert_called_once()
            # Assert OATSRefiner increment was called
            mock_increment.assert_called_once()
    finally:
        current_turn_tool_queries_var.reset(token)
