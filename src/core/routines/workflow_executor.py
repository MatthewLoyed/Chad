"""Shared execution for saved routines (ordered tool steps, no LLM)."""

from typing import Any, Dict, List, Optional, Tuple

from src.shared.logger import setup_logger

logger = setup_logger("WorkflowExecutor")


def summarize_step_result(label: str, result: Any) -> str:
    if isinstance(result, dict):
        if result.get("success") is False or result.get("isError") is True:
            return str(result.get("error") or "failed")[:500]
        inner = result.get("result", result)
        if isinstance(inner, str) and inner.strip():
            return inner.strip()[:500]
    if isinstance(result, str) and result.strip():
        return result.strip()[:500]
    return f"{label} done."


async def execute_workflow_steps(
    tool_registry,
    steps: List[Dict[str, Any]],
    *,
    event_bus=None,
    correlation_prefix: str = "routine",
    abort_on_failure: bool = True,
) -> Tuple[List[str], bool]:
    """
    Run an ordered list of {tool_name, params, label} steps.

    Returns (human-readable result lines, all_succeeded).
    """
    from src.application.events import ToolStarted, ToolCompleted

    results: List[str] = []
    all_ok = bool(steps)

    for i, step in enumerate(steps):
        tool_name = step.get("tool_name")
        params = step.get("params", {}) or {}
        label = step.get("label") or tool_name
        correlation_id = f"{correlation_prefix}:{i}"

        if not tool_name or tool_name not in tool_registry._tools:
            line = f"⚠️ Step {i + 1} ('{label}') skipped: tool '{tool_name}' not registered."
            results.append(line)
            all_ok = False
            if abort_on_failure:
                break
            continue

        if event_bus:
            event_bus.publish(
                ToolStarted(
                    tool_name=tool_name,
                    args=params,
                    correlation_id=correlation_id,
                    display_text=label,
                )
            )
        try:
            result = await tool_registry.execute_tool(tool_name, params)
            success = not (isinstance(result, dict) and (
                result.get("success") is False or result.get("isError") is True
            ))
            error = None if success else str(result.get("error") or "Tool reported failure.")
        except Exception as e:
            logger.error(f"[ ROUTINE ] step {i + 1} ('{tool_name}') failed: {e}", exc_info=True)
            result, success, error = None, False, str(e)

        if event_bus:
            event_bus.publish(
                ToolCompleted(
                    tool_name=tool_name,
                    result=result,
                    success=success,
                    error=error,
                    correlation_id=correlation_id,
                    display_text=label,
                )
            )

        if not success:
            results.append(f"❌ Step {i + 1} ('{label}') failed: {error}")
            all_ok = False
            if abort_on_failure:
                break
        else:
            results.append(f"✅ Step {i + 1} ('{label}'): {summarize_step_result(label, result)}")

    return results, all_ok
