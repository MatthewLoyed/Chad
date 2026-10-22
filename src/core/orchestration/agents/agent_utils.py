from langgraph.prebuilt import ToolNode
from typing import Any, Sequence, List, Union, Annotated
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from langgraph.channels.delta import DeltaChannel

import re
import json
from langchain_core.messages import ToolMessage

# Internal control-signal keys nodes/tool_node.py's wrapped_tool_node parses out of
# raw (pre-sanitized) ToolMessage JSON: schema/skill injection, plan-step mutation,
# voice escalation, and — critical for issue #279 — the pending-approval pause. These
# are structured envelopes our own orchestration code consumes, not page/DOM text
# shown to the model, so they must survive intact; sanitize_tool_output's [TOOL_DATA]
# prefix would otherwise break `json.loads()` downstream and silently swallow the
# signal (a gated write action would then never pause — see specialist_graph.py).
_CONTROL_SIGNAL_KEYS = (
    "_pending_approval", "_pause_for_input", "_step_complete", "_step_failed",
    "_voice_escalate", "_injected_schemas", "_loaded_skill", "_approval_declined",
    "_terminal_budget",
)


def _is_control_signal(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and any(key in parsed for key in _CONTROL_SIGNAL_KEYS)


# Provenance marker (#642): a tool sets this in its OWN return dict, at the point
# of production, when it knows its content came from a trusted source — a local
# file the user asked for, this repo's own artifacts, eval transcripts — as
# opposed to a web fetch, browser DOM, third-party MCP result, or email body.
# Only the tool itself can know this; sanitize_tool_output can't infer it from
# text content alone. See ReadLocalFileTool (src/tools/filesystem/
# discovery_tools.py) for the reference usage.
TRUSTED_SOURCE_KEY = "_trusted_source"


def _is_trusted_output(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get(TRUSTED_SOURCE_KEY) is True


def sanitize_tool_output(content: Any, *, trusted: bool = False) -> str:
    """
    Sanitizes raw tool outputs to prevent prompt injections.
    1. Ensures content is a string.
    2. Strips/neutralizes known injection pattern phrases (case-insensitive) —
       UNLESS `trusted=True` (#642).
    3. Adds a semantic [TOOL_DATA] role marker to signal to the LLM that this is data, not instructions.

    `trusted` distinguishes PROVENANCE, not content: a page trying to hijack the
    agent looks the same, textually, as a file legitimately discussing prompt
    engineering. Untrusted sources (web fetch, browser DOM, third-party MCP
    output, email bodies) are always stripped regardless of this flag — only a
    tool that KNOWS its own source is safe (see TRUSTED_SOURCE_KEY) can skip it.
    Do not widen this by loosening the patterns themselves; that weakens real
    injection defense instead of fixing the provenance mismatch.
    """
    if content is None:
        return "[TOOL_DATA]\nNone"

    if not isinstance(content, str):
        content = str(content)

    # Graph tools currently carry MCP results as JSON text, not multimodal
    # message parts. Never feed base64 pixels to a text model or clip them into
    # invalid JSON. Keep screenshot artifact links and require a usable observation.
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("_browser_tool"):
        parts = payload.get("result")
        if isinstance(parts, list):
            changed = False
            for index, part in enumerate(parts):
                if isinstance(part, dict) and part.get("type") == "image":
                    parts[index] = {
                        "type": "text",
                        "text": (
                            "[IMAGE ARTIFACT] Screenshot captured, but pixels are not available "
                            "through this text tool-result channel. Do not infer image contents. "
                            "Use browser_snapshot without a filename for page text and refs, "
                            "or a vision-capable observation tool."
                        ),
                    }
                    changed = True
            if changed:
                content = json.dumps(payload)

    # 1. Strip/neutralize known injection-pattern strings (case-insensitive regex)
    # Includes patterns aimed at browser/DOM/UIA-read text (issue #279 hardening):
    # on-screen text from an untrusted page — or, once desktop UIA support lands
    # (#278), from an untrusted window — is data, never instructions, no matter
    # how directly it addresses "the AI", "the agent", or "the assistant".
    injection_patterns = [
        r"(?i)ignore\s+(?:all\s+)?(?:previous\s+)?instructions",
        r"(?i)system\s+prompt",
        r"(?i)you\s+are\s+now\s+an?\s+assistant",
        r"(?i)new\s+instructions",
        r"(?i)system\s+override",
        r"(?i)assistant\s+override",
        r"(?i)jailbreak",
        r"(?i)dan\s+mode",
        r"(?i)do\s+anything\s+now",
        r"(?i)attention\s+(?:ai|assistant|agent|bot|llm)",
        r"(?i)(?:ai|assistant|agent|bot|llm)[,:]?\s+(?:please\s+)?(?:click|navigate|type|submit|enter|approve|ignore|disregard)",
        r"(?i)this\s+(?:page|site|element)\s+(?:requires|instructs)\s+you\s+to",
        r"(?i)end\s+of\s+(?:page|document|content)[,.]?\s*(?:new|real)\s+instructions",
    ]

    sanitized = content
    if not trusted:
        for pattern in injection_patterns:
            sanitized = re.sub(pattern, "[STRIPPED_POTENTIAL_INJECTION]", sanitized)

    # 2. Tag with [TOOL_DATA] role marker
    if not sanitized.startswith("[TOOL_DATA]"):
        sanitized = f"[TOOL_DATA]\n{sanitized}"

    return sanitized

def _sanitize_tool_node_result(result: Any) -> Any:
    if isinstance(result, dict) and "messages" in result:
        sanitized_messages = []
        for msg in result["messages"]:
            if isinstance(msg, ToolMessage) and not _is_control_signal(msg.content):
                msg.content = sanitize_tool_output(msg.content, trusted=_is_trusted_output(msg.content))
            sanitized_messages.append(msg)
        result["messages"] = sanitized_messages
    elif isinstance(result, list):
        sanitized_messages = []
        for msg in result:
            if isinstance(msg, ToolMessage) and not _is_control_signal(msg.content):
                msg.content = sanitize_tool_output(msg.content, trusted=_is_trusted_output(msg.content))
            sanitized_messages.append(msg)
        result = sanitized_messages

    return result


class SanitizedToolNode(ToolNode):
    """
    A custom ToolNode that sanitizes the output of executed tools
    before injecting them into the agent's message state.

    Overrides both `invoke` and `ainvoke`: `ToolNode.ainvoke` calls
    `self.afunc(...)` directly whenever an async func is registered (always
    true for a `ToolNode`, since its tools are awaited), which bypasses
    `invoke()` entirely — every graph in this codebase runs async, so without
    this override sanitize_tool_output (including the prompt-injection
    hardening for browser/DOM/UIA text, issue #279) never actually ran.
    """
    def invoke(self, input: Any, config: Any = None) -> Any:
        return _sanitize_tool_node_result(super().invoke(input, config))

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return _sanitize_tool_node_result(await super().ainvoke(input, config, **kwargs))

def create_fault_tolerant_tool_node(tools: list) -> ToolNode:
    """
    Wraps a list of tools in a SanitizedToolNode that captures exceptions and sanitizes outputs.
    """
    # Filters out empty tools to avoid crashing initialization
    valid_tools = [t for t in tools if t is not None]
    return SanitizedToolNode(valid_tools, handle_tool_errors=True)


# Re-exported for backward compatibility (moved to src.core.orchestration.graph.state)
from src.core.orchestration.graph.state import append_messages_bulk

