"""Canvas submission attachments at the assignment tool boundary."""

from unittest.mock import Mock

import pytest

from src.tools.canvas.assignments import GetAssignmentSubmissionTool


@pytest.fixture
def submission_tool(monkeypatch):
    manager = Mock()
    manager.resolve_course_id.return_value = (101, "Studio Art", None)
    manager.resolve_assignment_id.return_value = (202, "Color Study", None)
    monkeypatch.setattr("src.tools.canvas.assignments.CanvasManager", lambda: manager)
    return GetAssignmentSubmissionTool(), manager


@pytest.mark.asyncio
async def test_submission_includes_uploaded_file_metadata(submission_tool):
    tool, manager = submission_tool
    manager.get.return_value = {
        "success": True,
        "data": {
            "workflow_state": "graded",
            "score": 8,
            "attachments": [
                {
                    "id": 303,
                    "display_name": "Color Study.pdf",
                    "filename": "color-study.pdf",
                    "size": 4096,
                    "content-type": "application/pdf",
                    "url": "https://canvas.example.com/files/303/download?verifier=example",
                },
                {"id": 304, "filename": "notes.txt", "size": 0},
                None,
            ],
            "submission_comments": [{"comment": "Try a warmer palette."}],
        },
    }

    result = await tool.execute(course_id=101, assignment_id=202)

    assert result["success"] is True
    submission = result["submission"]
    assert submission["attachments"] == [
        {"id": 303, "filename": "Color Study.pdf", "size": 4096, "content_type": "application/pdf"},
        {"id": 304, "filename": "notes.txt", "size": 0, "content_type": None},
    ]
    assert submission["score"] == 8
    assert submission["instructor_comments"][0]["comment"] == "Try a warmer palette."
    assert manager.get.call_args.args == ("/courses/101/assignments/202/submissions/self",)
    assert manager.get.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"attachments": None}, {"attachments": []}])
async def test_submission_without_attachments_returns_an_empty_list(submission_tool, fields):
    tool, manager = submission_tool
    manager.get.return_value = {"success": True, "data": {"body": "A text submission.", **fields}}

    result = await tool.execute(course_id=101, assignment_id=202)

    assert result["success"] is True
    assert result["submission"]["attachments"] == []
    assert result["submission"]["body_preview"] == "A text submission."
