"""Shared agent attribution for limbs (Planner, Scout, specialists).

Nodes must not each reinvent set/reset of ``agent_name_var`` — use
``agent_scope`` so thinking/tool events label consistently for GUI + evals.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from src.core.orchestration.context import agent_name_var, parent_correlation_id_var


@contextmanager
def agent_scope(
    name: str,
    *,
    parent_id: Optional[str] = None,
    set_parent: bool = False,
) -> Iterator[None]:
    """Pin ``agent_name_var`` (and optionally ``parent_correlation_id_var``) for a limb.

    Args:
        name: Display/agent label (e.g. ``"Planner"``, ``"Scout"``).
        parent_id: Correlation id for nesting under a parent tool bubble.
        set_parent: When True, set parent even if ``parent_id`` is None (clears nest).
    """
    t_agent = agent_name_var.set(name)
    t_parent = None
    if set_parent or parent_id is not None:
        t_parent = parent_correlation_id_var.set(parent_id)
    try:
        yield
    finally:
        agent_name_var.reset(t_agent)
        if t_parent is not None:
            parent_correlation_id_var.reset(t_parent)
