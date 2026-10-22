"""
Reflection lesson review/approve commands (#194).

Relay the desktop "Reflection" panel's reads/writes to the Brain. Mirrors
`capability_commands.py` — these call the standalone async helpers in
``src.core.intelligence.reflection_lessons_store`` directly (a simple Supabase table),
dispatched through ``/api/command`` like other lightweight data commands.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .base_command import Command


@dataclass
class GetReflectionLessonsCommand(Command):
    """Fetch the pending (+ auto-applied) reflection lessons/questions queue."""

    limit: int = 20

    async def execute(self, facade) -> List[Dict[str, Any]]:
        from src.core.intelligence.reflection_lessons_store import get_pending_lessons
        return await get_pending_lessons(limit=int(self.limit))

    def __str__(self) -> str:
        return f"GetReflectionLessonsCommand(limit={self.limit})"


@dataclass
class SetReflectionLessonStatusCommand(Command):
    """Approve (write it) or dismiss a reflection lesson."""

    lesson_id: str
    status: str  # 'approved' | 'dismissed'

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.lesson_id:
            return False, "lesson_id is required"
        if self.status not in {"approved", "dismissed"}:
            return False, f"Invalid status: {self.status}"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.reflection_lessons_store import (
            get_lesson_by_id, set_lesson_status, apply_lesson,
        )

        if self.status == "dismissed":
            ok = await set_lesson_status(self.lesson_id, "dismissed")
            return {"success": ok}

        row = await get_lesson_by_id(self.lesson_id)
        if not row:
            return {"success": False, "error": f"Lesson {self.lesson_id} not found"}
        applied = await apply_lesson(row)
        if not applied:
            return {"success": False, "error": "Failed to write lesson to its destination"}
        ok = await set_lesson_status(self.lesson_id, "applied")
        return {"success": ok}

    def __str__(self) -> str:
        return f"SetReflectionLessonStatusCommand(id={self.lesson_id}, status={self.status})"


@dataclass
class AnswerReflectionQuestionCommand(Command):
    """Submit the user's answer to an open reflection question."""

    lesson_id: str
    answer: str

    def validate(self) -> tuple[bool, Optional[str]]:
        if not self.lesson_id:
            return False, "lesson_id is required"
        if not (self.answer or "").strip():
            return False, "answer is required"
        return True, None

    async def execute(self, facade) -> Dict[str, Any]:
        from src.core.intelligence.reflection_lessons_store import answer_question
        ok = await answer_question(self.lesson_id, self.answer)
        return {"success": ok}

    def __str__(self) -> str:
        return f"AnswerReflectionQuestionCommand(id={self.lesson_id})"
