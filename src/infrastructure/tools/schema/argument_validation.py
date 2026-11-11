"""Validate opted-in native tools before dispatch and render safe feedback."""

import json
from collections.abc import Callable
from typing import Any

from jsonschema import FormatChecker, validators
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from src.core.tools import failure


# Legacy schemas may advertise narrower inputs than their executors accept.
# Enroll tools after checking both their schema and their normal callers.
_SCHEMA_VALIDATED_TOOLS = frozenset({"get_calendar_events"})
_FRAMEWORK_KEYS = frozenset({"context", "memory_engine", "tool_registry"})
_FORMAT_CHECKER = FormatChecker()
ValidationIssues = list[dict[str, str]]


def uses_argument_validation(name: str) -> bool:
    return name in _SCHEMA_VALIDATED_TOOLS


def argument_failure(name: str, schema: dict, issues: ValidationIssues) -> dict:
    return failure.failure(
        "Arguments rejected before dispatch",
        failure_class=failure.BAD_INPUT,
        tool_name=name,
        validation_errors=issues,
        argument_schema=schema,
        dispatched=False,
        next_actions=["Correct the named parameters using argument_schema before trying again."],
    )


def validate_arguments(
    name: str,
    arguments: dict[str, Any],
    schema: dict,
    check_semantics: Callable[[dict[str, Any]], ValidationIssues] | None,
    *,
    registry: object,
) -> tuple[dict[str, Any], dict | None]:
    """Validate model arguments separately from trusted executor metadata."""
    validator_type = validators.validator_for(schema)
    try:
        validator_type.check_schema(schema)
    except SchemaError:
        return arguments, failure.failure(
            "Registered argument schema is invalid",
            failure_class=failure.CONFIG,
            tool_name=name,
            dispatched=False,
        )

    validator = validator_type(schema, format_checker=_FORMAT_CHECKER)
    required = set(schema.get("required", ()))
    properties = schema.get("properties", {})
    # ToolExecutor may already have injected its registry and request resources.
    # A model-provided JSON value cannot be the live registry instance.
    internal = (
        {key: arguments[key] for key in _FRAMEWORK_KEYS & arguments.keys()}
        if arguments.get("tool_registry") is registry
        else {}
    )
    # Local models send null for omitted optional fields. Preserve explicit null
    # when the schema accepts it and leave required fields for validation.
    normalized = {
        key: value
        for key, value in arguments.items()
        if key not in internal and (
            value is not None
            or key in required
            or key not in properties
            or validator.evolve(schema=properties[key]).is_valid(None)
        )
    }
    # Do not echo error.message or error.instance. Either can contain user data.
    issues = [
        {
            "path": ".".join(map(str, error.absolute_path)) or "$",
            "expected": f"registered schema constraint ({error.validator})",
        }
        for error in validator.iter_errors(normalized)
    ]
    if not issues and check_semantics is not None:
        issues = check_semantics(normalized)
    return normalized | internal, argument_failure(name, schema, issues) if issues else None


def wrapper_feedback(name: str, schema: dict) -> Callable[[ValidationError], str]:
    """Render failures from Pydantic with the registry's response shape."""
    def render(error: ValidationError) -> str:
        issues = [
            {
                "path": ".".join(map(str, item["loc"])) or "$",
                "expected": "registered argument name and type",
            }
            for item in error.errors(include_input=False, include_context=False)
        ]
        return json.dumps(argument_failure(name, schema, issues))

    return render
