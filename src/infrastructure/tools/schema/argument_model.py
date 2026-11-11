"""Build the invocation model for a registry-backed StructuredTool."""

from typing import Any, Union

from pydantic import ConfigDict, Field, create_model


_PYTHON_TYPES = {
    "integer": int,
    "boolean": bool,
    "number": float,
    "array": list,
    "object": dict,
    "string": str,
}


def build_argument_model(name: str, schema: dict, *, strict: bool):
    required = set(schema.get("required", ()))
    fields = {}
    for key, spec in schema.get("properties", {}).items():
        kind = spec.get("type", "string")
        if isinstance(kind, list):
            matched = [_PYTHON_TYPES[item] for item in kind if item in _PYTHON_TYPES]
            if strict and "null" in kind:
                matched.append(type(None))
            annotation = Union[tuple(matched)] if matched else Any
        else:
            annotation = Any if kind == "any" else _PYTHON_TYPES.get(kind, str)
        if strict and key not in required:
            # The registry treats an explicit null as omission when appropriate.
            annotation = annotation | None
        default = ... if key in required else spec.get("default")
        fields[key] = (annotation, Field(default=default, description=spec.get("description", "")))

    config = ConfigDict(strict=True, extra="forbid") if strict else None
    return create_model(f"{name}_args", __config__=config, **fields)
