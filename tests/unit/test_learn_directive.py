"""Legacy learn directives cannot bypass desktop memory-write approval."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.orchestration.conversation.directive_processor import handle_learn_directive
from src.core.intelligence.reply_parser import ReplyDirective, DirectiveType


@pytest.mark.asyncio
async def test_learn_directive_cannot_write_without_desktop_approval():
    directive = ReplyDirective(
        directive_type=DirectiveType.LEARN,
        data="User prefers dark mode in the UI",
        metadata={},
        position=0,
        raw="[[learn:User prefers dark mode in the UI]]",
    )

    mock_client = MagicMock()
    mock_client.ingest = AsyncMock(return_value=True)

    with patch(
        "src.core.intelligence.graph_rag_client.GraphRAGClient",
        return_value=mock_client,
    ):
        await handle_learn_directive(directive)

    mock_client.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_learn_directive_skips_empty_fact():
    directive = ReplyDirective(
        directive_type=DirectiveType.LEARN,
        data="",
        metadata={},
        position=0,
        raw="[[learn:]]",
    )

    with patch("src.core.intelligence.graph_rag_client.GraphRAGClient") as mock_cls:
        await handle_learn_directive(directive)
        mock_cls.assert_not_called()
