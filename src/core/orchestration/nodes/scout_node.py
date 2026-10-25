"""Scout node — planner-directed read-only gather limb on plan-lane turns."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from src.core.orchestration.context import (
    agent_name_var,
    parent_correlation_id_var,
    current_turn_delivery,
)
from src.core.orchestration.context import await_with_progress_heartbeat
from src.core.orchestration.agent_scope import agent_scope
from src.core.orchestration.scout_evidence import report_tool, evidence_record, render_evidence, relevant_known_context, validate_report_sources
from src.core.orchestration.scout_tool_policy import scout_tool_allowed

logger = logging.getLogger("ChadSupervisor")

# Emergency only — full scout doctrine lives in prompts/persona/scout.md (nodes must not
# own prompts; see docs/architecture/PERSONA_PROMPTS.md).
_SCOUT_FALLBACK_PROMPT = (
    "You are Scout. Follow the planner brief, gather facts with tools, then report what you found."
)

# Scout starts independently of the supervisor's task-tool selection. Discovery can
# add task-specific retrieval tools; it must never rebind blocked execution tools.
_SCOUT_CORE_TOOLS = (
    "web_search", "manage_user_memory", "search_for_tool", "read_file", "list_directory",
)

_SCOUT_BLOCKED_TOOLS = frozenset({
    "open_app", "ui_click", "ui_type", "keyboard_control", "mouse_control",
    "window_management", "run_saved_routine", "run_routine", "manage_connectors",
    "call_mcp_tool",  # generic execution bypasses the gather-only tool boundary

    "ask_user_question",  # clarifying Qs are the planner's job — Scout never asks the user
    "dispatch_scout",
    "skip_scout",
    "pause_for_input",
    "write_file",
    "build_html_file",
    "create_directory",
    "create_or_update_file",
    "create_repository",
    "propose_file_plan",
    "run_sandboxed_python",
    "run_sandboxed_bash",
    "send_email",
    "create_gmail_draft",
    "draft_gmail_reply",
    "send_gmail_message",
    "send_slack_message",
    "send_telegram_message",
})

_GATHER_ONLY_DELEGATE_SUFFIX = (
    "\n\n[GATHER-ONLY — Scout phase] READ and REPORT facts only. "
    "Do NOT write files, draft plans, send messages, or mutate state. "
    "Return paths, content summaries, URLs/IDs, and verified facts for the planner."
)

_WRITE_TASK_PATTERN = re.compile(
    r"\b(write|save|create|send|delete|move|update|post|publish|build)\b",
    re.IGNORECASE,
)
_READ_TASK_PATTERN = re.compile(
    r"\b(read|find|locate|search|list|return|report|summarize|inspect|open)\b",
    re.IGNORECASE,
)

_REPORT_BACK_NUDGE = (
    "[HARNESS: report phase] Research is finished for this Scout pass. "
    "Call finish_scout_report now using collected evidence; mark missing facts unresolved or unavailable. "
    "Only the reporting tool is loaded in this phase. This is a harness phase transition, not a user restriction."
)
_GATHER_RETRY_NUDGE = (
    "[HARNESS: gather phase] Read/search tools are enabled. The previous response did not call a tool. "
    "Gather the missing facts in the planner brief using those tools, or submit sourced findings and gaps "
    "with finish_scout_report. Read-only means observation is allowed; mutations are not."
)


def _schema_name(schema: Dict[str, Any]) -> Optional[str]:
    if "name" in schema:
        return schema["name"]
    fn = schema.get("function") or {}
    return fn.get("name")


def _schema_description(schema: Dict[str, Any]) -> str:
    desc = schema.get("description") or ""
    if not desc:
        fn = schema.get("function") or {}
        desc = fn.get("description") or ""
    return (desc or "")[:200]


def build_confirmed_tool_menu(
    rag_schemas: List[Dict[str, Any]],
    handoff_tools: List[Any],
    extra_schemas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Build the planner-facing tool menu (name + one-line description)."""
    menu: List[Dict[str, str]] = []
    seen: Set[str] = set()

    for schema in list(rag_schemas or []) + list(extra_schemas or []):
        name = _schema_name(schema)
        if not name or name in seen:
            continue
        seen.add(name)
        menu.append({"name": name, "description": _schema_description(schema)})

    for tool in handoff_tools or []:
        name = getattr(tool, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        desc = (getattr(tool, "description", None) or "")[:200]
        menu.append({"name": name, "description": desc})

    return menu


def _menu_tool_names(menu: List[Dict[str, str]]) -> Set[str]:
    return {entry["name"] for entry in menu if entry.get("name")}


def filter_scout_handoff_tools(handoff_tools: List[Any]) -> List[Any]:
    """Return delegate handoffs Scout is allowed to bind (planner owns code/system delegates)."""
    from src.shared.config_manager import ConfigManager

    if not ConfigManager.get("planner.scout_allow_delegates", False):
        return []
    allowlist = ConfigManager.get("planner.scout_delegate_allowlist") or [
        "traversal_agent",
        "deep_research_agent",
    ]
    allowed = {f"delegate_to_{name}" for name in allowlist}
    return [t for t in (handoff_tools or []) if getattr(t, "name", "") in allowed]


def _bind_scout_tools(
    state: Dict[str, Any],
    tool_registry: Any,
    handoff_tools: List[Any],
) -> tuple[List[Any], Dict[str, Any]]:
    """Resolve LangChain tools for the scout ReAct loop."""
    direct_tools: List[Any] = []
    for name in _SCOUT_CORE_TOOLS:
        tool = tool_registry.get_langchain_tool_by_name(name)
        if tool:
            direct_tools.append(tool)

    bound = [t for t in (handoff_tools or []) if scout_tool_allowed(getattr(t, "name", ""), tool_registry)] + direct_tools
    tool_map = {getattr(t, "name", ""): t for t in bound if getattr(t, "name", None)}
    return bound, tool_map


def build_scout_capability_summary(tool_registry: Any, loaded_names: Set[str]) -> str:
    """Compact catalog awareness without loading extra tools or promising live auth."""
    groups: Dict[str, List[str]] = {}
    families = (
        ("Gmail / email retrieval", {"gmail", "email", "mail", "inbox"}),
        ("Contacts lookup", {"contacts", "contact"}),
        ("Google Drive / document retrieval", {"drive", "document", "documents", "docs"}),
        ("Calendar lookup", {"calendar", "events", "event"}),
        ("Personal memory lookup", {"memory"}),
        ("Installed-app lookup", {"app"}),
        ("Browser inspection", {"browser", "snapshot", "ui"}),
        ("Local file reading / listing", {"file", "files", "directory", "filesystem", "pdf"}),
        ("Public web search", {"web", "searxng", "serper", "tavily"}),
    )
    reads = {"search", "read", "list", "get", "fetch", "find", "check", "inspect", "snapshot"}
    writes = {"send", "draft", "create", "delete", "update", "remove", "write", "trash",
              "modify", "archive", "move", "click", "type", "navigate", "launch"}
    for schema in tool_registry.get_all_schemas():
        if not isinstance(schema, dict):
            continue
        name = _schema_name(schema)
        if not name or name in _SCOUT_BLOCKED_TOOLS or not scout_tool_allowed(name, tool_registry):
            continue
        tokens = set(re.split(r"[^a-z0-9]+", name.lower()))
        if name != "manage_user_memory" and (not tokens & reads or tokens & writes):
            continue
        for label, keywords in families:
            if tokens & keywords:
                groups.setdefault(label, []).append(name)
                break
    lines = []
    for label, _ in families:
        names = sorted(set(groups.get(label, [])))
        if names:
            examples = ", ".join(
                f"`{name}` ({'loaded' if name in loaded_names else 'discover'})"
                for name in names[:2]
            )
            lines.append(f"- {label}: {examples}")
    return "\n".join(lines) or "No additional retrieval capabilities are registered."


def _refresh_loaded_tools(system_content: str, bound_tools: List[Any]) -> str:
    menu = "\n".join(f"- `{tool.name}`: {getattr(tool, 'description', '')[:200]}" for tool in bound_tools)
    return re.sub(r"(### LOADED TOOLS\n).*?(?=\n\n### USER REQUEST)",
                  lambda match: match.group(1) + menu, system_content, flags=re.DOTALL)


def _decode_scout_result(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def _serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


def _extract_injected_schemas(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    injected = result.get("_injected_schemas")
    if not injected and isinstance(result.get("result"), dict):
        injected = result["result"].get("_injected_schemas")
    return injected if isinstance(injected, list) else []


def _emit_scout_lifecycle(event_bus, scout_id: str, *, start: bool, success: bool = True, result: Any = None, prompt: Optional[str] = None) -> None:
    # This is the root header. Publishing under Scout's execution scope otherwise
    # inherits scout_id as its own parent and makes an unreachable/self-nested panel.
    with agent_scope("Scout", parent_id=None, set_parent=True):
        _publish_scout_lifecycle(event_bus, scout_id, start=start, success=success, result=result, prompt=prompt)


def _publish_scout_lifecycle(event_bus, scout_id: str, *, start: bool, success: bool = True, result: Any = None, prompt: Optional[str] = None) -> None:
    """Scout gather bubble — shared tool_lifecycle (no duplicated EventBus code)."""
    from src.core.orchestration.tool_lifecycle import publish_tool_completed, publish_tool_started

    if start:
        args: Dict[str, Any] = {"phase": "gather"}
        if prompt:
            args["prompt"] = prompt
            args["task"] = prompt
        publish_tool_started(
            "dispatch_scout",
            args,
            correlation_id=scout_id,
            display_text="Gathering evidence for plan…",
            parent_id=None,
            event_bus=event_bus,
        )
    else:
        publish_tool_completed(
            "dispatch_scout",
            result or {"gather_brief_ready": success},
            success=success,
            correlation_id=scout_id,
            display_text="Gathering evidence for plan…",
            parent_id=None,
            event_bus=event_bus,
        )


async def _execute_scout_tool(
    tool_name: str,
    args: Dict[str, Any],
    tool_map: Dict[str, Any],
    tool_registry: Any,
    config: RunnableConfig,
    scout_id: str,
) -> Any:
    if not scout_tool_allowed(tool_name, tool_registry):
        return {"success": False, "scout_blocked": True,
                "error": f"Tool '{tool_name}' has no approved read-only Scout operation."}
    tool = tool_map.get(tool_name)
    if not tool:
        return {"success": False, "error": f"Tool '{tool_name}' not bound for scout."}

    sanitized_args, block_reason = _sanitize_scout_tool_call(tool_name, args or {})
    if block_reason:
        return {"success": False, "error": block_reason, "scout_blocked": True}

    tc_config = dict(config or {})
    meta = dict(tc_config.get("metadata") or {})
    meta["tool_call_id"] = f"{scout_id}_{tool_name}_{uuid.uuid4().hex[:8]}"
    tc_config["metadata"] = meta

    from src.core.orchestration.tool_lifecycle import run_with_tool_lifecycle

    async def _invoke(arguments):
        if hasattr(tool, "ainvoke"):
            return _decode_scout_result(await tool.ainvoke(arguments, config=tc_config))
        if hasattr(tool, "coroutine"):
            return _decode_scout_result(await tool.coroutine(**arguments))
        return _decode_scout_result(await tool_registry.execute_tool(tool_name, arguments))

    async def _run():
        if tool_name == "search_for_tool":
            # Resolve an advertised exact name regardless of an accidentally narrow category.
            query = re.sub(r"[^a-z0-9]+", "_", str(sanitized_args.get("query", "")).lower()).strip("_")
            exact = [schema for schema in tool_registry.get_all_schemas()
                     if isinstance(schema, dict) and _schema_name(schema) == query
                     and scout_tool_allowed(query, tool_registry)]
            if exact:
                return {"success": True, "result": {"message": "Exact registered tool found; its schema will now be loaded.",
                        "_injected_schemas": exact, "tools": [{"name": query}], "exact_lookup": True}}
        result = await _invoke(sanitized_args)
        schemas = _extract_injected_schemas(result)
        permitted = [schema for schema in schemas if scout_tool_allowed(_schema_name(schema) or "", tool_registry)]
        if tool_name == "search_for_tool" and sanitized_args.get("category") and not permitted:
            # One broad catalog retry, not another research loop or an external action.
            broader = {key: value for key, value in sanitized_args.items() if key != "category"}
            result = await _invoke(broader)
            if isinstance(result, dict):
                result = {**result, "scout_retried_without_category": True}
        return result

    return await run_with_tool_lifecycle(
        tool_name, sanitized_args, _run, parent_id=scout_id,
        correlation_id=meta["tool_call_id"],
    )


def _sanitize_scout_tool_call(
    tool_name: str,
    args: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Enforce gather-only scout discipline before executing a tool."""
    if tool_name in _SCOUT_BLOCKED_TOOLS:
        return args, (
            f"Tool '{tool_name}' is blocked during scout gather (read-only phase). "
            "Gather facts only — the planner schedules writes."
        )

    if tool_name == "manage_user_memory" and args.get("action") not in {"search", "status"}:
        return args, "Scout may only search or inspect memory; saving and forgetting are blocked."

    if tool_name.startswith("delegate_to_"):
        from src.shared.config_manager import ConfigManager

        if not ConfigManager.get("planner.scout_allow_delegates", False):
            return args, (
                f"{tool_name} is not available during scout gather — use direct read/search tools. "
                "The planner will schedule delegates in execution steps."
            )

        task = str(args.get("task") or "").strip()
        if not task:
            return args, "Delegate calls during scout must include a gather-only `task`."

        allowlist = ConfigManager.get("planner.scout_delegate_allowlist") or [
            "traversal_agent",
            "deep_research_agent",
        ]
        allowed = {f"delegate_to_{name}" for name in allowlist}
        if tool_name not in allowed:
            return args, (
                f"{tool_name} is not available during scout gather — allowed: "
                f"{sorted(allowed)}."
            )

        if tool_name == "delegate_to_code_agent":
            return args, "delegate_to_code_agent is never available during scout gather."

        modified = dict(args)
        if _GATHER_ONLY_DELEGATE_SUFFIX not in task:
            modified["task"] = task + _GATHER_ONLY_DELEGATE_SUFFIX
        return modified, None

    return args, None


def _last_scout_message(scout_messages: List[Any]) -> str:
    """Scout's final tool-free AIMessage — this IS the gather_brief (no synth/filters)."""
    for msg in reversed(scout_messages or []):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return str(msg.content or "").strip()
    return ""


async def _force_report_turn(
    model: Any,
    scout_messages: List[Any],
    config: RunnableConfig,
    *,
    nudge: str = _REPORT_BACK_NUDGE,
    call_receipts: Optional[List[Dict[str, str]]] = None,
    known_context: str = "",
) -> List[Dict[str, str]]:
    """Bounded report phase: keep the actual report tool, never instruct zero tools."""
    scout_messages.append(HumanMessage(content=nudge))
    finish_tool = report_tool()
    report_model = model.copy(update={"thinking_level": "low"}).bind_tools([finish_tool])
    report = await await_with_progress_heartbeat(report_model.ainvoke(scout_messages, config=config))
    scout_messages.append(report)
    items: List[Dict[str, str]] = []
    for tc in getattr(report, "tool_calls", None) or []:
        raw: Any = {"success": False, "error": "Only finish_scout_report is available during report phase."}
        if tc.get("name") == "finish_scout_report" and not items:
            try:
                raw = await finish_tool.ainvoke(tc.get("args") or {}, config=config)
                if raw.get("_scout_report"):
                    error = validate_report_sources(raw["items"], call_receipts or [], known_context)
                    if error:
                        raw = {"success": False, "error": error}
                    else:
                        items = raw["items"]
            except Exception as exc:
                raw = {"success": False, "error": str(exc)}
        scout_messages.append(ToolMessage(content=_serialize_tool_result(raw), tool_call_id=tc.get("id", tc.get("name", "report")), name=tc.get("name", "report")))
    return items


async def _maybe_unload_main_model() -> None:
    """Free the heavy main model's VRAM for the duration of the gather.

    Scout runs on the small secondary model and never touches the main model, so we evict
    the main model while Scout works. We deliberately do NOT eagerly reload it afterwards —
    the planner's very next inference call JIT-loads it as a single instance. Issuing our own
    reload races that JIT load and makes LM Studio spin up a *second* copy of the heavy model
    (two 26B models at once → OOM/crash). Let JIT own the single reload.

    Routed through the shared coordinator's ``release`` (owner=OWNER_MAIN_MODEL) rather than a
    direct ``unload_model`` — this is a no-op if some other owner (e.g. an in-flight subagent
    swap) still holds a claim on the main model, instead of evicting it out from under them.

    No-ops when: secondary-model mode is off, the unload is disabled, the main model isn't
    local, or Scout shares the main model's weights.
    """
    from src.shared.config_manager import ConfigManager

    if not ConfigManager.get("planner.scout_use_secondary_model", True):
        return
    if not ConfigManager.get("planner.scout_unload_main_model", True):
        return
    if not ConfigManager.is_local_model_active():
        return

    main_cfg = ConfigManager.get_local_model_config()
    main_model = main_cfg.get("name")
    base_url = main_cfg.get("base_url")
    if not main_model or not base_url:
        return

    from src.shared.model_selection import secondary_config
    scout_model = secondary_config().get("name")
    if not scout_model or main_model.split(":")[0] == scout_model.split(":")[0]:
        return  # same weights — nothing to free

    from src.core.intelligence.model_swap import unload_model

    logger.info(f"[ SCOUT ] Unloading main model '{main_model}' for the gather (planner JIT-reloads it).")
    try:
        await unload_model(base_url, main_model)
    except Exception as exc:
        logger.warning(f"[ SCOUT ] Failed to unload '{main_model}': {exc}")


def make_scout_node(
    model: Any,
    tool_registry: Any,
    handoff_tools: List[Any],
) -> Callable:
    """Factory creating the scout gather node."""

    async def scout_node(state: Dict[str, Any], config: RunnableConfig):
        turn_id = state.get("turn_id") or state.get("session_id") or "unknown"
        scout_id = state.get("scout_correlation_id") or f"scout_{turn_id}"

        from src.application.events.event_bus import EventBus

        event_bus = EventBus.get_instance()

        from src.core.intelligence.prompt_builder import SystemPromptBuilder

        try:
            scout_instructions = SystemPromptBuilder.build(base_instruction="", scope="scout")
        except Exception:
            scout_instructions = _SCOUT_FALLBACK_PROMPT

        menu = build_confirmed_tool_menu(state.get("tools") or [], handoff_tools)
        scout_handoffs = filter_scout_handoff_tools(handoff_tools)
        bound_tools, tool_map = _bind_scout_tools(state, tool_registry, scout_handoffs)
        menu_block = "\n".join(
            f"- `{tool.name}`: {getattr(tool, 'description', '')[:200]}"
            for tool in bound_tools
        ) or "*(No read/search tools available.)*"

        capability_summary = build_scout_capability_summary(tool_registry, set(tool_map))

        user_goal = ""
        for msg in reversed(state.get("messages") or []):
            if isinstance(msg, HumanMessage):
                user_goal = str(msg.content)
                break

        scout_brief = (state.get("scout_gather_request") or "").strip()
        known_sources = [("turn supplied context", state.get("system_instruction") or "")]
        try:
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            known_sources.append(("personal core profile", GraphRAGClient.read_core_profile_sync() or ""))
            from src.infrastructure.repositories.prompt_registry import PromptRegistry
            known_sources.append(("persona:user", PromptRegistry.get_instance().get_prompt("user")))
        except Exception:
            pass
        known_context = relevant_known_context(scout_brief, user_goal, known_sources)
        gather_focus = ""
        if scout_brief:
            gather_focus = (
                f"### PLANNER BRIEF\n{scout_brief}\n\n"
            )

        from src.shared.config_manager import ConfigManager

        system_content = (
            f"{scout_instructions}\n\n"
            f"{gather_focus}"
            f"### REGISTERED RETRIEVAL CAPABILITIES\n{capability_summary}\n\n"
            f"### LOADED TOOLS\n{menu_block}\n\n"
            f"### USER REQUEST\n{user_goal}"
            f"{known_context}"
            f"{render_evidence(state.get('scout_evidence'))}"
        )
        # Do not copy the supervisor's composite system_instruction: it includes
        # unrelated retrieval/activity context. Scout retrieves facts for its brief.
        if not bound_tools:
            logger.info("[ SCOUT ] No tools bound — skipping gather loop.")
            _emit_scout_lifecycle(event_bus, scout_id, start=True, prompt=scout_brief or user_goal)
            _emit_scout_lifecycle(event_bus, scout_id, start=False, success=False,
                                  result={"error": "Scout has no available read/search tools.",
                                          "result": "Scout has no available read/search tools."})
            return {
                "gather_brief": "Scout has no available read/search tools.",
                "scout_evidence": evidence_record(state, scout_brief, [], "No available read/search tools.", status="unavailable"),
                "scout_coverage": None,
                "confirmed_tool_menu": menu,
                "scout_gather_request": None,
            }

        finish_tool = report_tool()
        bound_tools.append(finish_tool)
        tool_map[finish_tool.name] = finish_tool
        system_content = _refresh_loaded_tools(system_content, bound_tools)
        scout_messages: List[Any] = [SystemMessage(content=system_content), HumanMessage(content=(
            f"Gather the facts in this planner brief: {scout_brief or user_goal}\n"
            "Use the loaded read/search tools. Finish with finish_scout_report; report unknowns honestly."
        ))]
        report_items: List[Dict[str, str]] = []
        call_receipts: List[Dict[str, str]] = []
        scout_model = model.copy(update={"thinking_level": "low"}).bind_tools(bound_tools)

        extra_schemas: List[Dict[str, Any]] = []
        pending_approval = None
        updated_tools = list(state.get("tools") or [])

        max_tool_calls = int(ConfigManager.get("planner.scout_max_tool_calls", 20) or 20)
        wrapup_extra = int(ConfigManager.get("planner.scout_tool_calls_wrapup", 5) or 5)
        hard_limit = max_tool_calls + wrapup_extra
        tool_calls_made = 0
        wrapup_warned = False
        gather_retried = False
        report_attempted = False
        report_failures = 0
        report_failure_text = ""

        from src.core.orchestration.read_memory import fresh_read_cache

        with agent_scope("Scout", parent_id=scout_id, set_parent=True), fresh_read_cache():
            _emit_scout_lifecycle(event_bus, scout_id, start=True, prompt=scout_brief or user_goal)
            await _maybe_unload_main_model()

            try:
                while True:
                    response = await await_with_progress_heartbeat(
                        scout_model.ainvoke(scout_messages, config=config)
                    )
                    if not getattr(response, "tool_calls", None):
                        scout_messages.append(response)
                        if not tool_calls_made and not gather_retried:
                            gather_retried = True
                            scout_messages.append(HumanMessage(content=_GATHER_RETRY_NUDGE))
                            continue
                        break

                    scout_messages.append(response)
                    tool_calls_made += len(response.tool_calls)
                    for tc in response.tool_calls:
                        tool_name = tc["name"]
                        args = tc.get("args") or {}
                        if report_items:
                            scout_messages.append(ToolMessage(content="Report already submitted; no further tools executed.", tool_call_id=tc.get("id", tool_name), name=tool_name))
                            continue
                        try:
                            if tool_name == "finish_scout_report":
                                raw = await finish_tool.ainvoke(args, config=config)
                                if raw.get("_scout_report"):
                                    source_error = validate_report_sources(raw["items"], call_receipts, known_context)
                                    if source_error:
                                        raw = {"success": False, "error": source_error}
                                    else:
                                        report_items = raw["items"]
                            else:
                                raw = await _execute_scout_tool(
                                    tool_name, args, tool_map, tool_registry, config, scout_id
                                )
                                receipt_raw = raw
                                if isinstance(raw, str):
                                    try:
                                        receipt_raw = json.loads(raw)
                                    except ValueError:
                                        pass
                                failed = isinstance(receipt_raw, dict) and (receipt_raw.get("success") is False or receipt_raw.get("isError") is True)
                                call_receipts.append({"tool": tool_name, "call_id": tc.get("id", ""),
                                                      "outcome": "failed" if failed else "discovery" if tool_name == "search_for_tool" else "returned"})
                        except Exception as exc:
                            logger.warning("[ SCOUT ] Tool %s failed: %s", tool_name, exc)
                            raw = {"success": False, "error": str(exc)}

                        if isinstance(raw, dict) and raw.get("_pending_approval"):
                            pending_approval = raw
                            scout_messages.append(
                                ToolMessage(
                                    content=_serialize_tool_result(raw),
                                    tool_call_id=tc.get("id", tool_name),
                                    name=tool_name,
                                )
                            )
                            break

                        if tool_name == "finish_scout_report" and not report_items:
                            report_failures += 1
                            report_failure_text = str(raw.get("error", "Invalid Scout report")) if isinstance(raw, dict) else "Invalid Scout report"
                            if report_failures >= 2:
                                report_attempted = True  # Do not enter another report fallback.

                        discovered = _extract_injected_schemas(raw)
                        if tool_name == "search_for_tool" and discovered:
                            permitted = [schema for schema in discovered if _schema_name(schema) not in _SCOUT_BLOCKED_TOOLS
                                         and scout_tool_allowed(_schema_name(schema) or "", tool_registry)]
                            raw = {"success": True, "result": {
                                "message": ("Only the listed approved read-only tools were loaded. Launching, escalation and execution tools are excluded. "
                                            "If no match fits, search once more without a category for an inspection/read operation; otherwise report the gap. "
                                            "Public web search cannot inspect this machine or its tool catalog."),
                                "tools": [{"name": _schema_name(schema), "description": _schema_description(schema)} for schema in permitted],
                                "_injected_schemas": permitted,
                            }}
                        for schema in _extract_injected_schemas(raw):
                            name = _schema_name(schema)
                            if (name and name not in _SCOUT_BLOCKED_TOOLS
                                    and scout_tool_allowed(name, tool_registry)):
                                if not any(_schema_name(t) == name for t in updated_tools):
                                    updated_tools.append(schema)
                                lt = tool_registry.get_langchain_tool_by_name(name)
                                if lt and name not in tool_map:
                                    tool_map[name] = lt
                                    bound_tools.append(lt)
                                    scout_model = model.copy(update={"thinking_level": "low"}).bind_tools(
                                        bound_tools
                                    )

                        if tool_name == "search_for_tool":
                            system_content = _refresh_loaded_tools(system_content, bound_tools)
                            scout_messages[0] = SystemMessage(content=system_content)
                            if isinstance(raw, dict):
                                raw = {**raw, "loaded_tools_now": sorted(tool_map)}
                        elif tool_name != "finish_scout_report" and call_receipts:
                            receipt = call_receipts[-1]
                            raw = {**raw, "_scout_source": {"source_call_id": receipt["call_id"], "tool": tool_name, "outcome": receipt["outcome"]}} if isinstance(raw, dict) else {
                                "result": raw, "_scout_source": {"source_call_id": receipt["call_id"], "tool": tool_name, "outcome": receipt["outcome"]}}

                        scout_messages.append(
                            ToolMessage(
                                content=_serialize_tool_result(raw),
                                tool_call_id=tc.get("id", tool_name),
                                name=tool_name,
                            )
                        )

                    if pending_approval or report_items or report_failures >= 2:
                        break

                    if tool_calls_made >= hard_limit:
                        logger.warning(
                            "[ SCOUT ] Hard tool-call limit (%d) — forcing report turn.",
                            hard_limit,
                        )
                        report_attempted = True
                        report_items = await _force_report_turn(model, scout_messages, config,
                                                                call_receipts=call_receipts, known_context=known_context)
                        break

                    if tool_calls_made >= max_tool_calls and not wrapup_warned:
                        wrapup_warned = True
                        logger.info(
                            "[ SCOUT ] Soft tool-call limit (%d) — %d wrap-up calls left.",
                            max_tool_calls,
                            wrapup_extra,
                        )
                        scout_messages.append(
                            HumanMessage(
                                content=(
                                    f"[HARNESS: gather wrap-up] You've reached {max_tool_calls} tool calls. "
                                    f"Use at most {wrapup_extra} more only if you still need "
                                    "facts, then call finish_scout_report with findings and gaps."
                                )
                            )
                        )

                # If Scout stopped after tools without a text report, ask once for it.
                if not pending_approval and not report_items and not report_attempted and not _last_scout_message(scout_messages):
                    report_items = await _force_report_turn(model, scout_messages, config,
                                                            call_receipts=call_receipts, known_context=known_context)

                menu = build_confirmed_tool_menu(updated_tools, handoff_tools, extra_schemas)
                # Verbatim last Scout message — no synth, no filters, no tool dump.
                gather_brief = ("\n".join(
                    f"- [{item['status']}] {item['question']}: {item['finding']}" + (f" (source: {item['source']})" if item.get("source") else "")
                    for item in report_items
                ) if report_items else _last_scout_message(scout_messages))
                # Plain-text model exits remain visible but are not treated as structured verification.
                if report_failures >= 2 and not report_items:
                    gather_brief = "Scout report could not be validated after two attempts. Findings remain unverified. " + report_failure_text
                evidence = evidence_record(state, scout_brief, report_items, gather_brief[:16000],
                                           status="reported" if report_items else "report_failed" if report_failures >= 2 else "unstructured", calls=call_receipts)

                logger.info(
                    "[ SCOUT ] Gather complete (%d menu tools, brief=%d chars).",
                    len(menu),
                    len(gather_brief),
                )
                _emit_scout_lifecycle(
                    event_bus,
                    scout_id,
                    start=False,
                    success=True,
                    result={
                    "gather_brief_chars": len(gather_brief),
                    "tool_count": tool_calls_made,
                    "gather_brief": gather_brief,
                    "result": gather_brief or "Scout finished without a textual report.",
                },
                )

                result: Dict[str, Any] = {
                    "gather_brief": gather_brief,
                    "scout_evidence": evidence,
                    "scout_coverage": None,
                    "confirmed_tool_menu": menu,
                    "tools": updated_tools,
                    # Clear the request so route_after_planner does not re-dispatch Scout.
                    "scout_gather_request": None,
                }
                if pending_approval:
                    result["pending_approval"] = pending_approval
                return result

            except Exception as exc:
                logger.error("[ SCOUT ] Gather failed: %s", exc, exc_info=exc)
                _emit_scout_lifecycle(event_bus, scout_id, start=False, success=False, result={"error": str(exc)})
                return {
                    "gather_brief": f"(Scout gather failed: {exc})",
                    "scout_evidence": evidence_record(state, scout_brief, [], str(exc), status="failed", calls=call_receipts),
                    "scout_coverage": None,
                    "confirmed_tool_menu": menu,
                    "tools": updated_tools,
                    "scout_gather_request": None,
                }

    return scout_node


def validate_plan_tools_against_menu(
    plan_steps: List[Dict[str, Any]],
    menu: Optional[List[Dict[str, str]]],
) -> Optional[str]:
    """Return validation feedback if any step references tools outside the menu."""
    forbidden = {
        "ask_user_question",
        "pause_for_input",
        "dispatch_scout",
        "skip_scout",
        "review_scout",
        "propose_plan",
        "switch_to_plan_mode",
    }
    bad_interactive: Set[str] = set()
    for step in plan_steps:
        for tool in step.get("tools") or []:
            if tool in forbidden:
                bad_interactive.add(tool)
    if bad_interactive:
        return (
            f"Plan steps must not include planning-phase tools: {sorted(bad_interactive)}. "
            "Those belong in the planner info-needs phase, not the execution checklist. "
            "Re-plan with only executable DO steps."
        )

    if not menu:
        return None
    allowed = _menu_tool_names(menu)
    if not allowed:
        return None
    unknown: Set[str] = set()
    for step in plan_steps:
        for tool in step.get("tools") or []:
            if tool and tool not in allowed:
                unknown.add(tool)
    if not unknown:
        return None
    return (
        f"Plan steps reference tools not in the confirmed menu: {sorted(unknown)}. "
        f"Allowed tools: {sorted(allowed)}. Re-plan using ONLY confirmed tools."
    )
