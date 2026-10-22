"""Unit tests for the ask_user_question clarifying-questions tool."""

import json

import pytest

from src.tools.system.utilities.ask_user_question import AskUserQuestionTool


SAMPLE_QUESTIONS = [
    {
        "question": "Which mode should I use?",
        "header": "Mode",
        "options": [
            {"label": "Agent", "description": "Full tool access"},
            {"label": "Plan", "description": "Read-only planning"},
        ],
    }
]


@pytest.mark.asyncio
async def test_execute_returns_parsed_answers_from_json_decision():
    tool = AskUserQuestionTool()
    decision = json.dumps({"answers": {"Which mode should I use?": "Agent"}})
    result = await tool.execute(questions=SAMPLE_QUESTIONS, _approval_decision=decision)
    assert result["success"] is True
    assert result["answers"]["Which mode should I use?"] == "Agent"
    assert result["result"] == "Which mode should I use?: Agent"
    assert result["_clarification_mode"] is True


HONEYMOON_QUESTIONS = [
    {
        "question": "Where will you be flying from for your honeymoon?",
        "header": "Where will",
        "options": [
            {"label": "Huntsville, AL (HSV)"},
            {"label": "Birmingham, AL (BHM)"},
            {"label": "Nashville, TN (BNA)"},
            {"label": "Atlanta, GA (ATL)"},
        ],
    },
    {
        "question": "What's your preferred trip duration in May 2027?",
        "header": "What's your",
        "options": [
            {"label": "6 nights (weekend)"},
            {"label": "7 nights (full week)"},
            {"label": "8-10 nights"},
        ],
    },
    {
        "question": "Any specific activities or amenities you must have?",
        "header": "Any specific",
        "multiSelect": True,
        "options": [
            {"label": "Beach relaxation only"},
            {"label": "Water sports included"},
            {"label": "Romantic dining & shows"},
            {"label": "Kids clubs NOT needed"},
        ],
    },
    {
        "question": "What's your flight origin airport?",
        "header": "What's your",
        "options": [
            {"label": "HSV"},
            {"label": "BHM"},
            {"label": "BNA"},
        ],
    },
]


@pytest.mark.asyncio
async def test_execute_multi_question_output_shape():
    """Document the ToolMessage payload after the user answers several clarifying Qs.

    Regression: `result` used to be a duplicate of the `answers` map, which made the
    model think the response was "messy" / circular. `answers` is the structured map;
    `result` is a plain-text summary.
    """
    tool = AskUserQuestionTool()
    user_answers = {
        "Where will you be flying from for your honeymoon?": "im in huntsville al",
        "What's your preferred trip duration in May 2027?": "6 nights (weekend)",
        "Any specific activities or amenities you must have?": [
            "Water sports included",
            "Romantic dining & shows",
            "Beach relaxation only",
        ],
        "What's your flight origin airport?": "let me know",
    }
    decision = json.dumps({"answers": user_answers})

    out = await tool.execute(questions=HONEYMOON_QUESTIONS, _approval_decision=decision)

    assert out == {
        "success": True,
        "result": (
            "Where will you be flying from for your honeymoon?: im in huntsville al\n"
            "What's your preferred trip duration in May 2027?: 6 nights (weekend)\n"
            "Any specific activities or amenities you must have?: "
            "Water sports included, Romantic dining & shows, Beach relaxation only\n"
            "What's your flight origin airport?: let me know"
        ),
        "answers": user_answers,
        "_clarification_mode": True,
    }
    # What the model typically sees after json.dumps of the tool return:
    serialized = json.dumps(out)
    parsed = json.loads(serialized)
    assert parsed["answers"] is not None
    assert parsed["result"] != parsed["answers"]
    assert isinstance(parsed["result"], str)
    assert isinstance(parsed["answers"], dict)
    assert len(parsed["answers"]) == 4


def test_approval_request_normalizes_questions():
    tool = AskUserQuestionTool()
    req = tool.approval_request({"title": "Need input", "questions": SAMPLE_QUESTIONS})
    assert req["title"] == "Need input"
    assert len(req["questions"]) == 1
    assert req["questions"][0]["options"][0]["description"] == "Full tool access"


def test_normalize_skips_invalid_questions():
    tool = AskUserQuestionTool()
    normalized = tool._normalize_questions([
        {"question": "Only one option", "options": [{"label": "Solo"}]},
        *SAMPLE_QUESTIONS,
    ])
    assert len(normalized) == 1
    assert normalized[0]["question"] == "Which mode should I use?"


def test_tool_metadata():
    tool = AskUserQuestionTool()
    assert tool.requires_approval is True
    assert tool.approval_decision_type == "questions"
    assert tool.is_side_effecting is False
    schema = tool.get_schema()
    assert schema["name"] == "ask_user_question"
    assert "questions" in schema["parameters"]["properties"]


def test_schema_instructs_options_are_answers_not_questions():
    """Regression: model was stuffing distinct questions into options[] as radio labels."""
    tool = AskUserQuestionTool()
    schema = tool.get_schema()
    desc = schema["description"].lower()
    assert "answers" in desc
    assert "not put other questions in `options`" in desc or "do not put other questions" in desc
    assert "multiselect=true" in desc
    assert "select-all" in desc or "select all" in desc

    options_desc = (
        schema["parameters"]["properties"]["questions"]["items"]["properties"]["options"][
            "description"
        ].lower()
    )
    assert "answers" in options_desc
    assert "never another question" in options_desc or "not another question" in options_desc

    label_desc = (
        schema["parameters"]["properties"]["questions"]["items"]["properties"]["options"][
            "items"
        ]["properties"]["label"]["description"].lower()
    )
    assert "answer" in label_desc
    assert "not a question" in label_desc

    multi_desc = (
        schema["parameters"]["properties"]["questions"]["items"]["properties"]["multiSelect"][
            "description"
        ].lower()
    )
    assert "select-all" in multi_desc or "checklist" in multi_desc


def test_normalize_preserves_multiselect():
    tool = AskUserQuestionTool()
    normalized = tool._normalize_questions(
        [
            {
                "question": "Any amenities you must have?",
                "header": "Amenities",
                "multiSelect": True,
                "options": [
                    {"label": "Beach"},
                    {"label": "Snorkeling"},
                    {"label": "Hiking"},
                ],
            }
        ]
    )
    assert len(normalized) == 1
    assert normalized[0]["multiSelect"] is True
