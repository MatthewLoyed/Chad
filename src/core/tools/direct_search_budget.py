"""One direct web-search call budget shared by the tasks of an assistant turn."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from src.core.tools import failure, limits


class _DirectSearchBudget:
    def __init__(self, maximum: int, warn_after: int) -> None:
        self.maximum = maximum
        self.warn_after = warn_after
        self._calls = 0
        self._lock = Lock()

    def reserve(self) -> int | None:
        with self._lock:
            if self._calls >= self.maximum:
                return None
            self._calls += 1
            return self.maximum - self._calls


_budget: ContextVar[_DirectSearchBudget | None] = ContextVar("direct_web_search_budget", default=None)
_NEXT_ACTIONS = [
    "Use the search results already available to answer what you can.",
    "If substantial research remains, delegate the remaining goal to deep_research_agent.",
]


@contextmanager
def direct_search_budget_scope() -> Iterator[None]:
    maximum = int(limits.get("direct_web_search_calls_max"))
    warn_after = int(limits.get("direct_web_search_calls_soft"))
    # The mutable budget is shared by child tasks even when their contexts are copied.
    token = _budget.set(_DirectSearchBudget(maximum, warn_after) if maximum > 0 else None)
    try:
        yield
    finally:
        _budget.reset(token)


def check_call() -> tuple[dict | None, str | None]:
    budget = _budget.get()
    if budget is None:
        return None, None
    remaining = budget.reserve()
    if remaining is None:
        return failure.failure(
            f"The direct web search budget for this turn is exhausted ({budget.maximum} batches).",
            failure_class=failure.BLOCKED,
            tool_name="web_search",
            next_actions=_NEXT_ACTIONS,
            dispatched=False,
        ), None
    threshold = min(max(1, budget.warn_after), max(1, budget.maximum - 1))
    if budget.maximum - remaining >= threshold:
        return None, (
            f"Direct web search batches remaining this turn: {remaining}. "
            + " ".join(_NEXT_ACTIONS)
        )
    return None, None
