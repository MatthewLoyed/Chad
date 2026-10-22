"""Validate evidence references, not a model's confidence or causal storytelling."""
import json
from pathlib import PurePosixPath


SCHEMA = {
    "schema_version": 1,
    "observations": [{"id": "o1", "kind": "response_not_json"}],
    "hypotheses": [{"text": "A suspected cause, not an established fact", "observation_ids": ["o1"]}],
    "recommendation": "investigate",
    "repair": {"paths": [], "acceptance_check_ids": [], "regression_check_ids": []},
}


def triage_json_schema() -> dict:
    """Transport grammar only; validate_triage remains the evidence/scope authority."""
    def obj(properties):
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    string = {"type": "string", "minLength": 1}
    strings = {"type": "array", "items": string}
    observations = [
        obj({"id": string, "kind": {"const": "tool_call", "type": "string"},
             "index": {"type": "integer", "minimum": 0}, "name": string}),
        obj({"id": string, "kind": {"const": "response_equals", "type": "string"},
             "value": {"type": "string"}}),
        obj({"id": string, "kind": {"const": "response_not_json", "type": "string"}}),
    ]
    return obj({
        "schema_version": {"type": "integer", "const": 1},
        "observations": {"type": "array", "minItems": 1, "maxItems": 20,
                         "items": {"anyOf": observations}},
        "hypotheses": {"type": "array", "minItems": 1, "maxItems": 10,
                       "items": obj({"text": string, "observation_ids": {**strings, "minItems": 1}})},
        "recommendation": {"type": "string", "enum": ["investigate", "repair"]},
        "repair": obj({key: strings for key in SCHEMA["repair"]}),
    })


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and str(path) == value


def validate_triage(response: str, trace: dict, policy: dict) -> dict:
    """Only operator-owned scope/check IDs can authorize the next coding stage.

    Supported observations are exact predicates over supplied trace data. Hypothesis
    prose remains unverified, even when its cited observations are true.
    """
    errors = []
    try:
        data = _decode(response)
        if not isinstance(data, dict) or set(data) != set(SCHEMA):
            raise ValueError("Expected exact triage schema fields")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported triage schema version")
        if trace.get("error") or trace.get("warmup_error"):
            raise ValueError("Infrastructure errors cannot authorize a behavioral repair")
        if data["recommendation"] not in ("repair", "investigate"):
            raise ValueError("Unknown recommendation")
        observations = data["observations"]
        if not isinstance(observations, list) or not observations or len(observations) > 20:
            raise ValueError("Require 1–20 structured observations")
        ids = set()
        verified = []
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError("Invalid observation")
            oid = observation.get("id")
            if not isinstance(oid, str) or not oid or oid in ids:
                raise ValueError("Observation IDs must be unique nonempty strings")
            ids.add(oid)
            kind = observation.get("kind")
            if kind == "tool_call":
                if set(observation) != {"id", "kind", "index", "name"}:
                    raise ValueError("tool_call requires only id, kind, index, name")
                index = observation["index"]
                calls = trace.get("tool_calls")
                if type(index) is not int or index < 0 or not isinstance(calls, list) or index >= len(calls):
                    raise ValueError("Tool reference is outside the supplied trace")
                if not isinstance(observation["name"], str) or calls[index].get("name") != observation["name"]:
                    raise ValueError("Tool name contradicts the cited event")
                verified.append({**observation, "recorded_call": calls[index]})
            elif kind == "response_equals":
                if set(observation) != {"id", "kind", "value"} or not isinstance(observation["value"], str):
                    raise ValueError("response_equals requires a string value")
                if observation["value"] != trace.get("response"):
                    raise ValueError("Response claim contradicts the supplied trace")
                verified.append(observation)
            elif kind == "response_not_json":
                if set(observation) != {"id", "kind"} or not isinstance(trace.get("response"), str):
                    raise ValueError("response_not_json requires a captured response")
                try:
                    _decode(trace["response"])
                except ValueError:
                    verified.append(observation)
                else:
                    raise ValueError("Captured response is valid JSON")
            else:
                raise ValueError("Unsupported observation kind; free-text factual claims are not accepted")
        hypotheses = data["hypotheses"]
        if not isinstance(hypotheses, list) or not hypotheses or len(hypotheses) > 10:
            raise ValueError("Require 1–10 explicitly unverified hypotheses")
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict) or set(hypothesis) != {"text", "observation_ids"}:
                raise ValueError("Hypotheses require text and observation_ids only")
            refs = hypothesis["observation_ids"]
            if not isinstance(hypothesis["text"], str) or not hypothesis["text"].strip():
                raise ValueError("Empty hypothesis")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError("Hypothesis cites missing evidence")
        repair = data["repair"]
        if not isinstance(repair, dict) or set(repair) != set(SCHEMA["repair"]):
            raise ValueError("Invalid repair structure")
        for key in repair:
            if not isinstance(repair[key], list) or any(not isinstance(value, str) for value in repair[key]):
                raise ValueError("Repair fields must be string lists")
        if any(not _path(path) for path in repair["paths"]):
            raise ValueError("Repair paths must be normalized repository-relative files")
        validated = {"observations": verified, "hypotheses": hypotheses, "repair": repair,
                     "hypotheses_verified": False}
        if data["recommendation"] != "repair":
            return dict(valid=True, dispatch_allowed=False, reason="Further investigation requested", evidence=validated)
        signals = policy.get("failure_signals") or []
        if not signals or not any(isinstance(signal, dict) and signal and
                                  all(observation.get(key) == value for key, value in signal.items())
                                  for signal in signals for observation in observations):
            errors.append("No verified observation matches an operator-defined failure signal")
        for key in repair:
            allowed = policy.get(key)
            if not isinstance(allowed, list) or not allowed:
                errors.append(f"Operator policy missing {key}")
            elif not repair[key] or not set(repair[key]).issubset(set(allowed)):
                errors.append(f"Repair {key} is missing or outside operator policy")
        # Require every operator acceptance/regression check, not just a convenient subset.
        for key in ("acceptance_check_ids", "regression_check_ids"):
            if set(repair[key]) != set(policy.get(key) or []):
                errors.append(f"Repair omitted required {key}")
        definitions = policy.get("checks") or {}
        if not isinstance(definitions, dict) or any(
            not isinstance(definitions.get(check), str) or not definitions[check].strip()
            for key in ("acceptance_check_ids", "regression_check_ids") for check in repair[key]
        ):
            errors.append("Operator check IDs require concrete check definitions")
        return dict(valid=True, dispatch_allowed=not errors, errors=errors, evidence=validated)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return dict(valid=False, dispatch_allowed=False, errors=[str(exc)])


def coding_brief(gate: dict, policy: dict) -> str:
    if not gate.get("dispatch_allowed"):
        raise ValueError("Triage gate did not authorize dispatch")
    return (
        "Implement one bounded candidate repair. Verified observations are recorded facts; "
        "hypotheses are UNVERIFIED and may be wrong. Reproduce before editing; report a blocker "
        "instead of guessing. Never weaken tests or hardcode the example. Edit only the allowed "
        "files. Run every operator acceptance/regression check. No commit, push, network, model "
        "configuration changes, or starting/stopping a Chad Brain.\n"
        + json.dumps({"triage": gate["evidence"], "operator_policy": policy}, indent=2)
    )
