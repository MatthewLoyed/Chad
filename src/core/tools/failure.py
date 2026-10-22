"""
failure.py — THE standard shape for every failure Chad's models are shown.

One vocabulary, one envelope, one renderer. Every failure the model can see —
tool errors, timeouts, param validation, harness refusals (loop guards), and
specialist/delegate crashes — goes through here. Nothing should hand a model a
bare human-phrased error string ever again.

WHY THIS EXISTS
---------------
Error text reaching an LLM is a *prompt*, not a log line. A human-facing message
("Please try again or verify the path") makes an agent apologize and stop; an
agent-directed one (named class + what to do next + what NOT to do) makes it
recover on its own. Before this module, exactly one tool family (the E2B sandbox)
classified its failures; the other ~200 failure returns handed the model a bare
string, so recovery quality depended on which tool happened to break.

THE ENVELOPE
------------
    {
      "success": False,
      "error": "<the rendered, agent-directed text>",   # what the model reads
      "failure_class": "auth",                          # closed vocabulary, below
      "retryable": False,                               # policy, not a guess
      "next_actions": ["...", "..."],                   # concrete moves, in order
      "detail": "<original raw message>",               # kept for logs/UI
    }

`error` deliberately holds the *rendered* text rather than the raw message: the
tool result is serialized to JSON for the model, and whichever field it reads
first must carry the guidance. `detail` preserves the original for humans.

CONTEXT BUDGET
--------------
Local models have unlimited attempts but finite context, so a failure that costs
6KB of stack trace is what actually ends a recovery — the model stops being able
to see the task, not the error. Renders are deliberately terse and
`clip_failure_text` keeps the TAIL (where NEXT_ACTIONS live), not the head.

Adding a class: add it to `_POLICY` and nothing else. Adding a domain-specific
classifier: register a matcher via `register_classifier` (see sandbox.py) instead
of forking `classify`.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── The closed vocabulary ────────────────────────────────────────────────────
# Each class carries its retry policy and the default recovery moves. These are
# the defaults a caller gets for free; any call site may override next_actions
# with something more specific to its domain.
#
# Policy rationale (retryable): a class is retryable only when a *later identical
# or lightly-adjusted* attempt could plausibly succeed. Anything only the user can
# fix (credentials, config, permissions) is non-retryable by definition — retrying
# it burns turns and context for a guaranteed-identical result.

AUTH = "auth"
CONFIG = "config"
PERMISSION = "permission"
NOT_FOUND = "not_found"
BAD_INPUT = "bad_input"
RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
EXPIRED = "expired"
RUNTIME = "runtime"
BLOCKED = "blocked"
CAPABILITY_GAP = "capability_gap"
UNKNOWN = "unknown"

_POLICY: Dict[str, Dict[str, Any]] = {
    AUTH: {
        "retryable": False,
        "next_actions": [
            "Do NOT retry this call — the credentials will be rejected identically.",
            "Tell the user exactly which credential is bad and where to fix it.",
            "If another tool can reach the same answer without these credentials, use it.",
        ],
    },
    CONFIG: {
        "retryable": False,
        "next_actions": [
            "Do NOT retry — this needs a configuration change only the user can make.",
            "Name the specific missing setting and how to set it.",
            "Continue with any part of the task that does not need this tool.",
        ],
    },
    PERMISSION: {
        "retryable": False,
        "next_actions": [
            "Do NOT retry — access is denied and will be denied again.",
            "Report the lack of permission plainly, then take a different path if one exists.",
        ],
    },
    NOT_FOUND: {
        "retryable": False,
        "next_actions": [
            "Do NOT repeat this call with the same target — it does not exist.",
            "Use a search/discovery tool to find the correct name, id, or path first.",
            "If discovery also finds nothing, say so instead of guessing another target.",
        ],
    },
    BAD_INPUT: {
        "retryable": True,
        "next_actions": [
            "Fix the specific argument named in the message, then call again ONCE.",
            "Re-read the tool's parameter schema before retrying — do not guess a value.",
            "If you cannot determine the right value, ask the user one specific question.",
        ],
    },
    RATE_LIMIT: {
        "retryable": True,
        "next_actions": [
            "Wait briefly, then retry once.",
            "If it fails again, switch to a different tool or reduce the request size.",
        ],
    },
    TIMEOUT: {
        "retryable": True,
        "next_actions": [
            "Retry once with a NARROWER request (smaller scope, fewer items, shorter range).",
            "An identical repeat will most likely time out identically — change something.",
            "If the second attempt also times out, use a different tool or report the limit.",
        ],
    },
    UNAVAILABLE: {
        "retryable": True,
        "next_actions": [
            "The service is down or unreachable, not misused. Retry at most once.",
            "If it fails again, use a different tool or tell the user the service is unavailable.",
        ],
    },
    EXPIRED: {
        "retryable": True,
        "next_actions": [
            "The session or resource expired. Retry once — it will be recreated.",
        ],
    },
    RUNTIME: {
        "retryable": True,
        "next_actions": [
            "Read the error and change ONE thing before trying again.",
            "Do not repeat the call unchanged — it will fail the same way.",
            "After two failed attempts, switch tools or approaches.",
        ],
    },
    BLOCKED: {
        "retryable": False,
        "next_actions": [
            "The harness refused this call — repeating it is not possible.",
            "Change your approach: different tool, different arguments, or a different path.",
        ],
    },
    CAPABILITY_GAP: {
        "retryable": False,
        "next_actions": [
            "No available tool can do this. Do NOT improvise a workaround chain.",
            "Name the specific missing capability to the user and finish what you can.",
        ],
    },
    UNKNOWN: {
        "retryable": True,
        "next_actions": [
            "Read the message and change your approach before any retry.",
            "Do not repeat the identical call.",
        ],
    },
}

VALID_CLASSES = frozenset(_POLICY)


# ── Classification ───────────────────────────────────────────────────────────
# Ordered most-specific-first. Auth/permission are checked before the generic
# 4xx bucket so a 401 never degrades into "bad_input" and get retried forever.

_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (AUTH, re.compile(
        r"\b401\b|unauthorized|invalid[ _-]?api[ _-]?key|authentication failed"
        r"|bad credentials|token (?:expired|invalid|revoked)|malformed authorization",
        re.I)),
    (PERMISSION, re.compile(
        r"\b403\b|forbidden|permission denied|access denied|not authorized|insufficient scope",
        re.I)),
    (CONFIG, re.compile(
        r"missing [A-Z0-9_]{3,}|not configured|no api key|api[ _-]?key is empty"
        r"|looks invalid|set (?:it )?in (?:settings|config)",
        re.I)),
    (RATE_LIMIT, re.compile(r"\b429\b|rate.?limit|too many requests|quota exceeded", re.I)),
    (TIMEOUT, re.compile(r"timed? ?out|timeout|deadline exceeded|read timeout", re.I)),
    (UNAVAILABLE, re.compile(
        r"\b50[0234]\b|service unavailable|connection (?:refused|error|reset|aborted)"
        r"|cannot connect|unreachable|temporarily unavailable|bad gateway",
        re.I)),
    (EXPIRED, re.compile(r"expired|no longer (?:exists|valid)|was not found.*sandbox|session (?:gone|closed)", re.I)),
    (NOT_FOUND, re.compile(
        r"\b404\b|not found|no such file|does not exist|nonexistent|unknown (?:tool|id|file)",
        re.I)),
    (BAD_INPUT, re.compile(
        r"\b4(?:00|22)\b|invalid (?:parameter|argument|input|value|json)|validation (?:error|failed)"
        r"|missing required|unexpected keyword|typeerror|schema",
        re.I)),
    (BLOCKED, re.compile(r"\bblocked\b|safety lock|refused by|not permitted by policy", re.I)),
]

# Domain matchers registered by tool modules (e.g. the E2B sandbox). Consulted
# BEFORE the generic patterns so a domain can be more precise about its own
# errors without duplicating this module's structure.
_CUSTOM: List[Callable[[str], Optional[str]]] = []


def register_classifier(matcher: Callable[[str], Optional[str]]) -> None:
    """Register a domain matcher: takes the raw message, returns a class or None."""
    _CUSTOM.append(matcher)


def classify(message: Any, *, exc: Optional[BaseException] = None) -> str:
    """Map a raw error message (and optionally the exception) to a failure class."""
    text = str(message or "")

    for matcher in _CUSTOM:
        try:
            found = matcher(text)
        except Exception:  # a bad matcher must never break error handling
            found = None
        if found in VALID_CLASSES:
            return found

    if exc is not None:
        import asyncio

        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return TIMEOUT
        if isinstance(exc, PermissionError):
            return PERMISSION
        if isinstance(exc, FileNotFoundError):
            return NOT_FOUND
        if isinstance(exc, (ConnectionError, OSError)) and not isinstance(exc, FileNotFoundError):
            return UNAVAILABLE
        if isinstance(exc, (TypeError, ValueError, KeyError)):
            return BAD_INPUT

    for cls, pattern in _PATTERNS:
        if pattern.search(text):
            return cls
    return UNKNOWN


# ── Rendering ────────────────────────────────────────────────────────────────

# A failure gets a hard character budget. Context is the scarce resource for a
# local model running many attempts — a 6KB trace ends a recovery by crowding out
# the task, not by being unreadable. Detail beyond this is noise the model cannot
# act on anyway.
MAX_DETAIL_CHARS = 700


def _clip_detail(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= MAX_DETAIL_CHARS:
        return text
    # Keep both ends: the head names the operation, the tail usually names the cause.
    head = text[: MAX_DETAIL_CHARS // 2]
    tail = text[-(MAX_DETAIL_CHARS // 2):]
    return f"{head} […] {tail}"


def render(
    detail: Any,
    failure_class: str,
    *,
    tool_name: Optional[str] = None,
    retryable: Optional[bool] = None,
    next_actions: Optional[List[str]] = None,
) -> str:
    """Render the agent-directed text a model reads on failure.

    Shape (stable — evals and the collapse helper both key off the first line):

        TOOL_FAILED[timeout] web_search: <detail>
        RETRY: allowed, but only with a changed call.
        NEXT_ACTIONS:
        1. ...
        2. ...
    """
    policy = _POLICY.get(failure_class, _POLICY[UNKNOWN])
    if retryable is None:
        retryable = bool(policy["retryable"])
    actions = next_actions if next_actions is not None else list(policy["next_actions"])

    who = f" {tool_name}" if tool_name else ""
    lines = [f"TOOL_FAILED[{failure_class}]{who}: {_clip_detail(detail)}"]
    lines.append(
        "RETRY: allowed, but only with a CHANGED call."
        if retryable
        else "RETRY: pointless — this will fail identically. Change approach or report it."
    )
    if actions:
        lines.append("NEXT_ACTIONS:")
        lines.extend(f"{i}. {a}" for i, a in enumerate(actions, 1))
    return "\n".join(lines)


# ── The envelope ─────────────────────────────────────────────────────────────


def failure(
    error: Any,
    *,
    failure_class: Optional[str] = None,
    retryable: Optional[bool] = None,
    next_actions: Optional[List[str]] = None,
    tool_name: Optional[str] = None,
    exc: Optional[BaseException] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build the canonical failure envelope. The one way to fail a tool."""
    detail = str(error)
    cls = failure_class if failure_class in VALID_CLASSES else classify(detail, exc=exc)
    policy = _POLICY[cls]
    is_retryable = policy["retryable"] if retryable is None else bool(retryable)
    actions = list(policy["next_actions"]) if next_actions is None else list(next_actions)

    payload: Dict[str, Any] = {
        "success": False,
        "error": render(
            detail, cls, tool_name=tool_name, retryable=is_retryable, next_actions=actions
        ),
        "failure_class": cls,
        "retryable": is_retryable,
        "next_actions": actions,
        # Clipped, not raw: this field is serialized into the model's context, and an
        # unbounded stack trace here would undo the whole point of the budget. The
        # full text is already in the logs via the caller's logger.error(exc_info=True).
        "detail": _clip_detail(detail),
    }
    # `extra` carries tool-specific payload (partial results, ids, paths) — it must
    # not be able to silently overwrite the classification or the rendered guidance,
    # which the executor, the collapse pass, and the UI all key off. Callers that
    # genuinely need different text pass `next_actions`/`failure_class` instead.
    reserved = set(payload)
    payload.update({k: v for k, v in extra.items() if k not in reserved})
    return payload


