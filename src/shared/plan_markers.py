"""Shared utilities for [[PLAN]] marker stripping and plan extraction."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("PlanMarkers")

PLAN_BLOCK_RE = re.compile(r"\[\[PLAN\]\].*?\[\[/PLAN\]\]", re.DOTALL)
ORCHESTRATION_MARKER_RE = re.compile(
    r"(?:TASK_COMPLETED|TASK_FAILED|NEEDS_HUMAN|\[\[done\]\]|\[\[STEP_COMPLETE\]\]|\[\[STEP_FAILED\]\]|\[\[PAUSE\]\])\s*🦾?",
    re.IGNORECASE,
)


def strip_plan_markers(text: str) -> str:
    """Remove [[PLAN]]...[[/PLAN]] blocks from visible text."""
    if not text:
        return text
    return PLAN_BLOCK_RE.sub("", text).strip()


def strip_orchestration_markers(text: str) -> str:
    """Remove terminal orchestration sentinels from model output."""
    if not text:
        return text
    return ORCHESTRATION_MARKER_RE.sub("", text).strip()


def sanitize_response_text(text: str) -> str:
    """Full speech-path sanitizer: plan blocks + orchestration markers."""
    return strip_orchestration_markers(strip_plan_markers(text))


def extract_plan_from_text(response_text: str) -> Tuple[Optional[str], str]:
    """Split a model response into (plan_json_or_none, clean_user_facing_text).

    Always strips plan segments from returned text even when JSON parsing fails.
    """
    if not response_text:
        return None, response_text

    if "[[PLAN]]" in response_text:
        try:
            parts = response_text.split("[[PLAN]]")
            main_text = parts[0].strip()
            plan_segment = parts[1].split("[[/PLAN]]")[0].strip()
            return plan_segment, main_text
        except Exception as exc:
            logger.warning("Failed to parse embedded [[PLAN]] block: %s", exc)
            return None, strip_plan_markers(response_text)

    stripped = response_text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            obj = json.loads(stripped)
            if (isinstance(obj, dict) and "steps" in obj) or isinstance(obj, list):
                logger.info(
                    "[ PLAN ] Identified raw JSON plan in response; stripping from user-facing text."
                )
                return stripped, ""
        except json.JSONDecodeError:
            pass

    return None, response_text


def coerce_plan_steps(plan_payload: Any) -> Optional[List[dict]]:
    """Normalize planner payloads to a list of step dicts."""
    if isinstance(plan_payload, dict) and isinstance(plan_payload.get("steps"), list):
        return plan_payload["steps"]
    if isinstance(plan_payload, list):
        return plan_payload
    return None
