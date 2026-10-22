"""voice_quick_tool.py — tier 2 of the voice quick-command lane (issue #654 problem 3).

Tier 1 (`voice_fast_lane.py`) is instant but only catches phrasings its regex/
embedding rules recognize. This tier catches everything tier 1 misses that's STILL
a single-step command by asking the SAME fast local model already resident for voice
(`local_models.category_models.voice`, e.g. `qwen/qwen3.5-9b`) with NOTHING but a
minimal system prompt and a small fixed tool set — no persona, no history, no memory,
no RAG shortlist.

Info tools (`web_search`, `search_gmail`) run the tool then a SECOND minimal-context
call to synthesize a short spoken answer. Action tools (`open_*`, `hubspace_control`)
and `get_datetime` ack from the tool result without a second model call.

Fails closed (returns None -> tier 3) if the model declines, the tool fails, or
synthesis comes up empty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("VoiceQuickTool")

SYSTEM_PROMPT = (
    "You are a fast command router for a voice assistant. Call exactly ONE tool "
    "when the request is a single step that matches a tool below.\n"
    "Routing rules:\n"
    "- 'open/go to/pull up/launch' a website or app → open_browser or open_app "
    "(e.g. 'go to gmail' opens the Gmail site — do NOT search_gmail).\n"
    "- 'search/look up/find out/weather/who won' facts on the web → web_search.\n"
    "- 'what time/date is it' → get_datetime.\n"
    "- 'check/search my email/inbox' for messages → search_gmail.\n"
    "- turn lights on/off / brightness → hubspace_control.\n"
    "Reply with the single word NONE — do not call a tool — when the request needs "
    "multiple steps, calendar/memory of prior context, drafting/sending email, "
    "reading a full email thread, or is just conversation."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_browser",
            "description": (
                "Launch the default browser to a named site (e.g. 'youtube', 'reddit', "
                "'gmail'). Use the site/brand name, not a URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Site name or search term."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch a desktop application by name (Discord, Chrome, Spotify, Notepad, Calculator, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Application name."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for a query and return results (weather, sports scores, "
                "facts, 'look up X'). No visible window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Search queries.",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Get the current local time and/or date from the system clock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["time", "date", "datetime", "timestamp"],
                        "description": "What to return. Use 'time' for 'what time is it'.",
                    },
                },
                "required": ["format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_gmail",
            "description": (
                "Search or list recent Gmail messages in the user's inbox "
                "(keywords / unread). Use only when they want to CHECK email contents "
                "('check my email', 'any mail from X') — NOT when they want to open the "
                "Gmail website ('go to gmail', 'open gmail'). "
                "One-shot check only — not for drafting, sending, or full threads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional Gmail search keywords (e.g. 'lease', 'from:sam').",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "If true, only unread mail.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max messages to return (default 5 for voice).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hubspace_control",
            "description": (
                "Control Hubspace smart lights/devices: turn_on, turn_off, set_brightness, "
                "or list_devices."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["turn_on", "turn_off", "set_brightness", "list_devices"],
                        "description": "Action to perform.",
                    },
                    "device_name": {
                        "type": "string",
                        "description": "Device / room name (e.g. 'living room lights').",
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "0-100, only for set_brightness.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]

# Fire-and-forget style acks — opening something IS the complete answer.
QUICK_TOOL_ACK = {
    "open_browser": lambda args: f"Opening {args.get('query') or 'that'}.",
    "open_app": lambda args: f"Opening {args.get('name') or 'that'}.",
}
_KNOWN_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

# Tools whose payload must be spoken after a short synthesis call.
_SYNTH_TOOLS = frozenset({"web_search", "search_gmail"})

SYNTHESIS_SYSTEM_PROMPT = (
    "Answer the user's question in 1-2 short spoken sentences, using ONLY the "
    "tool results below. No markdown, no links, no lists — just the answer as "
    "you'd say it out loud. If the results don't actually answer the question, "
    "say briefly that you couldn't find a clear answer. "
    "After thinking, put the final spoken answer in your response content."
)

_DEFAULT_TIMEOUT_S = 45.0
_MAX_SOURCES_IN_DIGEST = 4
_MAX_SNIPPET_CHARS = 300
_MAX_GMAIL_IN_DIGEST = 5
# Thinking is always on for the voice 9B — budget enough for reason + answer.
_DECIDE_MAX_TOKENS = 900
_SYNTH_MAX_TOKENS = 1200

# Filled on every try_quick_tool_call for evals / debugging (not thread-safe across
# concurrent voice turns — voice is sequential on one Brain process).
LAST_TRACE: Dict[str, Any] = {}


def get_last_trace() -> Dict[str, Any]:
    return dict(LAST_TRACE)


def _format_search_digest(search_result: Any) -> str:
    """Compact text digest of a `web_search` tool result for the synthesis prompt."""
    if not isinstance(search_result, dict):
        return ""
    results = search_result.get("results")
    if not isinstance(results, list):
        return ""

    lines = []
    for query_result in results:
        if not isinstance(query_result, dict):
            continue
        sources = query_result.get("sources") or []
        for source in sources[:_MAX_SOURCES_IN_DIGEST]:
            if not isinstance(source, dict):
                continue
            title = source.get("title") or "Untitled"
            text = source.get("snippet") or source.get("content") or ""
            text = text[:_MAX_SNIPPET_CHARS].strip()
            if text:
                lines.append(f"- {title}: {text}")
    return "\n".join(lines)


def _format_gmail_digest(gmail_result: Any) -> str:
    """Compact digest of `search_gmail` for spoken synthesis."""
    if not isinstance(gmail_result, dict):
        return ""
    emails = gmail_result.get("emails")
    if isinstance(emails, list) and emails:
        lines = []
        for em in emails[:_MAX_GMAIL_IN_DIGEST]:
            if not isinstance(em, dict):
                continue
            who = em.get("from_name") or em.get("from_email") or "Unknown"
            subj = em.get("subject") or "No subject"
            snip = (em.get("snippet") or "")[:_MAX_SNIPPET_CHARS].strip()
            lines.append(f"- From {who}: {subj}" + (f" — {snip}" if snip else ""))
        return "\n".join(lines)
    result = gmail_result.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()[:2000]
    return ""


def _ack_from_result(tool_name: str, args: Dict[str, Any], result: Any) -> Optional[str]:
    """Build a spoken ack for tools that don't need a second LLM call.

    Returns None on tool failure so the turn falls through to tier 3 instead of
    speaking a long TOOL_FAILED dump.
    """
    if tool_name in QUICK_TOOL_ACK:
        return QUICK_TOOL_ACK[tool_name](args)

    if not isinstance(result, dict):
        return None

    if tool_name == "get_datetime":
        value = result.get("result")
        if not value:
            return None
        fmt = args.get("format") or result.get("format") or "datetime"
        if fmt == "time":
            return f"It's {value}."
        if fmt == "date":
            return f"Today is {value}."
        return f"It's {value}."

    if tool_name == "hubspace_control":
        if result.get("success") and result.get("result"):
            text = str(result["result"]).rstrip(".")
            return f"{text}."
        return None

    return None


def _chat_completions(
    *,
    messages: List[Dict[str, Any]],
    model: str,
    base_url: str,
    timeout: float,
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 200,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns ``(body, error)``. Thinking is always enabled for Qwen3."""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if "qwen3" in model.lower():
        payload["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()), None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning(f"[ VOICE QUICK TOOL ] LM Studio unreachable: {e}")
        return None, str(e)


def _extract_reasoning(message: Dict[str, Any]) -> Optional[str]:
    """Pull model reasoning if the provider exposes it (Qwen thinking / similar)."""
    for key in ("reasoning_content", "reasoning"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Some servers nest under message.reasoning_details
    details = message.get("reasoning_details")
    if isinstance(details, list):
        parts = [str(d.get("text") or d) for d in details if d]
        joined = "\n".join(p for p in parts if p).strip()
        return joined or None
    return None


def _parse_tool_call(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return None
    fn = tool_calls[0].get("function", {})
    name = fn.get("name")
    if name not in _KNOWN_TOOL_NAMES:
        return None
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    return {"name": name, "args": args}


def _spoken_answer_from_message(message: Dict[str, Any]) -> Optional[str]:
    """Pull the spoken answer after thinking.

    With thinking on, Qwen puts chain-of-thought in ``reasoning_content`` and the
    final line in ``content``. Prefer ``content``. If the server truncates before
    content is filled, recover the last complete quoted spoken draft from reasoning.
    """
    content = (message.get("content") or "").strip()
    if content:
        # Strip accidental markdown / think tags if a template leaked them.
        content = re.sub(r"</?think>", "", content, flags=re.I).strip()
        lines = [ln.strip().lstrip("*`- ").strip() for ln in content.splitlines() if ln.strip()]
        joined = " ".join(lines).strip()
        if joined and not joined.lower().startswith(("thinking", "analyze", "constraint")):
            return joined

    reasoning = _extract_reasoning(message) or ""
    quoted = re.findall(r'"([^"\n]{20,320})"', reasoning)
    scored: List[str] = []
    for candidate in quoted:
        text = candidate.strip()
        low = text.lower()
        if low.startswith(("thinking", "analyze", "question:", "constraint", "wait,")):
            continue
        if "tool results" in low or "spoken sentences" in low:
            continue
        if text[-1] in ".!?" and len(text) >= 40:
            scored.append(text)
    if scored:
        return scored[-1]
    return None


def _synthesize_sync(
    question: str, digest: str, model: str, base_url: str, timeout: float
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Second 9B call: turn tool results into a short spoken answer (thinking on).

    Returns ``(answer, error, reasoning)``. This is the "synth" step — search/gmail
    return raw data; without this the user would only hear silence after the tool.
    Thinking stays on; we budget enough tokens so ``content`` still gets the final
    spoken line.
    """
    if len(digest) > 1200:
        digest = digest[:1200] + "\n…"

    body, err = _chat_completions(
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Question: {question}\n\nTool results:\n{digest}",
            },
        ],
        model=model,
        base_url=base_url,
        timeout=timeout,
        max_tokens=_SYNTH_MAX_TOKENS,
    )
    if body is None:
        logger.warning(f"[ VOICE QUICK TOOL ] synthesis call unreachable: {err}")
        return None, f"unreachable:{err}", None
    message = body.get("choices", [{}])[0].get("message", {}) or {}
    reasoning = _extract_reasoning(message)
    answer = _spoken_answer_from_message(message)
    if not answer:
        finish = (body.get("choices", [{}])[0].get("finish_reason") or "")
        return None, f"empty_content:finish={finish}", reasoning
    return answer, None, reasoning


def _decide_sync(
    text: str,
    model: str,
    base_url: str,
    timeout: float,
) -> Dict[str, Any]:
    body, err = _chat_completions(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        model=model,
        base_url=base_url,
        timeout=timeout,
        tools=TOOLS,
        max_tokens=_DECIDE_MAX_TOKENS,
    )
    if body is None:
        return {"error": f"unreachable:{err}"}

    message = body.get("choices", [{}])[0].get("message", {}) or {}
    call = _parse_tool_call(message)
    return {
        "call": call,
        "content": (message.get("content") or "").strip() or None,
        "reasoning": _extract_reasoning(message),
        "raw_message_keys": sorted(message.keys()),
    }


async def try_quick_tool_call(
    text: str,
    tool_registry: Optional[Any],
    *,
    model: str,
    base_url: str,
    timeout: float = _DEFAULT_TIMEOUT_S,
    include_reasoning: bool = True,  # kept for call-site compat; thinking is always on
) -> Optional[str]:
    """Tier 2: ask the resident voice model to call ONE quick tool with no other
    context. Thinking is always on. Returns spoken ack/answer on a handled hit,
    or None to escalate to the full agent lane (tier 3).

    For search/gmail, runs a second "synth" call that turns tool results into
    1-2 spoken sentences (also with thinking on).
    """
    LAST_TRACE.clear()
    LAST_TRACE.update({
        "prompt": text,
        "include_reasoning": True,
        "tool": None,
        "args": None,
        "content": None,
        "reasoning": None,
        "synth_reasoning": None,
        "ack": None,
        "error": None,
        "ms": None,
    })

    if tool_registry is None:
        LAST_TRACE["error"] = "no_registry"
        return None

    t0 = time.perf_counter()
    decision = await asyncio.to_thread(
        _decide_sync, text, model, base_url, timeout
    )
    LAST_TRACE["content"] = decision.get("content")
    LAST_TRACE["reasoning"] = decision.get("reasoning")
    if decision.get("error"):
        LAST_TRACE["error"] = decision["error"]
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        return None

    call = decision.get("call")
    if call is None:
        LAST_TRACE["error"] = "no_tool_call"
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        return None

    tool_name, args = call["name"], call["args"]
    LAST_TRACE["tool"] = tool_name
    LAST_TRACE["args"] = args

    tool = tool_registry.get_langchain_tool_by_name(tool_name)
    if not tool:
        logger.warning(f"[ VOICE QUICK TOOL ] tool '{tool_name}' not found in registry.")
        LAST_TRACE["error"] = "tool_missing"
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        return None

    invoke_args = dict(args)
    if tool_name == "web_search":
        # Snippets only — full page fetch would add HTTP latency on top of model calls.
        invoke_args["fetch_content"] = False
    elif tool_name == "search_gmail":
        # Keep voice digests short.
        invoke_args.setdefault("limit", 5)

    try:
        result = await tool.ainvoke(invoke_args)
    except Exception as e:
        logger.warning(f"[ VOICE QUICK TOOL ] tool '{tool_name}' failed ({e!r}) for {invoke_args!r}.")
        LAST_TRACE["error"] = f"tool_failed:{type(e).__name__}"
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        return None

    if tool_name in _SYNTH_TOOLS:
        if tool_name == "web_search":
            digest = _format_search_digest(result)
        else:
            digest = _format_gmail_digest(result)
        LAST_TRACE["digest_chars"] = len(digest or "")
        if not digest:
            logger.info(f"[ VOICE QUICK TOOL ] {tool_name} returned no usable content for {text!r}.")
            LAST_TRACE["error"] = "empty_digest"
            LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
            return None
        answer, synth_err, synth_reason = await asyncio.to_thread(
            _synthesize_sync, text, digest, model, base_url, timeout
        )
        LAST_TRACE["synth_reasoning"] = synth_reason
        if answer is None:
            LAST_TRACE["error"] = f"synthesis_failed:{synth_err}"
            LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
            return None
        LAST_TRACE["ack"] = answer
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        logger.info(
            f"[ VOICE QUICK TOOL ] {tool_name} + synthesis for {text!r} "
            f"in {LAST_TRACE['ms']}ms"
        )
        return answer

    ack = _ack_from_result(tool_name, args, result)
    if not ack:
        # Surface a short spoken failure for hubspace instead of silent fallthrough
        # when the tool returned a structured error.
        if tool_name == "hubspace_control" and isinstance(result, dict) and result.get("error"):
            err = str(result["error"])
            if "not found" in err.lower():
                ack = "I couldn't find that light."
            else:
                ack = "I couldn't control that light."
            LAST_TRACE["ack"] = ack
            LAST_TRACE["error"] = f"hubspace:{err[:120]}"
            LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
            return ack
        LAST_TRACE["error"] = "tool_or_ack_failed"
        LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
        return None

    LAST_TRACE["ack"] = ack
    LAST_TRACE["ms"] = round((time.perf_counter() - t0) * 1000)
    logger.info(
        f"[ VOICE QUICK TOOL ] {tool_name}({args}) in {LAST_TRACE['ms']}ms"
    )
    return ack