def ensure_envelope(result: Any, *, tool_name: Optional[str] = None) -> Any:
    """Upgrade any legacy `{"success": False, "error": "..."}` in place of a rewrite.

    This is what gives whole-fleet coverage without editing ~200 return sites:
    the executor runs every failing result through here, so a tool that still
    returns a bare string still reaches the model as a classified envelope. A
    result that already carries `failure_class` is left untouched — a tool that
    classified itself knows better than the generic matcher.
    """
    if not isinstance(result, dict) or result.get("success") is not False:
        return result
    if result.get("failure_class") in VALID_CLASSES:
        return result

    raw = result.get("detail") or result.get("error") or "Tool failed with no error message."
    upgraded = failure(raw, tool_name=tool_name)
    # Preserve everything the tool returned; only the failure fields are replaced.
    merged = {k: v for k, v in result.items() if k not in upgraded}
    merged.update(upgraded)
    return merged


# ── Delegate failures ────────────────────────────────────────────────────────
# A specialist crash and a tool crash are the same event to the supervisor's
# model: it made a call, the call failed, it must decide what to do next. These
# live here rather than in handoffs.py because BOTH delegate paths
# (`create_handoff_tools` and `create_subagent_adapter`) need the identical text,
# and handoffs.py is already well over the module size ceiling.

