"""
Ask User Question Tool — Claude-style structured clarifying questions.

When Chad needs direction and there are multiple valid approaches, it calls
this tool with one or more multiple-choice questions. The GUI renders a rich
question card (labels + descriptions, single/multi-select, tabbed multi-Q)
and execution resumes once the user submits their answers.

Critical shape: each questions[] entry is ONE question; its options[] are
ANSWERS (radio by default; checkboxes when multiSelect), not additional
questions. Multiple distinct clarifying questions belong as separate
questions[] entries (paginated in the UI).

Schema mirrors Claude Code's AskUserQuestion / Cursor's cursor/ask_question:
  questions[].question  — full question text (also the answer-map key)
  questions[].header    — short tab label (max ~12 chars)
  questions[].options[] — { label, description? }  # answers to that question
  questions[].multiSelect — allow multiple selections

The full nested schema lives in get_schema(); ChatChad forwards it verbatim to the
model (see tests/unit/test_tool_schema_fidelity.py).
"""

import json
from typing import Any, Dict, List, Optional

from src.tools.base_tool import BaseTool


class AskUserQuestionTool(BaseTool):
    """Present structured clarifying questions and wait for the user's choices."""

    name = "ask_user_question"
    description = (
        "MANDATORY when you need to ask the user anything before proceeding: ambiguous "
        "requests, missing info, or multiple valid approaches. NEVER ask clarifying "
        "questions in plain chat text — always call this tool.\n\n"
        "Shape (critical): each entry in `questions` is ONE question. Its `options` are "
        "ANSWERS to that question — not additional questions. Do NOT put other questions "
        "in `options`; put each distinct clarifying question as its own `questions` entry "
        "(the UI paginates them).\n\n"
        "Single vs select-all:\n"
        "- Default (multiSelect omitted/false): mutually exclusive answers (pick one).\n"
        "- multiSelect=true: select-all / checklist — user may pick several answers that "
        "can all apply at once (amenities, preferences, constraints, topics to cover). "
        "Set this whenever more than one option could reasonably be true together.\n\n"
        "Good single: question='Where should I create the tasks?', options=["
        "{label:'The Knot'}, {label:'Local file'}, {label:'Task manager'}].\n"
        "Good select-all: question='Any activities or amenities you must have?', "
        "multiSelect=true, options=[{label:'Beach'}, {label:'Snorkeling'}, "
        "{label:'Hiking'}, {label:'Romantic dining'}].\n"
        "Bad: question='I need more clarity', options=["
        "{label:\"What is X?\"}, {label:\"Where is Y?\"}] — those are questions, not answers.\n\n"
        "Present 1-4 questions, each with 2-4 answer options (short label + helpful "
        "description). The UI adds a free-text 'Something else' option automatically."
    )
    category = "system"
    requires_approval = True
    approval_decision_type = "questions"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "Optional card title shown above the questions "
                            "(e.g. 'A few quick questions')."
                        ),
                    },
                    "questions": {
                        "type": "array",
                        "description": (
                            "One or more clarifying questions. Each item is a separate "
                            "question (paginated in the UI when there are several). Do not "
                            "stuff multiple unrelated questions into one item's options."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": (
                                        "The single question the user will answer "
                                        "(e.g. 'Where should I create the tasks?'). "
                                        "Not a preamble listing several questions."
                                    ),
                                },
                                "header": {
                                    "type": "string",
                                    "description": "Short tab label (max ~12 characters).",
                                },
                                "options": {
                                    "type": "array",
                                    "description": (
                                        "2-4 ANSWERS to this question. Default: mutually "
                                        "exclusive (radio). With multiSelect=true: "
                                        "non-exclusive checklist items the user may combine. "
                                        "Never another question."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": (
                                                    "Short answer label (1-5 words), e.g. "
                                                    "'The Knot' or 'Local file' — not a "
                                                    "question like 'What is X?'."
                                                ),
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": (
                                                    "One sentence explaining this answer "
                                                    "choice or its trade-offs."
                                                ),
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                    "minItems": 2,
                                    "maxItems": 4,
                                },
                                "multiSelect": {
                                    "type": "boolean",
                                    "description": (
                                        "Set true for select-all / checklist questions "
                                        "where several answers can apply together "
                                        "(amenities, must-haves, interests). Renders "
                                        "checkboxes. Default false = pick exactly one."
                                    ),
                                },
                            },
                            "required": ["question", "options"],
                        },
                        "minItems": 1,
                        "maxItems": 4,
                    },
                },
                "required": ["questions"],
            },
        }

    def approval_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        questions = self._normalize_questions(params.get("questions") or [])
        title = (params.get("title") or "").strip() or None
        first_q = questions[0]["question"] if questions else "Please choose an option:"
        return {
            "prompt": first_q,
            "title": title,
            "questions": questions,
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        questions = params.get("questions") or []
        if isinstance(questions, list) and questions:
            first = questions[0]
            if isinstance(first, dict):
                header = first.get("header") or first.get("question") or "question"
                return f"Asking about {header}"
        return "Asking clarifying questions"

    async def execute(
        self,
        questions: Optional[List[Dict[str, Any]]] = None,
        title: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        decision = kwargs.get("_approval_decision")
        if decision is not None:
            answers = self._parse_decision(decision)
            # `answers` is the structured Q→A map the model should read.
            # `result` is a plain-text summary (not a duplicate of `answers`) so the
            # ToolMessage isn't a messy double copy of the same object.
            return {
                "success": True,
                "result": self._format_answers_summary(answers),
                "answers": answers,
                "_clarification_mode": True,
            }

        # Unreachable on first call — the approval gate short-circuits before execute().
        normalized = self._normalize_questions(questions or [])
        return {
            "success": True,
            "result": normalized[0]["question"] if normalized else "",
            "_pending_approval": True,
            "_clarification_mode": True,
        }

    @staticmethod
    def _normalize_questions(raw: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            header = str(item.get("header") or question[:12]).strip()[:12]
            options_in = item.get("options") or []
            options: List[Dict[str, str]] = []
            for opt in options_in:
                if isinstance(opt, str):
                    label = opt.strip()
                    if label:
                        options.append({"label": label, "description": ""})
                elif isinstance(opt, dict):
                    label = str(opt.get("label") or "").strip()
                    if not label:
                        continue
                    desc = str(opt.get("description") or "").strip()
                    options.append({"label": label, "description": desc})
            if len(options) < 2:
                continue
            out.append(
                {
                    "question": question,
                    "header": header or question[:12],
                    "options": options[:4],
                    "multiSelect": bool(item.get("multiSelect")),
                }
            )
        return out

    @staticmethod
    def _format_answer_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if str(v).strip())
        return str(value)

    @classmethod
    def _format_answers_summary(cls, answers: Dict[str, Any]) -> str:
        """Plain-text Q/A lines for ToolMessage `result` (not a JSON clone of `answers`)."""
        if not isinstance(answers, dict) or not answers:
            return ""
        lines: List[str] = []
        for question, answer in answers.items():
            formatted = cls._format_answer_value(answer)
            if not formatted:
                continue
            lines.append(f"{question}: {formatted}")
        return "\n".join(lines)

    @staticmethod
    def _parse_decision(decision: Any) -> Dict[str, Any]:
        if isinstance(decision, dict):
            if "answers" in decision:
                return decision["answers"]
            return decision
        if isinstance(decision, str):
            try:
                parsed = json.loads(decision)
                if isinstance(parsed, dict):
                    if "answers" in parsed:
                        return parsed["answers"]
                    return parsed
            except json.JSONDecodeError:
                pass
            return {"response": decision}
        return {"response": str(decision)}
