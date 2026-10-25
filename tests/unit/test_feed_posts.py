"""Short Feed posts retain source, image and selection provenance."""
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.system.update_dashboard import UpdateDashboardTool


@pytest.mark.asyncio
async def test_short_post_preserves_source_image_and_reason():
    metadata = {
        "url": "https://example.com/notes", "content_type": "source_summary",
        "image_url": "https://example.com/preview.png",
        "selection_reason": "Matches Python in the interest profile.",
    }
    body = "A prototype keyword-searches local notes. Semantic search was not tested."
    with patch("src.core.intelligence.dashboard_feed.append_entry",
               new=AsyncMock(return_value={"id": "post-1"})) as append:
        result = await UpdateDashboardTool().execute(
            kind="discovery", title="Local note search", body=body, metadata=metadata,
        )
    assert result["success"] is True
    assert append.await_args.kwargs["metadata"] == metadata
    assert append.await_args.kwargs["body"] == body


@pytest.mark.asyncio
async def test_existing_short_dashboard_notes_still_work_without_metadata():
    with patch("src.core.intelligence.dashboard_feed.append_entry",
               new=AsyncMock(return_value={"id": "note-1"})):
        result = await UpdateDashboardTool().execute(
            kind="thought", title="A connection", body="A brief note.",
        )
    assert result["success"] is True