_INFRA_MARKERS = (
    "no models loaded", "model not found", "does not exist", "failed to load",
    "connection refused", "connection error", "cannot connect", "not loaded",
)


def is_infra_failure(message: Any) -> bool:
    """Would this failure repeat identically on every retry? (model/host problems)"""
    err = str(message).lower()
    return any(marker in err for marker in _INFRA_MARKERS)


def delegate_tool_name(agent_name: str) -> str:
    """Canonical `delegate_to_<agent>` — underscored, never spaced.

    The rendered name is parsed back out by `failure_signature`, which stops at
    whitespace. A prettified "browser agent" would truncate the signature to the
    bare class, silently merging every agent's failures into one bucket and
    collapsing unrelated messages. Prose gets the pretty name; the marker does not.
    """
    return f"delegate_to_{str(agent_name).strip().replace(' ', '_')}"


def _delegate(
    agent_name: str, detail: str, cls: str, next_actions: List[str]
) -> str:
    return render(
        detail, cls, tool_name=delegate_tool_name(agent_name), next_actions=next_actions
    )


def _pretty(agent_name: str) -> str:
    return str(agent_name).replace("_", " ").strip()


def delegate_blocked(agent_name: str, attempts: int) -> str:
    return _delegate(
        agent_name,
        f"The {_pretty(agent_name)} agent has already been delegated to {attempts} "
        f"times for this task without resolving it.",
        BLOCKED,
        [
            "Further delegation to this specialist is blocked for this task.",
            "Do the remaining work with your own tools, or use a different specialist.",
            "If neither is possible, call fail_step with what you learned.",
        ],
    )


