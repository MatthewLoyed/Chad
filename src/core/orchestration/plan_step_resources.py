"""Resolve plan step tool references against registry tools and skill catalog."""
from __future__ import annotations

from typing import Iterable, List, Optional, Set, Tuple

SKILL_META_TOOLS = frozenset({"load_skill", "list_skills"})
DELEGATE_STEP_TOOLS = frozenset({
    "delegate_to_code_agent",
    "delegate_to_traversal_agent",
    "delegate_to_ui_automation_agent",
    "delegate_to_browser_agent",
    "delegate_to_system_agent",
    "delegate_to_canvas_agent",
    # Research is a single path (#510): delegate_to_deep_research_agent, which owns discovery +
    # parallel depth research + synthesis internally. There is no delegate_to_research_agent —
    # research_agent is the private worker pool behind deep_research_agent's own tools, not a
    # directly-callable delegate.
    "delegate_to_deep_research_agent",
})


def _normalize_token(value: str) -> str:
    """Normalize planner/supervisor aliases for fuzzy skill matching."""
    token = value.lower().strip().replace("_", "-").replace(" ", "-")
    for suffix in ("-skill.md", "-skill", ".skill"):
        if token.endswith(suffix):
            token = token[: -len(suffix)]
    return token.strip("-")


def resolve_skill_name(reference: str, catalog_names: Iterable[str]) -> Optional[str]:
    """
    Map a plan-step reference to a canonical skill catalog name.

    Handles planner inventions like ``frontend_design_skill`` → ``frontend-design``.
    """
    names = list(catalog_names)
    if not names or not reference:
        return None

    if reference in names:
        return reference

    norm_ref = _normalize_token(reference)
    if not norm_ref:
        return None

    by_norm = {_normalize_token(n): n for n in names}

    if norm_ref in by_norm:
        return by_norm[norm_ref]

    # Substring match for compound planner names (e.g. frontend_design_skill).
    for norm_name, canonical in by_norm.items():
        if norm_name and (norm_name in norm_ref or norm_ref in norm_name):
            return canonical

    return None


def classify_step_resources(
    step_tools: List[str],
    registry_names: Set[str],
    skill_catalog_names: Set[str],
) -> Tuple[List[str], List[Tuple[str, str]], List[str]]:
    """
    Classify plan step resources.

    Returns:
        direct_tools: registry tool names usable this step
        skill_hints: (plan_reference, canonical_skill_name) pairs
        offline: references that match neither tools nor skills
    """
    direct: List[str] = []
    skills: List[Tuple[str, str]] = []
    offline: List[str] = []

    for ref in step_tools:
        if ref in DELEGATE_STEP_TOOLS or ref.startswith("delegate_to_"):
            if ref not in direct:
                direct.append(ref)
            continue
        if ref in registry_names or ref in SKILL_META_TOOLS:
            if ref not in direct:
                direct.append(ref)
            continue

        canonical = resolve_skill_name(ref, skill_catalog_names)
        if canonical:
            skills.append((ref, canonical))
            continue

        offline.append(ref)

    return direct, skills, offline


def step_references_skill(step_tools: List[str], skill_catalog_names: Set[str]) -> bool:
    """True when any step tool entry resolves to a catalog skill."""
    for ref in step_tools:
        if ref in SKILL_META_TOOLS:
            return True
        if resolve_skill_name(ref, skill_catalog_names):
            return True
    return False
