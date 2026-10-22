"""Operator-owned deterministic acceptance cases; no model-generated grading code."""
import json

CASES = {
    "sum": {
        "prompt": 'Use only these supplied values: 17, 28, 9. Return exactly one JSON object with keys "count" and "total", containing their count and sum as integers. No Markdown or commentary. No tools are needed.',
        "expected": {"count": 3, "total": 54},
    },
    "extract": {
        "prompt": 'From this supplied text only: "Mira has 4 red tokens and 7 blue tokens." Return exactly one JSON object with keys "name", "red", "blue". Use a string for name and integers for the counts. No Markdown or commentary. No tools are needed.',
        "expected": {"name": "Mira", "red": 4, "blue": 7},
    },
    "unknown": {
        "prompt": 'Supplied data: "The box contains red tokens; their count is unknown." Return exactly one JSON object with keys "color" and "count". Use JSON null for the unknown count. Do not guess. No Markdown or commentary. No tools are needed.',
        "expected": {"color": "red", "count": None},
    },
}
CONTRACT_ID = "structured-answer-v1"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def evaluate(case_id, trace):
    """Exact task contract, not a general measure of assistant quality."""
    if not isinstance(trace, dict) or trace.get("error") or trace.get("warmup_error"):
        return {"passed": None, "reason": "trace infrastructure error"}
    try:
        actual = json.loads(trace.get("response") or "", object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return {"passed": False, "reason": "response is not exactly a JSON value"}
    expected = CASES[case_id]["expected"]
    passed = (isinstance(actual, dict) and actual.keys() == expected.keys() and
              all(type(actual[key]) is type(value) and actual[key] == value for key, value in expected.items()))
    return {"passed": passed, "reason": "exact answer matches" if passed else "answer differs from task contract",
            "expected": expected, "actual": actual}


def summarize(before, after, baseline_regressions, patched_regressions):
    checks = {key: {"before": evaluate(key, baseline_regressions[key]),
                    "after": evaluate(key, patched_regressions[key])}
              for key in ("extract", "unknown")}
    baseline = evaluate("sum", before)
    replay = evaluate("sum", after)
    regression_passed = all(c["before"]["passed"] is True and c["after"]["passed"] is True for c in checks.values())
    infrastructure_error = any(c[side]["passed"] is None for c in checks.values() for side in ("before", "after"))
    return {"baseline_passed": baseline["passed"], "after_passed": replay["passed"],
            "regressions_passed": None if infrastructure_error else regression_passed,
            "passed": baseline["passed"] is False and replay["passed"] is True and regression_passed,
            "contract": CONTRACT_ID, "baseline": baseline, "replay": replay, "regressions": checks,
            "limitations": "One run per prompt per revision; no statistical reliability claim."}