def delegate_empty(agent_name: str) -> str:
    return _delegate(
        agent_name,
        f"The {_pretty(agent_name)} agent returned no messages.",
        RUNTIME,
        [
            "Re-delegate ONCE with a more specific, concrete task description — a "
            "vague brief is the usual cause.",
            "If it comes back empty again, do the work yourself or use a different "
            "specialist.",
        ],
    )


def delegate_timeout(agent_name: str) -> str:
    return _delegate(
        agent_name,
        f"The {_pretty(agent_name)} agent took too long to respond and was aborted.",
        TIMEOUT,
        [
            "Re-delegate ONCE with a NARROWER task (fewer items, smaller scope) — "
            "the same brief will time out again.",
            "If the narrowed task also times out, do the smaller pieces yourself or "
            "report the limit.",
        ],
    )


def delegate_unavailable(agent_name: str, detail: Any) -> str:
    """Infrastructure failure — identical on every retry, so forbid re-delegation."""
    return _delegate(
        agent_name,
        f"The {_pretty(agent_name)} agent is UNAVAILABLE: {detail}",
        CAPABILITY_GAP,
        [
            "This fails identically on every retry — do NOT delegate to this agent "
            "again for this task.",
            "Complete the step another way (a different specialist or your own "
            "tools), or call fail_step.",
        ],
    )


