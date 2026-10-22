import logging
import json
from typing import Dict, Any, List, Optional

logger = logging.getLogger("ChadSupervisor")

# ── Structured plan schema (LM Studio / OpenAI json_schema response_format) ──────────
# The planner emits ONE object: {"steps": [...]}. We constrain to this so a local model
# returns clean JSON in `content` instead of free-form text we have to regex out.
# Thinking MUST be disabled on the planner turn (Qwen3 thinking models bind json_schema
# to the reasoning stream on LM Studio, leaving `content` empty).
PLAN_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "execution_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Sequential id, e.g. 'step_1'."},
                            "description": {"type": "string", "description": "One concrete, verifiable action."},
                            "tools": {"type": "array", "items": {"type": "string"}},
                            "status": {"type": "string", "enum": ["pending"]},
                        },
                        "required": ["id", "description", "tools", "status"],
                    },
                }
            },
            "required": ["steps"],
        },
    },
}


def _coerce_steps(obj: Any) -> Optional[List[Dict[str, Any]]]:
    """Pull a list of step dicts out of a parsed planner payload."""
    if isinstance(obj, dict) and isinstance(obj.get("steps"), list):
        return obj["steps"]
    if isinstance(obj, list):
        return obj
    return None


def _parse_plan(content: str, reasoning: str = "") -> Optional[List[Dict[str, Any]]]:
    """Extract the step list from a planner response.

    Order matters:
      1. Explicit [[PLAN]] markers (in content or reasoning) — unambiguous legacy signal.
         Checked FIRST so json_repair never mangles the literal '[[' into nested arrays.
      2. Clean JSON object/array — the structured-output (response_format) happy path.
      3. json_repair — last resort for local-model trailing prose / trailing commas.
    Returns None only when nothing parseable is found, so the caller decides how to degrade.
    """
    # 1. Explicit markers win.
    for blob in (content, reasoning):
        if blob and "[[PLAN]]" in blob:
            inner = blob.split("[[PLAN]]")[1].split("[[/PLAN]]")[0].strip()
            try:
                steps = _coerce_steps(json.loads(inner))
                if steps is not None:
                    logger.warning("[ PLANNER ] Recovered plan via legacy [[PLAN]] markers.")
                    return steps
            except (json.JSONDecodeError, TypeError):
                try:
                    from json_repair import repair_json
                    steps = _coerce_steps(repair_json(inner, return_objects=True))
                    if steps:
                        logger.warning("[ PLANNER ] Recovered plan via [[PLAN]] markers + json_repair.")
                        return steps
                except Exception:
                    pass

    # 2. Clean structured JSON (happy path).
    if content:
        try:
            steps = _coerce_steps(json.loads(content.strip()))
            if steps is not None:
                return steps
        except (json.JSONDecodeError, TypeError):
            pass
        # 3. json_repair last resort.
        try:
            from json_repair import repair_json
            steps = _coerce_steps(repair_json(content, return_objects=True))
            if steps:
                logger.warning("[ PLANNER ] Recovered plan via json_repair.")
                return steps
        except Exception:
            pass
    return None


def _next_actionable_step(plan: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First pending step, else first running step. Completed/failed are terminal."""
    return (
        next((s for s in plan if s.get("status") == "pending"), None)
        or next((s for s in plan if s.get("status") == "running"), None)
    )


def _resolve_step(plan: List[Dict[str, Any]], step_id: Optional[str], active_id: str = "") -> Optional[Dict[str, Any]]:
    """Resolve the step a complete_step/fail_step call refers to.

    Local models routinely pass a wrong or hallucinated step_id. Rather than silently
    no-op (which strands the plan on the same step forever), fall back to the currently
    active step, then to the first non-terminal step.
    """
    if step_id:
        step = next((s for s in plan if s.get("id") == step_id), None)
        if step:
            return step
    if active_id:
        step = next((s for s in plan if s.get("id") == active_id), None)
        if step:
            return step
    return next((s for s in plan if s.get("status") in ("pending", "running")), None)
