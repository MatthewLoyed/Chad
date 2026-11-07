"""Registry execution boundary: hydrate, validate, guard, route."""


async def dispatch_tool(registry, tool_name, parameters):
    tool = registry._tools.get(tool_name)
    if not tool:
        raise ValueError(f"Tool '{tool_name}' not found in registry.")

    # Check if it's a stub and hydrate if necessary
    if isinstance(tool, dict) and tool.get('is_stub'):
        tool = registry._load_stub(tool_name)
        if not tool:
            raise ValueError(f"Failed to hydrate stub tool '{tool_name}'.")

    from .schema.argument_validation import validate_arguments
    schema = registry._extract_schema(tool_name, tool)
    parameters, invalid = validate_arguments(tool_name, parameters, schema)
    if invalid is not None:
        return invalid

    from src.tools.base_tool import BaseTool
    if isinstance(tool, BaseTool) and "tool_registry" not in parameters:
        parameters = {**parameters, "tool_registry": registry}

    source = registry._tool_sources.get(tool_name, 'local')
    executor = registry._routing_engine.route_to_executor(tool)

    # Non-progress guard (src/core/tools/repetition.py). This is the one place
    # every lane passes through — the LangGraph tool node reaches tools via
    # this method, bypassing ToolCoordinator and its LoopDetector entirely.
    # Blocks only an identical call that already failed; everything else is an
    # advisory hint attached to the result.
    from src.core.tools import fault_injection, repetition

    # Measurement hook: OFF unless explicitly armed (env var or the
    # `injecting()` context manager — deliberately no config.yaml key, so a
    # stray committed flag can't make a production Brain fail real calls).
    # Placed before the guard so an injected failure is indistinguishable
    # from a real one to everything downstream — which is the point.
    injected = fault_injection.maybe_fail(tool_name)
    if injected is not None:
        repetition.current_guard().note(tool_name, parameters, False)
        return injected

    guard = repetition.current_guard()
    blocked = guard.check(tool_name, parameters)
    if blocked is not None:
        return blocked

    result = await executor.execute(tool, parameters)

    succeeded = not (isinstance(result, dict) and result.get("success") is False)
    hint = guard.note(tool_name, parameters, succeeded)
    if hint and isinstance(result, dict):
        # A separate key rather than appended to `error`/`result`: this is the
        # harness talking about the CALL PATTERN, not part of the tool's output.
        result = {**result, "harness_hint": hint}
    return result