def delegate_crashed(agent_name: str, detail: Any) -> str:
    """Anything else that blew up inside the specialist's subgraph."""
    cls = classify(str(detail))
    return _delegate(
        agent_name,
        f"The {_pretty(agent_name)} agent failed: {detail}",
        cls,
        list(_POLICY[cls]["next_actions"]),
    )


# ── Context hygiene ──────────────────────────────────────────────────────────
# Attempts are cheap on local inference; context is not. These keep N failed
# attempts from costing N × (full tool output).

# A failed call needs its class, cause, and next actions — not its stack trace.
# Successful outputs keep the larger budget applied elsewhere (tool_node).
# Kept as a module constant for the default so this module has no import-time
# dependency on config; `clip_failure_text` reads the live limit per call.
MAX_FAILED_OUTPUT_CHARS = 1800


def _failed_output_cap() -> int:
    from src.core.tools import limits

    try:
        return int(limits.get("failed_output_max_chars"))
    except Exception:
        return MAX_FAILED_OUTPUT_CHARS

# NOT anchored to line start: by the time this text reaches the model it has been
# JSON-serialized into a ToolMessage (`{"error": "TOOL_FAILED[...] ..."}`) and
# prefixed with `[TOOL_DATA]`, so the marker sits mid-line. Anchoring here was a
# real bug — every failure silently fell through to the generic 8000-char path.
_FAILED_LINE = re.compile(r"TOOL_FAILED\[([a-z_]+)\](?:\s+(\S+?):)?")


def clip_failure_text(text: str, limit: Optional[int] = None) -> str:
    """Clip a failed tool's serialized output, preserving BOTH ends.

    Middle-out rather than head-first. In the serialized envelope the rendered
    guidance (`error`) sits near the front and the raw `detail` near the back, and
    a tool may append arbitrary extra keys after either — so there is no single
    end that is safe to drop. Cutting the middle keeps the class line and the
    NEXT_ACTIONS block intact, which is all the model actually acts on.
    """
    if limit is None:
        limit = _failed_output_cap()
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return (
        f"{text[:half]}"
        f"\n[…{omitted} chars of failure detail omitted to keep context bounded…]\n"
        f"{text[-half:]}"
    )


def failure_signature(text: str) -> Optional[str]:
    """Stable identity for 'the same failure again' — class + tool, ignoring detail.

    Returns None for text that isn't a rendered failure, so callers can leave
    ordinary tool output alone.
    """
    m = _FAILED_LINE.search(text or "")
    if not m:
        return None
    return f"{m.group(1)}:{m.group(2) or ''}"
