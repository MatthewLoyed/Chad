"""Feature-gated per-need selection; preserves prerequisites through final union."""
import json
import re
import urllib.request
from src.core.intelligence.model_lifecycle import active_use_sync

NEEDS_PROMPT = (
    "Split the user request into required capabilities, including implied external reads/actions and output. "
    "Do not invent work. Preserve requested mutations, IDs, dates and artifact types. "
    "An artifact such as a flowchart is ONE creation need; its nodes/edges are content, not operations. "
    "External facts are NOT already supplied: Calendar, files, email, repository state and host metrics need tools. "
    "Use mode no_tool only for composing an answer from facts already in this message. "
    "Use tool for external reads/actions or creating an artifact. Return the needs object."
)
NEEDS_SCHEMA = {"type": "object", "properties": {"needs": {"type": "array", "minItems": 1, "items": {
    "type": "object", "properties": {"id": {"type": "string"}, "requirement": {"type": "string"},
    "mode": {"type": "string", "enum": ["tool", "no_tool"]}},
    "required": ["id", "requirement", "mode"], "additionalProperties": False}}},
    "required": ["needs"], "additionalProperties": False}


def pure_response_need(requirement):
    """Conservative no-tool gate, not a tool-name/capability whitelist."""
    text = requirement.lower().strip()
    external = re.search(r"\b(retrieve|fetch|read|search|check|obtain|locate|open|send|create|delete|update|schedule|execute|uptime|commit)\b", text)
    return bool(re.match(r"(compose|summarize|present|explain|rewrite|answer|calculate)\b", text)) and not external


def extract_needs(selector, query):
    if not selector.is_available():
        raise RuntimeError("Selector unavailable")
    model = selector._resolved_model_id or selector._model_id
    request = urllib.request.Request(selector._completions_url,
        data=json.dumps({"model": model, "temperature": 0, "max_tokens": 1536,
            "messages": [{"role": "system", "content": NEEDS_PROMPT}, {"role": "user", "content": query}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "needs", "strict": True, "schema": NEEDS_SCHEMA}}}).encode(),
        headers=selector._request_headers(), method="POST")
    with active_use_sync(selector._base_url, model):
        with urllib.request.urlopen(request, timeout=selector._select_timeout()) as response:
            raw = selector._extract_message_content(json.loads(response.read()))
    from jsonschema import validate
    parsed = json.loads(raw)
    validate(parsed, NEEDS_SCHEMA)
    for need in parsed["needs"]:
        if need["mode"] == "no_tool" and not pure_response_need(need["requirement"]):
            need["mode"] = "tool"
            need["mode_correction"] = "external/unknown prerequisite cannot be satisfied by assuming facts"
    return parsed["needs"]


def select_per_need(turn_selector, query, catalog, history, knowledge_context, on_start):
    """Unknown extraction/selection falls back via caller; never authorizes actions."""
    selector = turn_selector._llm_selector
    try:
        needs = extract_needs(selector, query)
        union = []; mappings = []
        for need in needs:
            if need["mode"] == "no_tool":
                mappings.append({**need, "selected": [], "status": "no_tool"})
                continue
            scoped = turn_selector._selector_catalog(need["requirement"], history, {},
                knowledge_context=knowledge_context, category="planning")
            candidates = {**catalog, **scoped}
            scoped_query = ("Select tools for THIS REQUIRED CAPABILITY ONLY: " + need["requirement"] +
                "\nOriginal request (context, not additional selection targets): " + query +
                "\nInclude required prerequisites. Return [] if no listed tool can satisfy it.")
            chosen = selector.select(scoped_query, candidates, history=history,
                knowledge_context=knowledge_context, core_tools=list(turn_selector._core_tool_names), on_completions_start=on_start)
            valid = list(dict.fromkeys(name for name in chosen or [] if name in candidates or name in turn_selector._core_tool_names))
            mappings.append({**need, "selected": valid, "status": "selected" if valid else "unresolved"})
            union.extend(valid)
        # Failed/empty external coverage is explicit and preserves the proven old path.
        gaps = [n["id"] for n in mappings if n["status"] == "unresolved"]
        selector._last_select_meta = {"path": "per_need", "needs": mappings, "unresolved": gaps,
                                      "selected": list(dict.fromkeys(union))}
        return None if gaps else list(dict.fromkeys(union))
    except Exception as exc:
        selector._last_select_meta = {"path": "per_need", "status": "failed", "error_type": type(exc).__name__}
        return None


def choose_tools(turn_selector, query, catalog, history, on_start, knowledge_context):
    from src.shared.config_manager import ConfigManager
    selector = turn_selector._llm_selector
    kwargs = dict(history=history, on_completions_start=on_start,
                  knowledge_context=knowledge_context, core_tools=list(turn_selector._core_tool_names))
    if not ConfigManager.get("tools.per_need_selection", False):
        return selector.select(query, catalog, **kwargs)
    selected = select_per_need(turn_selector, query, catalog, history, knowledge_context, on_start)
    if selected is not None:
        return selected
    failed_mapping = selector.last_select_meta
    fallback = selector.select(query, catalog, **kwargs)
    selector._last_select_meta["per_need_fallback"] = failed_mapping
    return fallback
