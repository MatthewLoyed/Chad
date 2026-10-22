"""
Work Status State Machine

Canonical "this thing is being worked on" status model: todo -> in_progress ->
needs_review -> done. Built for the kanban board, but deliberately
domain-agnostic so other surfaces (workflows, autonomous task execution) can
share the same status set and transition rules instead of hand-rolling their
own column lists.
"""

from enum import Enum
from typing import Dict, FrozenSet, List


class WorkStatus(Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"
    DONE = "done"


# Display order for board-style rendering.
ORDERED_STATUSES: List[WorkStatus] = [
    WorkStatus.TODO,
    WorkStatus.IN_PROGRESS,
    WorkStatus.NEEDS_REVIEW,
    WorkStatus.DONE,
]

VALID_STATUS_VALUES: FrozenSet[str] = frozenset(s.value for s in WorkStatus)

DEFAULT_STATUS = WorkStatus.TODO

# Adjacent-step transitions (either direction) plus a "reopen" edge from done
# back to todo. Manual drag-and-drop and agent-driven moves both go through
# this graph so a card can't silently jump from todo straight to done.
_TRANSITIONS: Dict[WorkStatus, FrozenSet[WorkStatus]] = {
    WorkStatus.TODO: frozenset({WorkStatus.IN_PROGRESS}),
    WorkStatus.IN_PROGRESS: frozenset({WorkStatus.TODO, WorkStatus.NEEDS_REVIEW, WorkStatus.DONE}),
    WorkStatus.NEEDS_REVIEW: frozenset({WorkStatus.IN_PROGRESS, WorkStatus.DONE}),
    WorkStatus.DONE: frozenset({WorkStatus.TODO}),
}


def is_valid_status(value: str) -> bool:
    return value in VALID_STATUS_VALUES


def can_transition(from_status: str, to_status: str) -> bool:
    """Whether moving directly from from_status to to_status is allowed.
    A no-op (same status) is always allowed."""
    if from_status == to_status:
        return is_valid_status(from_status)
    if not (is_valid_status(from_status) and is_valid_status(to_status)):
        return False
    return WorkStatus(to_status) in _TRANSITIONS[WorkStatus(from_status)]


def assert_valid_transition(from_status: str, to_status: str) -> None:
    """Raises ValueError if to_status isn't a known status, or the transition
    isn't allowed from from_status."""
    if not is_valid_status(to_status):
        raise ValueError(f"Invalid status '{to_status}'. Must be one of {sorted(VALID_STATUS_VALUES)}.")
    if not can_transition(from_status, to_status):
        raise ValueError(f"Cannot move from '{from_status}' to '{to_status}'.")
