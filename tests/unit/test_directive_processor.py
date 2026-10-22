"""
Unit tests for DirectiveProcessor.

Covers:
- register / dispatch for sync and async handlers
- unregister removes the handler
- list_handlers returns registered names
- unknown directive is skipped silently (no exception)
- handler exceptions are caught and do not propagate
- handle_learn_directive module-level function calls GraphRAGClient
"""

import sys
from pathlib import Path
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration.conversation.directive_processor import (
    DirectiveProcessor,
    handle_learn_directive,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_directive(directive_type: str, data: str = "test-data"):
    """Create a minimal ReplyDirective-like mock."""
    d = MagicMock()
    d.directive_type.value = directive_type
    d.data = data
    return d


def _make_parsed_response(*directives):
    """Create a minimal ParsedResponse-like mock with given directives."""
    pr = MagicMock()
    pr.directives = list(directives)
    return pr


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------

def test_register_and_list():
    proc = DirectiveProcessor()
    handler = MagicMock()
    proc.register("foo", handler)
    assert "foo" in proc.list_handlers()


def test_unregister_removes_handler():
    proc = DirectiveProcessor()
    handler = MagicMock()
    proc.register("foo", handler)
    proc.unregister("foo")
    assert "foo" not in proc.list_handlers()


def test_unregister_nonexistent_is_noop():
    proc = DirectiveProcessor()
    proc.unregister("does-not-exist")  # should not raise


# ---------------------------------------------------------------------------
# Dispatch — sync handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_handler_called():
    proc = DirectiveProcessor()
    handler = MagicMock()
    proc.register("greet", handler)

    directive = _make_directive("greet", "hello")
    await proc.process(_make_parsed_response(directive))

    handler.assert_called_once_with(directive)


# ---------------------------------------------------------------------------
# Dispatch — async handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_handler_called():
    proc = DirectiveProcessor()
    handler = AsyncMock()
    proc.register("greet", handler)

    directive = _make_directive("greet", "world")
    await proc.process(_make_parsed_response(directive))

    handler.assert_awaited_once_with(directive)


# ---------------------------------------------------------------------------
# Dispatch — unknown directive is silently skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_directive_is_skipped():
    proc = DirectiveProcessor()
    directive = _make_directive("mystery")
    # Should not raise even though no handler is registered
    await proc.process(_make_parsed_response(directive))


# ---------------------------------------------------------------------------
# Dispatch — handler exception does not propagate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handler_exception_is_swallowed():
    proc = DirectiveProcessor()

    async def bad_handler(d):
        raise RuntimeError("boom")

    proc.register("bad", bad_handler)
    directive = _make_directive("bad")
    # Must NOT raise
    await proc.process(_make_parsed_response(directive))


# ---------------------------------------------------------------------------
# Empty / None directives
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_empty_directives_is_noop():
    proc = DirectiveProcessor()
    pr = MagicMock()
    pr.directives = []
    await proc.process(pr)  # no exception


@pytest.mark.asyncio
async def test_process_none_response_is_noop():
    proc = DirectiveProcessor()
    await proc.process(None)  # no exception


# ---------------------------------------------------------------------------
# handle_learn_directive — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_learn_directive_cannot_bypass_memory_approval():
    directive = _make_directive("learn", "The sky is blue")

    mock_client = AsyncMock()
    mock_client.ingest = AsyncMock(return_value=True)
    # The directive now goes through MemoryWriteGate before writing.
    mock_client.initialize = AsyncMock(return_value=True)
    mock_client.max_similarity = AsyncMock(return_value=(0.0, None))
    mock_cls = MagicMock(return_value=mock_client)

    # GraphRAGClient is imported lazily inside handle_learn_directive; patch it
    # at the source module so the import resolves to the mock.
    with patch(
        "src.core.intelligence.graph_rag_client.GraphRAGClient",
        mock_cls,
        create=True,
    ):
        # Also inject the mock into sys.modules so the lazy import picks it up
        import sys
        import types
        fake_mod = types.ModuleType("src.core.intelligence.graph_rag_client")
        fake_mod.GraphRAGClient = mock_cls
        orig = sys.modules.get("src.core.intelligence.graph_rag_client")
        sys.modules["src.core.intelligence.graph_rag_client"] = fake_mod
        try:
            await handle_learn_directive(directive)
        finally:
            if orig is None:
                sys.modules.pop("src.core.intelligence.graph_rag_client", None)
            else:
                sys.modules["src.core.intelligence.graph_rag_client"] = orig

    mock_cls.assert_not_called()
    mock_client.ingest.assert_not_called()


# ---------------------------------------------------------------------------
# handle_learn_directive — empty data is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_learn_directive_empty_data_noop():
    directive = _make_directive("learn", "")
    # Should not raise and should not call GraphRAGClient
    import sys
    import types
    mock_cls = MagicMock()
    fake_mod = types.ModuleType("src.core.intelligence.graph_rag_client")
    fake_mod.GraphRAGClient = mock_cls
    orig = sys.modules.get("src.core.intelligence.graph_rag_client")
    sys.modules["src.core.intelligence.graph_rag_client"] = fake_mod
    try:
        await handle_learn_directive(directive)
    finally:
        if orig is None:
            sys.modules.pop("src.core.intelligence.graph_rag_client", None)
        else:
            sys.modules["src.core.intelligence.graph_rag_client"] = orig
    mock_cls.assert_not_called()
