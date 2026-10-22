import sys
from pathlib import Path
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.tools.cloud.supabase_bridge import SupabaseToolSyncBridge

@pytest.mark.asyncio
async def test_supabase_bridge_drift_detection():
    # Mock supabase manager
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    
    # Mock query result
    # We have three existing tools:
    # - "read_file": description hash matches, schema matches -> UNCHANGED
    # - "write_file_atomic": schema changed -> UPDATED
    # - "legacy_write": not in tool_map -> REMOVED
    # - And a new tool "search_web" in tool_map -> NEW
    existing_tools = [
        {
            "name": "read_file",
            "intent_preamble": "INTENT: Use read_file to read.",
            "description_hash": "hash_read",
            "schema": {"name": "read_file", "description": "Read a file"},
            "embedding_model": "nomic-v2-moe"
        },
        {
            "name": "write_file_atomic",
            "intent_preamble": "INTENT: Use write_file_atomic.",
            "description_hash": "hash_write",
            "schema": {"name": "write_file_atomic", "description": "Write a file", "old_param": True},
            "embedding_model": "nomic-v2-moe"
        },
        {
            "name": "legacy_write",
            "intent_preamble": "INTENT: Use legacy_write.",
            "description_hash": "hash_legacy",
            "schema": {"name": "legacy_write", "description": "Legacy write"},
            "embedding_model": "nomic-v2-moe"
        }
    ]
    
    mock_supabase.async_query = AsyncMock(return_value=existing_tools)
    mock_supabase.async_upsert = AsyncMock(return_value={"success": True})
    mock_supabase.async_delete = AsyncMock(return_value={"success": True})
    
    bridge = SupabaseToolSyncBridge()
    # Mock model and preamble generator to avoid real loads/calls
    bridge._model = MagicMock()
    mock_array = MagicMock()
    mock_array.tolist.return_value = [0.1, 0.2, 0.3]
    bridge._model.encode = MagicMock(return_value=mock_array)
    
    bridge._preamble_gen = AsyncMock()
    bridge._preamble_gen.generate_preamble = AsyncMock(return_value="Use search_web.")
    
    # Construct tool_map
    mock_read = MagicMock()
    mock_read.get_schema.return_value = {"name": "read_file", "description": "Read a file"}
    
    mock_write = MagicMock()
    mock_write.get_schema.return_value = {"name": "write_file_atomic", "description": "Write a file", "new_param": True}
    
    mock_search = MagicMock()
    mock_search.get_schema.return_value = {"name": "search_web", "description": "Search the web"}
    
    tool_map = {
        "read_file": mock_read,
        "write_file_atomic": mock_write,
        "search_web": mock_search
    }
    
    with patch("src.infrastructure.tools.cloud.supabase_bridge.get_supabase_manager", return_value=mock_supabase), \
         patch("src.infrastructure.tools.cloud.supabase_bridge.get_description_hash") as mock_hash, \
         patch("src.infrastructure.tools.cloud.supabase_bridge.MANUAL_PREAMBLES", {}):
        # MANUAL_PREAMBLES is patched to {} because it now has real curated
        # entries (e.g. "read_file") that don't match this test's synthetic
        # cached intent_preamble text — that's a content-curation concern,
        # not what this test exercises (hash/schema diffing + the bulk-removal
        # safety gate). See MAX_REMOVAL_COUNT/MAX_REMOVAL_RATIO docstring.

        # Map hashes for mock_hash
        def hash_side_effect(desc):
            if "Read" in desc: return "hash_read"
            if "Write" in desc: return "hash_write"
            return "hash_search"
        mock_hash.side_effect = hash_side_effect
        
        # Capture print statement
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            # force_delete=True: the bulk-removal safety gate (MAX_REMOVAL_COUNT/
            # MAX_REMOVAL_RATIO) would otherwise refuse to delete 1/3 (33%) of the
            # catalog and skip the REMOVED case this test exercises.
            await bridge.sync_all(tool_map, force_delete=True)
            
        output = f.getvalue()
        
        # Verify print output
        assert "[refresh_rag_tools] Schema changes detected:" in output
        assert "  NEW:     search_web (new tool)" in output
        assert "  UPDATED: write_file_atomic (schema parameters changed)" in output
        assert "  REMOVED: legacy_write (no longer in registry)" in output
        assert "  UNCHANGED: 1 tools (skipped)" in output
        
        # Verify db operations
        # Upsert should have been called for "write_file_atomic" (schema changed) and "search_web" (new tool)
        # It should NOT be called for "read_file"
        mock_supabase.async_upsert.assert_called_once()
        upsert_payload = mock_supabase.async_upsert.call_args[0][1]
        upsert_names = [p["name"] for p in upsert_payload]
        assert "write_file_atomic" in upsert_names
        assert "search_web" in upsert_names
        assert "read_file" not in upsert_names
        
        # Delete should have been called for "legacy_write"
        mock_supabase.async_delete.assert_called_once_with("tools", {"name": "eq.legacy_write"})

@pytest.mark.asyncio
async def test_supabase_bridge_no_changes():
    # Test case when no changes are detected - should skip db operations entirely
    mock_supabase = MagicMock()
    mock_supabase.enabled = True
    
    existing_tools = [
        {
            "name": "read_file",
            "intent_preamble": "INTENT: Use read_file to read.",
            "description_hash": "hash_read",
            "schema": {"name": "read_file", "description": "Read a file"},
            "embedding_model": "nomic-v2-moe"
        }
    ]
    
    mock_supabase.async_query = AsyncMock(return_value=existing_tools)
    mock_supabase.async_upsert = AsyncMock()
    mock_supabase.async_delete = AsyncMock()
    
    bridge = SupabaseToolSyncBridge()
    bridge._model = MagicMock()
    bridge._preamble_gen = AsyncMock()
    
    mock_read = MagicMock()
    mock_read.get_schema.return_value = {"name": "read_file", "description": "Read a file"}
    
    tool_map = {
        "read_file": mock_read
    }
    
    with patch("src.infrastructure.tools.cloud.supabase_bridge.get_supabase_manager", return_value=mock_supabase), \
         patch("src.infrastructure.tools.cloud.supabase_bridge.get_description_hash", return_value="hash_read"), \
         patch("src.infrastructure.tools.cloud.supabase_bridge.MANUAL_PREAMBLES", {}):
        # See test_supabase_bridge_drift_detection: MANUAL_PREAMBLES has a real
        # curated "read_file" entry that doesn't match this test's synthetic
        # cached preamble, which would otherwise force a spurious update.

        await bridge.sync_all(tool_map)
        
        # Verify that upsert and delete were NOT called
        mock_supabase.async_upsert.assert_not_called()
        mock_supabase.async_delete.assert_not_called()
