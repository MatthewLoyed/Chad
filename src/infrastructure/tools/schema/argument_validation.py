"""Authoritative schema validation before any registry-dispatched action."""
import copy
from datetime import datetime
import re
from jsonschema import validators, FormatChecker
from src.core.tools import failure


def _calendar_errors(name, args):
    if name != "get_calendar_events":
        return []
    dates = {}; errors = []
    for key in ("time_min", "time_max"):
        value = args.get(key)
        if value is None:
            continue
        try:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", value):
                raise ValueError()
            dates[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            errors.append({"path": key, "expected": "timezone-qualified RFC3339 timestamp"})
    if len(dates) == 2 and dates["time_min"] >= dates["time_max"]:
        errors.append({"path": "time_max", "expected": "later than time_min"})
    return errors


def validate_arguments(name, parameters, schema):
    """Return normalized args and canonical failure, or None when valid.

    Optional nulls mean omission only when the schema does not permit null.
    Original args remain in lifecycle records. Internal registry injection happens later.
    """
    schema = schema.get("function", schema).get("parameters", schema)
    args = copy.deepcopy(parameters)
    required = set(schema.get("required", []))
    for key, spec in schema.get("properties", {}).items():
        if key not in required and args.get(key, False) is None:
            validator = validators.validator_for(spec)(spec)
            if not validator.is_valid(None):
                args.pop(key)
    try:
        cls = validators.validator_for(schema)
        cls.check_schema(schema)
        errors = [{"path": ".".join(map(str, e.absolute_path)), "expected": e.message}
                  for e in cls(schema, format_checker=FormatChecker()).iter_errors(args)]
    except Exception:
        return args, failure.failure("Registered argument schema is invalid", failure_class=failure.CONFIG,
                                     tool_name=name, dispatched=False)
    errors.extend(_calendar_errors(name, args))
    if not errors:
        return args, None
    # Never echo values or whole object errors that may contain credentials.
    details = [{"path": e["path"], "expected": e["expected"] if name == "get_calendar_events" else "value violates registered schema"} for e in errors]
    return args, failure.failure(
        "Arguments rejected before dispatch", failure_class=failure.BAD_INPUT, tool_name=name,
        validation_errors=details, argument_schema=schema, dispatched=False,
        next_actions=["Correct the named parameter using argument_schema; do not resend unchanged arguments.",
                      "Call the tool again only with corrected arguments."])


def wrapper_feedback(name, schema):
    """Pydantic rejects some calls before registry entry; preserve the model error path."""
    def render(error):
        import json
        paths = [".".join(map(str, e["loc"])) for e in error.errors()]
        return json.dumps(failure.failure("Argument types rejected before dispatch",
            failure_class=failure.BAD_INPUT, tool_name=name, dispatched=False,
            validation_errors=[{"path": path, "expected": "registered argument type"} for path in paths],
            argument_schema=schema,
            next_actions=["Correct the named fields using argument_schema; do not resend unchanged arguments."]))
    return render
