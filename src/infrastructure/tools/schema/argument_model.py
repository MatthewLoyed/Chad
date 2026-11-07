"""Pydantic wrapper argument models derived from registry schemas."""


def build_argument_model(name, params, required):
    from pydantic import create_model, Field
    # Create a dynamic Pydantic model for the tool arguments
    fields = {}
    for p_name, p_info in params.items():
        p_type = p_info.get('type', 'string')
        p_desc = p_info.get('description', '')

        # Basic type mapping
        if isinstance(p_type, list):
            from typing import Union, Any
            type_map = {
                'integer': int,
                'boolean': bool,
                'number': float,
                'array': list,
                'object': dict,
                'string': str,
            }
            matched_types = [type_map[t] for t in p_type if t in type_map]
            py_type = Union[tuple(matched_types)] if matched_types else Any
        else:
            py_type = str
            if p_type == 'integer':
                py_type = int
            elif p_type == 'boolean':
                py_type = bool
            elif p_type == 'number':
                py_type = float
            elif p_type == 'array':
                py_type = list
            elif p_type == 'object':
                py_type = dict
            elif p_type == 'any':
                from typing import Any
                py_type = Any

        default = ... if p_name in required else p_info.get("default", None)
        fields[p_name] = (py_type, Field(default=default, description=p_desc))

    args_model = create_model(f"{name}_args", **fields)

    return args_model
