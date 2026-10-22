import asyncio
import json
from src.core.orchestration.scout_evidence import render_evidence
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Callable, Tuple
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage, AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from src.shared.logger import setup_logger
from src.application.events import EventBus, ToolStarted, ToolCompleted

logger = setup_logger("AgentHandoffs")

from src.infrastructure.tools.web_search_routing import (
    RESEARCH_AGENT_HANDOFF_DESC,
    BROWSER_AGENT_HANDOFF_DESC,
)
from src.core.intelligence.model_lifecycle import release, ensure_resident, current_priority, OWNER_MAIN_MODEL
from src.core.tools import failure, limits

# Strong refs for fire-and-forget swap tasks — asyncio doesn't keep a task alive on its
# own once the creating coroutine drops its reference, so without this the task can be
# garbage-collected mid-flight (and CPython logs "Task was destroyed but it is pending").
_background_swap_tasks: set = set()


def _budget_progress_report(result: Dict[str, Any], agent_name: str, summary: str) -> str:
    """Preserve partial progress without turning a capped child into a success."""
    terminal = bool(result.get("browser_budget_exhausted"))
    if not (terminal or result.get("budget_exceeded")):
        return summary
    actions = (
        ["No more browser delegations can run in this conversation. Use captured evidence or continue independent non-browser work."]
        if terminal else
        ["This specialist reached its local step cap. Review partial progress before choosing another bounded approach."]
    )
    return failure.failure(
        "Browser action budget exhausted." if terminal else "Specialist step cap reached.",
        failure_class=failure.BLOCKED,
        tool_name=f"delegate_to_{agent_name}",
        retryable=not terminal,
        next_actions=actions,
    )["error"] + "\n\nPARTIAL PROGRESS (not task completion):\n" + summary


def _browser_budget_blocked() -> str:
    return _budget_progress_report(
        {"browser_budget_exhausted": True}, "browser_agent", "No additional browser actions were attempted."
    )


def _fire_and_forget(coro, label: str) -> None:
    """Schedule `coro` without blocking the caller; failures are logged, never raised."""
    async def _runner():
        try:
            await coro
        except Exception as e:
            logger.debug(f"[ MODEL_SWAP ] Background {label} failed (non-fatal): {e}")

    task = asyncio.ensure_future(_runner())
    _background_swap_tasks.add(task)
    task.add_done_callback(_background_swap_tasks.discard)


_BROWSER_AGENT_PREAUTH = (
    "\n### ALREADY SIGNED IN — DO NOT LOG IN\n"
    "The Chrome profile you drive is the user's own and is ALREADY authenticated to their "
    "accounts (Supabase, GitHub, Vercel, bank, etc.). Never attempt to sign in, enter "
    "credentials, or ask the user to log in. If the task text says 'log in' / 'sign in', "
    "IGNORE that wording — it just means 'open the page'. Navigate DIRECTLY to the target "
    "destination (e.g. the dashboard/project URL like https://supabase.com/dashboard/projects), "
    "not the marketing homepage. Only if you navigate to the real destination and are then "
    "redirected to a genuine login wall should you report 'LOGIN REQUIRED' — and even then, "
    "try re-navigating once first (the session cookie can take a moment to attach).\n"
)


_CODE_AGENT_VERIFY_PROTOCOL = (
    "\n### PROTOCOL\n"
    "1. HTML/JS artifacts: call build_html_file directly (no sandbox). Never write download links in text.\n"
    "2. Python: test with run_sandboxed_python; confirm no traceback before completing.\n"
    "3. Syntax errors (CODE_VERIFY_FAILED): read the gate error, fix the exact tag/syntax, and retry.\n"
    "4. Editing existing artifact: call read_artifact first to inspect current source before modifying.\n"
)


def _effective_subagent_model_override(agent_name: str) -> Optional[Dict[str, Any]]:
    from src.core.orchestration.agents.factory import get_agent_model_override
    return get_agent_model_override(agent_name)


async def _swap_for_subagent(agent_name: str):
    """
    If this subagent is pinned to a different local model than the supervisor's main
    model (e.g. code_agent -> qwen3.5-9b while the main model is a 26B+), the supervisor
    sits fully idle for the subagent's whole run anyway — so free the main model's VRAM
    before the subagent loads, then hand it back when the subagent is done. Coding tasks
    in particular run long enough that the swap latency is comfortably worth it; quick
    subagent calls may not be (see model_override comment in agents/factory.py).

    No-ops when: the feature is disabled, the main model isn't local, or this agent has
    no model_override (i.e. it already shares the main model — nothing to swap).

    Also claims the subagent's own model resident via the shared coordinator (see below),
    so the VRAM budget can evict unwanted resident models before this one loads.

    Returns (main_base_url, main_model, sub_base_url, sub_model, agent_name) to hand back
    to `_swap_back`, or None if nothing was swapped (so `_swap_back` knows to skip too).
    """
    from src.shared.config_manager import ConfigManager

    if os.getenv("CHAD_TESTING") == "true":
        return None
    if not ConfigManager.get("subagents.model_swap_enabled", True):
        return None
    if not ConfigManager.is_local_model_active():
        return None

    override = _effective_subagent_model_override(agent_name)
    if not override:
        return None

    main_cfg = ConfigManager.get_local_model_config()
    main_model = main_cfg.get("name")
    main_base_url = main_cfg.get("base_url")
    if not main_model or not main_base_url:
        return None

    sub_model = override["model_name"]
    if main_model.split(":")[0] == sub_model.split(":")[0]:
        return None  # same weights — nothing to evict

    sub_provider_cfg = ConfigManager.get_local_provider_config(override.get("provider", "lmstudio")) or {}
    sub_base_url = sub_provider_cfg.get("base_url", main_base_url)
    if sub_base_url != main_base_url:
        return None  # different servers — no shared VRAM to free

    try:
        await release(main_base_url, main_model, owner=OWNER_MAIN_MODEL)
    except Exception as e:
        logger.debug(f"[ MODEL_SWAP ] release({main_model}) failed (non-fatal): {e}")

    # Claim the subagent's own model through the SAME coordinator (not a blind LM Studio
    # JIT-load) so `_make_room`'s VRAM budget gets a chance to evict whatever else is
    # unwanted/resident before this (often large) specialist model loads. Previously this
    # just let JIT absorb the load uncoordinated — if a leftover aux model (or a previous
    # subagent's model) was still resident, it stacked instead of being evicted, letting
    # multiple heavy models pile up resident at once and blow the VRAM budget.
    try:
        await ensure_resident(sub_base_url, sub_model, owner=agent_name, priority=current_priority())
    except Exception as e:  # noqa: BLE001 — best-effort; JIT still covers us on failure
        logger.debug(f"[ MODEL_SWAP ] {agent_name} preload of '{sub_model}' failed (non-fatal): {e}")

    return main_base_url, main_model, sub_base_url, sub_model, agent_name


async def _swap_back(swapped: Optional[tuple]):
    """Hand VRAM back to the main model once the subagent is done (success or failure)."""
    if not swapped:
        return
    main_base_url, main_model, sub_base_url, sub_model, agent_name = swapped
    # Drop this agent's claim on its own model so it becomes evictable (unwanted) again once
    # idle, instead of permanently pinning it resident alongside the reclaimed main model.
    try:
        await release(sub_base_url, sub_model, owner=agent_name)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ MODEL_SWAP ] release('{sub_model}', owner={agent_name}) failed (non-fatal): {e}")
    logger.info(f"[ MODEL_SWAP ] Reclaiming main model '{main_model}' for the supervisor.")
    # Await reload so the supervisor's main model is fully resident before supervisor_node
    # issues its next completion request. Fire-and-forget caused race conditions where LM Studio
    # threw 'Failed to load model' when concurrent inference and background loading collided.
    try:
        await ensure_resident(main_base_url, main_model, owner=OWNER_MAIN_MODEL, priority=current_priority())
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ MODEL_SWAP ] Reclaiming main model '{main_model}' failed (non-fatal): {e}")


from langchain_core.tools import StructuredTool
from pathlib import Path
import asyncio


# ---------------------------------------------------------------------------
# Parallel research fan-out (issue #508) — orchestrator/worker map-reduce
# ---------------------------------------------------------------------------
# Multi-item deep research (the "10 caterers" wedding task) was serial and swap-bound: the
# supervisor (gemma-26B) re-delegated one item at a time, so each item paid a full 26B<->9B
# LM Studio swap and researched sequentially (~84 min for 10 items vs <5 min without plan
# mode). `parallel_research` collapses that to ~2 swaps total: gemma is released ONCE, N 9B
# workers research their own item CONCURRENTLY (LM Studio serves the concurrent requests off
# one resident 9B — the coordinator's `active` counter keeps it from being evicted mid-batch),
# then gemma is reloaded ONCE to synthesize. Locally verified: 8×9B in parallel is very fast.
# Guardrails (copied from Anthropic's multi-agent research post, our exact failure modes):
# cap N to task size, one precise self-contained brief per worker, workers return CURATED
# summaries (never raw dumps — that is the same context-rot that caused hour-long loops), and
# a per-worker completion criterion + timeout so a single item can't loop forever.

def _parallel_research_worker_system(remaining_budget: Optional[int] = None) -> str:
    budget_line = (
        f"- SHARED SEARCH BUDGET: {remaining_budget} results remain across the whole research "
        "task (all workers draw from the same pool). Ration your queries — if it's running low, "
        "wrap up with what you have instead of searching further.\n"
        if remaining_budget is not None
        else ""
    )
    return (
        "ROLE: You are ONE worker in a parallel research team. You are responsible for EXACTLY ONE "
        "assigned item — ignore every other item; another worker has each of the others.\n"
        "- Research ONLY the item described below.\n"
        "- Batch your web_search sub-queries into as few calls as possible; never one query per turn. "
        "Budget AT MOST 3 web_search calls total for your item — a simple lookup needs 1.\n"
        + budget_line +
        "- Return a COMPACT, curated summary: ONLY the exact fields requested, each with its source URL. "
        "Target under ~180 words. Do NOT paste raw search results — the orchestrator synthesizes many "
        "of these and will overflow on dumps.\n"
        "- COMPLETION: stop as soon as you have the requested fields. If a field genuinely cannot be "
        "found after a broad search, write 'not found' for that field and stop — do NOT keep searching "
        "for a source that may not exist. If web_search reports the search budget is exhausted, stop "
        "immediately and return your best answer with whatever you already have.\n"
    )

# Breadth-discovery worker (#510): a SEPARATE role from the depth worker above. Fires BEFORE
# parallel_research when the caller doesn't already know the concrete item names — e.g. "10
# wedding caterers near Nashville" needs a real list of businesses before any per-item deep
# research can even be briefed. One worker, a couple of broad searches, names only — no fields,
# no depth, no synthesis. See create_research_discover_tool.
def _research_discover_worker_system(remaining_budget: Optional[int] = None) -> str:
    budget_line = (
        f"- SHARED SEARCH BUDGET: {remaining_budget} results remain for this whole research task "
        "(discovery + the per-item research that follows share the same pool) — keep this phase "
        "to a couple of broad searches so depth research has budget left.\n"
        if remaining_budget is not None
        else ""
    )
    return (
        "ROLE: You are a fast discovery scout. Your ONLY job is to find and LIST real items matching "
        "the request below — NOT to research any details about them yet; that happens later.\n"
        "- Use a couple of broad web_search calls (2-3 at most) to find real, named items (businesses, "
        "products, papers, etc.) matching the criteria.\n"
        + budget_line +
        "- Return ONLY a plain list of names, one per line — no prices, no descriptions, no analysis, "
        "no source URLs. Just the names.\n"
        "- If the request asks for N items, try to find at least N. If you can't after a couple of "
        "broad searches, return what you found and note the shortfall in one line — do NOT keep "
        "searching indefinitely for the last one or two.\n"
        "- Do NOT deep-dive into any single item — a later phase handles that.\n"
    )


def _parallel_research_cap() -> int:
    """Max workers per concurrent batch. Caps VRAM/compute and stops the orchestrator from
    spawning 50 workers for a trivial ask (Anthropic's observed failure). Extra items run in
    sequential batches of this size. See subagents.parallel_research_max_workers in
    config.yaml — the production-vs-isolated-probe concurrency discrepancy is still under
    investigation as of #510."""
    from src.shared.config_manager import ConfigManager
    try:
        return max(1, int(ConfigManager.get("subagents.parallel_research_max_workers", 6) or 6))
    except (TypeError, ValueError):
        return 6


def _parallel_worker_timeout() -> float:
    """Per-worker wall-clock cap so one stuck item can't stall the whole batch."""
    from src.shared.config_manager import ConfigManager
    try:
        return float(ConfigManager.get("subagents.parallel_research_worker_timeout_s", 300) or 300)
    except (TypeError, ValueError):
        return 300.0


def _search_budget_per_task() -> int:
    """Shared web_search result budget for one whole deep-research task (issue #524).
    See subagents.search_budget_per_task in config.yaml."""
    from src.shared.config_manager import ConfigManager
    try:
        return max(0, int(ConfigManager.get("subagents.search_budget_per_task", 50) or 50))
    except (TypeError, ValueError):
        return 50


def _research_worker_model() -> Optional[tuple]:
    """(base_url, model_id) of the model research workers run on, honoring a user override.
    None if research_agent shares the main model (nothing to pre-load/swap)."""
    from src.shared.config_manager import ConfigManager
    override = _effective_subagent_model_override("research_agent")
    if not override:
        return None
    provider_cfg = ConfigManager.get_local_provider_config(override.get("provider", "lmstudio")) or {}
    base_url = provider_cfg.get("base_url", "http://localhost:1234/v1")
    model_id = override.get("model_name")
    if not model_id or not base_url:
        return None
    return base_url, model_id


async def _run_research_worker(
    research_graph: Any, brief: str, index: int, base_config: RunnableConfig, recursion_limit: int
) -> str:
    """Run one research worker on its own namespaced thread and return its curated summary.

    Each worker gets a UNIQUE thread_id (``..._research_worker_<index>``) so concurrent
    workers never collide on the shared checkpointer. asyncio.gather runs each of these in
    its own Task, so the ContextVar set here is task-local and workers don't clobber each
    other's agent-name attribution.
    """
    from src.core.orchestration.context import agent_name_var, search_budget_var

    parent_id = base_config.get("configurable", {}).get("thread_id", "default")
    sub_config = dict(base_config)
    sub_config["recursion_limit"] = recursion_limit
    sub_config["configurable"] = {
        **base_config.get("configurable", {}),
        "thread_id": f"{parent_id}_research_worker_{index}",
    }

    budget = search_budget_var.get()
    messages = [
        SystemMessage(content=_parallel_research_worker_system(
            budget.remaining if budget is not None else None
        )),
        HumanMessage(content=brief),
    ]

    t_agent = agent_name_var.set(f"Research Worker {index + 1}")
    try:
        result = await asyncio.wait_for(
            research_graph.ainvoke({"messages": messages}, config=sub_config),
            timeout=_parallel_worker_timeout(),
        )
        return _clean_subagent_response(result.get("messages", []))
    except asyncio.TimeoutError:
        logger.warning(f"[ PARALLEL_RESEARCH ] worker {index + 1} timed out.")
        return f"(worker {index + 1} timed out before returning results — treat this item as incomplete)"
    except Exception as e:  # noqa: BLE001 — a single worker's crash must not sink the batch
        logger.error(f"[ PARALLEL_RESEARCH ] worker {index + 1} failed: {e}", exc_info=True)
        return f"(worker {index + 1} failed: {e})"
    finally:
        agent_name_var.reset(t_agent)


def create_research_discover_tool(research_graph: Any) -> Any:
    """Build the `research_discover` tool: ONE research_agent worker doing a fast, breadth-only
    discovery pass (find and list real item names) — the step BEFORE `parallel_research`'s depth
    fan-out, for when the caller doesn't already know the concrete items to research (#510).

    Deliberately a single call, not a fan-out: discovery needs one coherent list built from a
    couple of broad searches, not N independent per-item lookups. Bound ONLY to
    deep_research_agent (see supervisor.py) — never directly to the main supervisor, which
    should delegate the whole research task rather than touch discovery/depth tools itself.
    """

    async def research_discover_fn(task: str, config: RunnableConfig) -> str:
        task = (task or "").strip()
        if not task:
            return "ERROR: research_discover needs a `task` describing what to find and list."

        from src.shared.config_manager import ConfigManager
        default_limit = (
            ConfigManager.get("recursion_limit")
            or ConfigManager.get("orchestration.recursion_limit")
            or 100
        )

        # Same ~1-swap model lifecycle as parallel_research's own worker preload — see there for
        # why (avoids each call racing its own JIT load / duplicate-instance bug).
        swapped = await _swap_for_subagent("research_agent")
        worker_model = _research_worker_model()
        if worker_model:
            try:
                await ensure_resident(
                    worker_model[0], worker_model[1],
                    owner="research_discover", priority=current_priority(),
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ RESEARCH_DISCOVER ] worker-model preload failed (non-fatal): {e}")

        try:
            parent_id = config.get("configurable", {}).get("thread_id", "default")
            sub_config = dict(config)
            sub_config["recursion_limit"] = default_limit
            sub_config["configurable"] = {
                **config.get("configurable", {}),
                "thread_id": f"{parent_id}_research_discover",
            }
            from src.core.orchestration.context import agent_name_var, search_budget_var
            budget = search_budget_var.get()
            messages = [
                SystemMessage(content=_research_discover_worker_system(
                    budget.remaining if budget is not None else None
                )),
                HumanMessage(content=task),
            ]
            t_agent = agent_name_var.set("Research Discover")
            try:
                result = await asyncio.wait_for(
                    research_graph.ainvoke({"messages": messages}, config=sub_config),
                    timeout=_parallel_worker_timeout(),
                )
                return _clean_subagent_response(result.get("messages", []))
            except asyncio.TimeoutError:
                return "(research_discover timed out before returning a list — try a narrower request)"
            except Exception as e:  # noqa: BLE001
                logger.error(f"[ RESEARCH_DISCOVER ] failed: {e}", exc_info=True)
                return f"(research_discover failed: {e})"
            finally:
                agent_name_var.reset(t_agent)
        finally:
            if worker_model:
                try:
                    await release(worker_model[0], worker_model[1], owner="research_discover")
                except Exception:  # noqa: BLE001
                    pass
            await _swap_back(swapped)

    def research_discover_sync(task: str, config: RunnableConfig) -> str:
        raise NotImplementedError("Use ainvoke/arun for research_discover.")

    description = (
        "Fast discovery pass: find and LIST real items (businesses, products, papers, etc.) "
        "matching your criteria — NOT deep research on any of them yet. Use this FIRST whenever "
        "you don't already know the concrete names of what you need (e.g. 'find 10 wedding "
        "caterers near Nashville, TN'). Returns just a list of names. Once you have the list, "
        "write one brief per item and call `parallel_research` for the actual depth research. "
        "`task` should state exactly what to find and how many, e.g. 'List 10 real wedding "
        "catering companies that serve Nashville, TN. Return just their names, one per line.'"
    )

    return StructuredTool.from_function(
        func=research_discover_sync,
        coroutine=research_discover_fn,
        name="research_discover",
        description=description,
    )


def create_parallel_research_tool(research_graph: Any) -> Any:
    """Build the `parallel_research` tool that fans one brief-per-item out to concurrent 9B
    workers and returns a single combined summary for the supervisor to synthesize.

    Bound ONLY to deep_research_agent (#510) — the main supervisor delegates the whole research
    task via `delegate_to_deep_research_agent` rather than touching this tool directly, so its
    own thread doesn't accumulate dozens of raw web_search/worker-summary tool results.
    """

    async def parallel_research_fn(research_briefs: List[str], config: RunnableConfig) -> str:
        briefs = [b.strip() for b in (research_briefs or []) if b and b.strip()]
        if not briefs:
            return ("ERROR: parallel_research needs a non-empty `research_briefs` list — one "
                    "self-contained brief per item. Research is a SINGLE path: even for exactly "
                    "one item, call parallel_research with a one-element research_briefs list "
                    "(there is no separate single-item delegate tool).")

        from src.shared.config_manager import ConfigManager
        default_limit = (
            ConfigManager.get("recursion_limit")
            or ConfigManager.get("orchestration.recursion_limit")
            or 100
        )
        cap = _parallel_research_cap()
        total = len(briefs)
        logger.info(f"[ PARALLEL_RESEARCH ] Fanning out {total} briefs (<= {cap} concurrent/batch).")

        # ~2-swap model lifecycle: free gemma's VRAM ONCE for the whole batch, and pre-load the
        # 9B worker model so N concurrent workers don't each race a JIT load (LM Studio's
        # duplicate-instance-on-concurrent-JIT bug). Both are reverted in `finally`.
        swapped = await _swap_for_subagent("research_agent")
        worker_model = _research_worker_model()
        if worker_model:
            try:
                await ensure_resident(
                    worker_model[0], worker_model[1],
                    owner="parallel_research", priority=current_priority(),
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[ PARALLEL_RESEARCH ] worker-model preload failed (non-fatal): {e}")

        results: List[str] = []
        try:
            for start in range(0, total, cap):
                batch = briefs[start:start + cap]
                tasks = [
                    _run_research_worker(research_graph, b, start + i, config, default_limit)
                    for i, b in enumerate(batch)
                ]
                results.extend(await asyncio.gather(*tasks))
        finally:
            if worker_model:
                try:
                    await release(worker_model[0], worker_model[1], owner="parallel_research")
                except Exception:  # noqa: BLE001
                    pass
            await _swap_back(swapped)

        parts = [f"## Parallel research results ({total} item(s))\n"]
        for i, (brief, summary) in enumerate(zip(briefs, results), 1):
            head = brief.splitlines()[0][:90] if brief else f"item {i}"
            parts.append(f"### Item {i}: {head}\n{summary}\n")
        parts.append(
            "\nSYNTHESIZE the above into the requested deliverable. Do NOT re-research items "
            "already covered here."
        )
        return "\n".join(parts)

    def parallel_research_sync(research_briefs: List[str], config: RunnableConfig) -> str:
        raise NotImplementedError("Use ainvoke/arun for parallel_research.")

    description = (
        "THE research tool — use this for ANY web research task, whether it's ONE item or MANY. "
        "For a single item, pass a research_briefs list with exactly one brief. For several "
        "independent things to look up (e.g. '10 caterers', '5 products', '8 competitors'), pass "
        "one brief per item — they research concurrently on a team of fast workers, which is "
        "dramatically faster than looking them up one at a time. "
        "`research_briefs` is a list where EACH string is a COMPLETE, self-contained brief for ONE "
        "item: include that item's name, exactly which fields to find, and the format to return. "
        "Each brief is researched concurrently by its own worker; you receive one combined, curated "
        "summary to synthesize into the final deliverable. "
        "Example research_briefs: ["
        "'Research \"Mission BBQ\" catering near Huntsville AL: find per-plate price, distance from "
        "The Ledges venue, and whether they cater off-site weddings. Return each with a source URL.', "
        "'Research \"Dreamland BBQ\" catering near Huntsville AL: (same three fields, each with a "
        "source URL).']"
    )

    return StructuredTool.from_function(
        func=parallel_research_sync,
        coroutine=parallel_research_fn,
        name="parallel_research",
        description=description,
    )


def _content_text(content: Any) -> str:
    """Flatten a message's content to plain text, dropping tool_use/non-text blocks.

    Local-model adapters often return AIMessage.content as a list of blocks
    (e.g. [{"type":"text",...},{"type":"tool_use",...}]). Returning that verbatim
    leaks the tool call back to the supervisor as context bloat — keep only text.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
            # tool_use / thinking / image blocks are intentionally skipped
        return "\n".join(parts).strip()
    return str(content).strip() if content else ""


def _clean_subagent_response(messages: List[BaseMessage]) -> str:
    # Preserve verified content handles even if the specialist's final answer
    # omits them or paraphrases its observations. Never forward the stored body.
    from src.shared.captured_content import load_captured_text
    # Checkpointed specialists retain earlier delegations. Their old captures
    # must not become deliverables for a new (possibly unsuccessful) task.
    start = next((index for index in range(len(messages) - 1, -1, -1)
                  if isinstance(messages[index], HumanMessage)), 0)
    messages = messages[start:]
    refs = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == 'browser_capture_text':
            try:
                payload = json.loads(_content_text(message.content).removeprefix('[TOOL_DATA]\n'))
                if payload.get('success') is True:
                    record = load_captured_text(payload['content_id'])
                    refs[record['content_id']] = {key: record[key] for key in (
                        'content_id', 'char_count', 'sha256', 'source_url')}
            except (ValueError, KeyError, TypeError):
                continue
    text = _subagent_response_text(messages)
    if refs:
        text += '\n\nVERBATIM CONTENT REFERENCES (pass content_id unchanged to the destination tool):\n'
        text += json.dumps(list(refs.values()), ensure_ascii=False)
    return text


def _subagent_response_text(messages: List[BaseMessage]) -> str:
    """Return the specialist's natural-language answer — never a tool call.

    Picks the last AIMessage that carries real text. A trailing message with only
    tool_calls (empty text) is skipped so the supervisor gets the summary, not a
    dangling tool invocation. If all AIMessages carried only tool calls and no final
    text, synthesizes a concise status of the actions taken.
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _content_text(msg.content)
            if text:
                return text

    # If the subagent executed tools but never emitted final conversational text,
    # summarize the last tool actions so the supervisor knows what occurred.
    executed_tools: List[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                if name:
                    executed_tools.append(name)
        elif isinstance(msg, ToolMessage) and getattr(msg, "name", None):
            executed_tools.append(msg.name)

    if executed_tools:
        unique_tail = executed_tools[-3:]
        # Try to extract the last tool's text result for additional context
        last_tool_summary = ""
        for msg in reversed(messages):
            if isinstance(msg, ToolMessage) and msg.content:
                tool_text = _content_text(msg.content)
                if tool_text and not tool_text.startswith("[TOOL_DATA]"):
                    last_tool_summary = f" Latest page observation: {tool_text[:180]}..."
                break
        return (
            f"Specialist completed actions ({', '.join(unique_tail)}).{last_tool_summary}"
        )

    # Fallback: last message's text, or a clear placeholder.
    if messages:
        return _content_text(messages[-1].content) or "(specialist returned no textual result)"
    return "(specialist returned no messages)"


def _desktop_evidence(messages: List[BaseMessage], task: str) -> str:
    """Bounded tool receipts let the parent assess a UI report against actual targets.

    Only inspect this invocation, not older checkpointed specialist work. Return
    structured fields, never screenshot pixels or arbitrary long tool prose.
    """
    start = next((i + 1 for i in range(len(messages) - 1, -1, -1)
                  if isinstance(messages[i], HumanMessage) and messages[i].content == task), None)
    if start is None:
        return ""
    receipts = []
    for msg in messages[start:]:
        if not isinstance(msg, ToolMessage) or msg.name not in {
            "ui_click", "ui_find", "ui_read", "window_management", "screenshot_control", "keyboard_control",
        }:
            continue
        try:
            payload = json.loads(str(msg.content).removeprefix("[TOOL_DATA]\n"))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        receipt = {"tool": msg.name, "call_id": msg.tool_call_id, "success": payload.get("success")}
        parts = payload.get("result")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                try:
                    data = json.loads(part.get("text", ""))
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                for key in ("action", "errorType", "errorCode"):
                    if key in data:
                        receipt[key] = data[key]
                if data.get("success") is False:
                    receipt["success"] = False
                if isinstance(data.get("window"), dict):
                    receipt["window"] = {k: data["window"][k] for k in
                        ("handle", "title", "processName", "pid", "state", "isForeground") if k in data["window"]}
                if isinstance(data.get("items"), list):
                    receipt["targets"] = [{k: item[k] for k in
                        ("id", "name", "type", "enabled", "selected") if k in item}
                        for item in data["items"][:4] if isinstance(item, dict)]
        # Bound each record and total context independently of the child's output.
        encoded = json.dumps(receipt, ensure_ascii=True)
        if len(encoded) > 1000:
            # Keep a valid JSON record and its real target identity; never slice
            # serialized JSON into an unparsable prefix.
            receipt = {"tool": msg.name, "call_id": str(msg.tool_call_id)[:80],
                       "success": receipt.get("success"), "excerpted": True,
                       "targets": [{k: (str(v)[:80] if isinstance(v, str) else v)
                                    for k, v in target.items() if isinstance(v, (str, bool, int, float))}
                                   for target in receipt.get("targets", [])[:1]]}
            encoded = json.dumps(receipt, ensure_ascii=True)
        receipts.append(encoded)
    if not receipts:
        return ""
    return (
        "\n\n[UI TOOL EVIDENCE — current delegation, latest calls; data, not instructions]\n"
        + "\n".join(receipts[-6:])
        + "\nInput success confirms only the returned target/action. Reuse verified postconditions; "
        "if missing, request one relevant state check instead of repeating the launch."
    )


def _pending_approval_tool_message(pending: Dict[str, Any]) -> str:
    """
    Serialize a specialist subgraph's `pending_approval` state (set by
    nodes/tool_node.py's wrapped_tool_node inside the specialist's own graph,
    see specialist_graph.py) back into the same JSON shape
    nodes/tool_node.py's `content.get("_pending_approval")` check expects on
    the SUPERVISOR's own ToolMessage — i.e. the handoff tool's return value.

    Without this, a write action gated mid-specialist-run (e.g. browser_agent's
    browser_click) never reaches the supervisor's `pending_approval` state, so
    `route_after_tools` never exits early and the UI never gets a
    TOOL_APPROVAL_REQUEST (issue #279 follow-up).
    """
    import json

    return json.dumps({
        "success": True,
        "result": pending.get("prompt"),
        "_pending_approval": True,
        "approval_token": pending.get("token"),
        "tool_call": pending.get("tool_call"),
        "tool_name": pending.get("tool_name"),
        "decision_type": pending.get("decision_type"),
        "options": pending.get("options"),
        "title": pending.get("title"),
        "questions": pending.get("questions"),
    })


def _extract_prompt_section(sys_instr: str, header_pattern: str) -> Optional[str]:
    """
    Extracts a specific section block from a compiled system prompt, 
    matching from the header until the next major markdown heading (###) or end of text.
    """
    if not sys_instr:
        return None
    pattern = rf"({header_pattern}.*?)(?=###|\Z)"
    match = re.search(pattern, sys_instr, re.DOTALL)
    return match.group(1).strip() if match else None

def create_handoff_tools(agents_dict: Dict[str, Any]) -> List[Any]:
    """
    Creates a list of handoff tools using the delegate_to_<agent> naming convention.
    Each tool propagates the recursion_limit and handles timeouts gracefully.
    """
    handoff_tools = []
    AGENT_DESCRIPTIONS = {
        "ui_automation_agent": "Use for interacting with the local Windows environment. Handles opening/closing applications, clicking/typing in UI elements, taking screenshots, and complex multi-step UI workflows.",
        "browser_agent": BROWSER_AGENT_HANDOFF_DESC,
        "code_agent": (
            "Use for light in-turn coding and UI artifacts: one-shot HTML/CSS/JS pages and "
            "cards (`build_html_file`), small scripts, sandbox runs. Embeds real data gathered "
            "from earlier steps into code/artifacts. After load_skill, delegate here — the specialist "
            "receives the full skill SOP. Deep / multi-file coding work belongs to Chad Code "
            "(`dispatch_coding_task`), not this agent. NEVER delegate Canvas LMS tasks or reading "
            "Canvas assignment files here; Canvas files are read directly using `read_course_file` on the supervisor."
        ),
        "system_agent": "Use for low-level OS tasks. Handles local file system operations (move, copy, delete), checking system stats (CPU/RAM), and managing OS settings.",
        "traversal_agent": (
            "Use for open-ended Google Drive or local filesystem explore / search-through "
            "(unknown location, hop-by-hop browse, multi-hop gather). Not for Canvas LMS, "
            "not for a known Drive file_id, known local path, or a single quick named search — "
            "those use google_drive_* / fuzzy_find_local / list_directory / read_file / find_project "
            "or Canvas tools on the supervisor. The `task` parameter must state the complete retrieval goal "
            "in one shot — never delegate only 'navigate to folder'."
        ),
        # research_agent itself is never a direct delegate (see the skip below) — this key is
        # unused now but kept so a stray lookup doesn't KeyError.
        "research_agent": RESEARCH_AGENT_HANDOFF_DESC,
        # deep_research_agent (#510) owns the WHOLE research workflow — breadth discovery via
        # research_discover, depth via parallel_research, synthesis — internally. The main
        # supervisor delegates the full research goal here ONCE instead of touching
        # research_discover/parallel_research/web_search itself, so its own thread stays a
        # single delegate-in/report-out instead of accumulating dozens of raw tool calls.
        "deep_research_agent": (
            "THE research specialist — delegate ANY web research task here, whether it's ONE "
            "item or MANY (e.g. '10 caterers', 'compare 3 vendors', 'find the founding year of "
            "X'). It owns the whole research workflow internally: discovering real item names "
            "when you don't already have them, researching each in parallel, and synthesizing "
            "one report. You get back ONE clean answer, not a stream of raw search results. "
            "Never call web_search or parallel_research yourself for research tasks — delegate "
            "here instead."
        ),
        "canvas_agent": (
            "Use for Canvas LMS coursework tasks: finding and downloading lecture slides/files, "
            "exploring course modules, reading syllabus, checking upcoming assignments, due dates, "
            "grades, and quizzes. Delegate any multi-step Canvas exploration or course file retrieval "
            "here. The specialist returns concise structured summaries and local downloaded file paths."
        ),
    }

    TRAVERSAL_TASK_DOC = (
        "End-to-end retrieval goal for the traversal specialist: what to find, open, read, "
        "and return (include file contents and any user-specific section). "
        "Do NOT pass only the current plan micro-step (e.g. 'navigate to folder')."
    )
    RESEARCH_TASK_DOC = (
        "Full research goal for the research specialist: what to find, how many items (if "
        "several), and the format/fields to return. State the goal end-to-end — deep_research_agent "
        "handles discovery-if-needed, parallel depth research, and synthesis internally; you do "
        "not need to enumerate item names yourself or split this into multiple delegations."
    )
    CANVAS_TASK_DOC = (
        "End-to-end Canvas task description: course name, what lectures/items to find, download, "
        "or inspect, and destination location. State the goal completely so the specialist can run "
        "the full discovery and download loop autonomously."
    )
    CODE_TASK_DOC = (
        "Complete coding specification for the specialist: target filename, layout, requirements, "
        "and concrete data/fields to include. The specialist executes in clean isolation — pass any "
        "necessary data directly in this task prompt."
    )

    for agent_name, agent in agents_dict.items():
        # research_agent is NOT exposed as a direct serial delegate. It exists only as the WORKER
        # that deep_research_agent's research_discover/parallel_research tools fan out to. Research
        # is a SINGLE path: the main supervisor delegates the whole task to deep_research_agent,
        # which decomposes it into research_agent worker calls. Exposing delegate_to_research_agent
        # directly gave models a tempting serial fallback (cramming all N items into one call) —
        # the exact redundancy #508 is meant to remove. Skip it here.
        if agent_name == "research_agent":
            continue

        def _create_tool(target_agent=agent, target_name=agent_name):
            # Clean name for tool string
            display_name = target_name.replace("_agent", "").replace("_", " ")
            detailed_desc = AGENT_DESCRIPTIONS.get(target_name, f"Delegate a task to the {display_name} agent. Use this for {display_name} related requests.")
            task_doc = TRAVERSAL_TASK_DOC if target_name == "traversal_agent" else (
                RESEARCH_TASK_DOC if target_name == "deep_research_agent" else (
                    CANVAS_TASK_DOC if target_name == "canvas_agent" else (
                        CODE_TASK_DOC if target_name == "code_agent" else (
                            "Clear, standalone task description for the specialist."
                        )
                    )
                )
            )
            
            async def handoff_fn(task: str, config: RunnableConfig) -> str:
                shared_handoff = config.setdefault("configurable", {}).setdefault("handoff_state", {})
                if target_name == "browser_agent" and shared_handoff.get("browser_budget_exhausted"):
                    return _browser_budget_blocked()
                logger.info(f"[ HANDOFF ] Delegating to {target_name}: {task[:50]}...")
                
                # Context Engineering: Propagate skills
                # The sub-agent needs to know WHO it is and WHAT skills are active.
                
                loaded_skill = config.get("configurable", {}).get("loaded_skill")
                loaded_skill_name = config.get("configurable", {}).get("loaded_skill_name")
                
                # --- SKILL STAGING (Sandbox Prep) ---
                # If delegating to code_agent and a skill is active, upload its scripts to the sandbox.
                skill_scripts_msg = ""
                if target_name == "code_agent" and loaded_skill_name:
                    try:
                        from src.core.skill_system.skill_loader import get_skill_loader, PROJECT_ROOT
                        from src.tools.coding.sandbox import get_sandbox
                        
                        loader = get_skill_loader()
                        entry = loader.get_entry(loaded_skill_name)
                        
                        if entry:
                            # Skill dir is the parent of the SKILL.md file
                            skill_file_path = PROJECT_ROOT / entry.file_path
                            skill_dir = skill_file_path.parent
                            scripts_dir = skill_dir / "scripts"
                            
                            if scripts_dir.exists() and scripts_dir.is_dir():
                                logger.info(f"[ STAGING ] Preparing sandbox for skill '{loaded_skill_name}'...")
                                
                                # Get sandbox (sync call, should be fast if already exists)
                                sb = get_sandbox()
                                
                                # Upload all relevant scripts
                                uploaded_files = []
                                for script_path in scripts_dir.rglob("*"):
                                    if script_path.is_file() and script_path.suffix in ['.py', '.sh', '.js', '.vbs', '.bat', '.ps1']:
                                        # Use relative path inside sandbox
                                        rel_path = script_path.relative_to(scripts_dir)
                                        remote_path = f"/skills/{loaded_skill_name}/{rel_path}".replace("\\", "/")
                                        
                                        logger.debug(f"[ STAGING ] Uploading: {remote_path}")
                                        
                                        # Wrap blocking IO
                                        content = script_path.read_bytes()
                                        await asyncio.to_thread(sb.files.write, remote_path, content)
                                        uploaded_files.append(remote_path)
                                
                                if uploaded_files:
                                    skill_scripts_msg = f"\nTECHNICAL SCRIPTS STAGED IN SANDBOX:\n" + "\n".join([f"- {f}" for f in uploaded_files])
                                    logger.info(f"[ STAGING ] {len(uploaded_files)} scripts uploaded to sandbox.")
                                else:
                                    logger.debug(f"[ STAGING ] No scripts found in {scripts_dir}")
                    except Exception as e:
                        logger.error(f"[ STAGING ] Failed to stage skill scripts: {e}", exc_info=True)
                        skill_scripts_msg = f"\n⚠️ WARNING: Failed to stage technical scripts in sandbox. Error: {str(e)}"
                
                # 1. Build Specialist Identity
                system_content = f"ROLE: You are the {display_name} specialist. Perform the task and return a concise result to the supervisor.\n"
                if target_name == "browser_agent":
                    system_content += _BROWSER_AGENT_PREAUTH


                # 2. Inject Active Skill (SOP/Expertise)
                loaded_skill = config.get("configurable", {}).get("loaded_skill")
                loaded_skill_name = config.get("configurable", {}).get("loaded_skill_name")
                if loaded_skill and loaded_skill_name:
                    system_content += (
                        f"\n### 🟢 ACTIVE SKILL: {loaded_skill_name}\n"
                        f"You MUST follow the instructions in the skill SOP below exactly. "
                        f"This is your technical execution blueprint for this task.\n\n"
                        f"{loaded_skill}\n"
                    )
                    
                    if skill_scripts_msg:
                        system_content += skill_scripts_msg + "\n"
                    
                    # 3. Inject Runtime & Install instructions
                    loader = get_skill_loader()
                    entry = loader.get_entry(loaded_skill_name)
                    if entry:
                        if entry.runtime:
                            system_content += f"RUNTIME: This task uses `{entry.runtime}`.\n"
                        if entry.install and not entry.install.strip().lower().startswith("none"):
                            system_content += f"SETUP: You MUST run this command in the sandbox before executing code: `{entry.install}`\n"
                
                # Propagate config (recursion_limit, metadata, etc.)
                # We ensure recursion_limit is at least the configured default for sub-agents
                sub_config = config.copy()
                from src.shared.config_manager import ConfigManager
                default_limit = ConfigManager.get("recursion_limit") or ConfigManager.get("orchestration.recursion_limit") or 100
                from src.core.orchestration.agents.specialist_graph import get_specialist_max_steps
                default_limit = max(default_limit, 2 * get_specialist_max_steps(target_name) + 4)
                if "recursion_limit" not in sub_config or sub_config["recursion_limit"] < default_limit:
                    sub_config["recursion_limit"] = default_limit
                
                # NAMESPACING: Give the sub-agent a unique thread_id to avoid state collision 
                # with the supervisor while still allowing it to persist its own sub-history.
                parent_id = sub_config.get("configurable", {}).get("thread_id", "default")
                sub_config["configurable"] = {
                    **sub_config.get("configurable", {}),
                    "thread_id": f"{parent_id}_{target_name}"
                }

                # Get existing messages from sub-agent thread to avoid duplicating system prompt
                existing_messages = []
                existing_browser_progress = None
                try:
                    sub_state = await target_agent.aget_state(sub_config)
                    if sub_state and sub_state.values:
                        existing_messages = sub_state.values.get("messages", [])
                        existing_browser_progress = sub_state.values.get('browser_progress')
                except Exception as e:
                    logger.debug(f"[ HANDOFF ] Could not fetch subagent state: {e}")

                has_system_msg = any(isinstance(m, SystemMessage) for m in existing_messages)

                messages = []
                if not has_system_msg:
                    messages.append(SystemMessage(content=system_content))
                evidence_context = render_evidence(config.get("configurable", {}).get("scout_evidence"), task)
                messages.append(HumanMessage(content=task + evidence_context))
                sub_agent_input = {"messages": messages, "budget_exceeded": False, "specialist_steps": 0, "pending_approval": None}
                if target_name == 'browser_agent':
                    from src.core.orchestration.agents.browser_state import delegation_input
                    delegation_id = config.get('metadata', {}).get('tool_call_id') or parent_id
                    sub_agent_input.update(delegation_input(task, delegation_id, existing_browser_progress))
                    sub_agent_input['browser_task'].update(source_evidence=evidence_context,
                                                           specialist_guidance=system_content)
                
                # --- LOOP PREVENTION (counts FAILURES, not delegations) ---
                # See the matching block in create_subagent_adapter for why: bumping
                # on every call meant three SUCCESSFUL delegations locked the
                # specialist out with a "has failed 3 times" message.
                handoff_state = sub_config.get("configurable", {}).get("handoff_state", {})
                handoff_key = f"{parent_id}_{target_name}"
                current_count = handoff_state.get(handoff_key, 0)

                if current_count >= int(limits.get("delegate_failures_max")):
                    logger.error(f"[ HANDOFF ] delegate failure cap reached for {display_name}")
                    return failure.delegate_blocked(display_name, current_count)

                sub_config["configurable"]["handoff_state"] = handoff_state

                def _note_failure(text: str) -> str:
                    """Book a failure against the counter and warn before the ceiling."""
                    count = handoff_state.get(handoff_key, 0) + 1
                    handoff_state[handoff_key] = count
                    soft = int(limits.get("delegate_failures_soft"))
                    hard = int(limits.get("delegate_failures_max"))
                    if soft <= count < hard:
                        text += (
                            f"\n\nNOTE: {display_name} has now failed {count} time(s) for "
                            f"this task; after {hard} it will be refused. Change the brief "
                            f"or use another route."
                        )
                    return text

                def _note_success() -> None:
                    handoff_state.pop(handoff_key, None)
                
                # --- AGENT CONTEXT PROPAGATION ---
                from src.core.orchestration.context import agent_name_var, parent_correlation_id_var, search_budget_var
                t_agent = agent_name_var.set(display_name.title())

                # Extract the correlation ID of THIS delegation tool call
                curr_tool_call_id = config.get("metadata", {}).get("tool_call_id")
                t_parent = parent_correlation_id_var.set(curr_tool_call_id)

                # Shared task-scoped search budget (issue #524): one SearchBudget per
                # deep-research task, inherited by every research_discover/parallel_research
                # worker Task spawned during this ainvoke (asyncio Tasks copy the current
                # Context, including this var, at creation time — so the same instance is
                # shared and decremented across all concurrent workers).
                t_budget = None
                if target_name == "deep_research_agent":
                    from src.tools.web.web_search import SearchBudget
                    t_budget = search_budget_var.set(SearchBudget(_search_budget_per_task()))

                try:
                    # Sub-agents are CompiledGraphs, so we use ainvoke
                    result = await target_agent.ainvoke(sub_agent_input, config=sub_config)
                    if result.get("browser_budget_exhausted"):
                        shared_handoff["browser_budget_exhausted"] = True
                    
                    # A gated write action (e.g. browser_click classified as a write,
                    # issue #279) paused inside the specialist's own graph — forward the
                    # pending-approval payload as-is so the supervisor's own tool node
                    # picks it up and actually surfaces an ApprovalCard, instead of
                    # collapsing it into a natural-language summary.
                    pending = result.get("pending_approval")
                    if pending:
                        return _pending_approval_tool_message(pending)

                    # Extract the final response from the sub-agent's state
                    messages = result.get("messages", [])
                    if not messages:
                        return _note_failure(failure.delegate_empty(display_name))

                    # Return only the specialist's natural-language summary — never a
                    # trailing tool call / non-text block (needless supervisor context).
                    summary = _clean_subagent_response(messages)
                    if target_name == "ui_automation_agent":
                        summary += _desktop_evidence(messages, task)
                    summary = _budget_progress_report(result, target_name, summary)
                    if failure.failure_signature(summary):
                        return _note_failure(summary)
                    _note_success()
                    return summary

                except asyncio.TimeoutError:
                    logger.error(f"[ HANDOFF ] {display_name} timed out")
                    return _note_failure(failure.delegate_timeout(display_name))
                except Exception as e:
                    logger.error(f"[ HANDOFF ] ERROR in {target_name}: {e}")
                    if failure.is_infra_failure(e) or "no tools" in str(e).lower():
                        return _note_failure(failure.delegate_unavailable(display_name, e))
                    return _note_failure(failure.delegate_crashed(display_name, e))
                finally:
                    agent_name_var.reset(t_agent)
                    parent_correlation_id_var.reset(t_parent)
                    if t_budget is not None:
                        search_budget_var.reset(t_budget)

            def handoff_sync(task: str, config: RunnableConfig) -> str:
                """Synchronous wrapper for handoff (should not be called in async context)."""
                raise NotImplementedError("Use ainvoke/arun for handoff tools.")

            handoff_fn.__doc__ = (
                "Delegate a specific task to the specialized agent. "
                + task_doc
            )

            # Create StructuredTool with explicit name and description
            return StructuredTool.from_function(
                func=handoff_sync,
                coroutine=handoff_fn,
                name=f"delegate_to_{target_name}",
                description=detailed_desc
            )

        handoff_tools.append(_create_tool())

    return handoff_tools




def create_subagent_adapter(agent_name: str, agent_graph: Any) -> Callable:
    """
    Creates an adapter node that runs a specialist agent subgraph as an isolated child turn.
    Namespaces the thread_id, manages handoff retry limits, handles script staging for code_agent,
    and encapsulates all internal ReAct steps into a single clean ToolMessage return value.
    """
    display_name = agent_name.replace("_agent", "")

    async def subagent_node(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
        last_msg = state["messages"][-1]
        
        # 1. Identify target tool call
        target_tool_call = None
        for tc in last_msg.tool_calls:
            if tc["name"] == f"delegate_to_{agent_name}":
                target_tool_call = tc
                break
                
        if not target_tool_call:
            # Fallback: find first delegate tool call matching target display name
            for tc in last_msg.tool_calls:
                if tc["name"] == f"delegate_to_{display_name}":
                    target_tool_call = tc
                    break
                    
        if not target_tool_call:
            # Final fallback: first delegate tool call
            for tc in last_msg.tool_calls:
                if tc["name"].startswith("delegate_to_"):
                    target_tool_call = tc
                    break
                    
        if not target_tool_call:
            raise ValueError(f"Handoff target delegate_to_{display_name} not found in last message tool calls.")

        tool_call_id = target_tool_call["id"]
        raw_args = dict(target_tool_call.get("args") or {})
        task = raw_args.get("task") or raw_args.get("prompt") or raw_args.get("instructions") or ""
        if "task" not in raw_args and task:
            raw_args["task"] = task
        if "prompt" not in raw_args and task:
            raw_args["prompt"] = task

        # Announce the delegation to the UI. Without this, the frontend's ToolBubble for
        # "delegate_to_<agent>" has no ToolStarted/ToolCompleted pair to key off — it was
        # showing "Working..." forever regardless of outcome, because this handoff path
        # (unlike every atomic tool, see tool_executor.py) never published either event.
        event_bus = EventBus.get_instance()
        event_bus.publish(ToolStarted(
            tool_name=target_tool_call["name"],
            args=raw_args,
            correlation_id=tool_call_id,
            display_text=f"Delegating to {display_name.replace('_', ' ')} specialist",
        ))

        if agent_name == "browser_agent" and state.get("browser_budget_exhausted"):
            content = _browser_budget_blocked()
            event_bus.publish(ToolCompleted(
                tool_name=target_tool_call["name"], result=content, success=False,
                error=content, correlation_id=tool_call_id,
            ))
            return {"messages": [ToolMessage(
                content=content, tool_call_id=tool_call_id, name=target_tool_call["name"]
            )], "browser_budget_exhausted": True}

        loaded_skill = state.get("loaded_skill")
        loaded_skill_name = state.get("loaded_skill_name")

        # --- SKILL STAGING (Sandbox Prep) ---
        skill_scripts_msg = ""
        if agent_name == "code_agent" and loaded_skill_name:
            try:
                from src.core.skill_system.skill_loader import get_skill_loader, PROJECT_ROOT
                from src.tools.coding.sandbox import get_sandbox
                
                loader = get_skill_loader()
                entry = loader.get_entry(loaded_skill_name)
                
                if entry:
                    skill_file_path = PROJECT_ROOT / entry.file_path
                    skill_dir = skill_file_path.parent
                    scripts_dir = skill_dir / "scripts"
                    
                    if scripts_dir.exists() and scripts_dir.is_dir():
                        logger.info(f"[ STAGING ] Preparing sandbox for skill '{loaded_skill_name}'...")
                        sb = get_sandbox()
                        
                        uploaded_files = []
                        for script_path in scripts_dir.rglob("*"):
                            if script_path.is_file() and script_path.suffix in ['.py', '.sh', '.js', '.vbs', '.bat', '.ps1']:
                                rel_path = script_path.relative_to(scripts_dir)
                                remote_path = f"/skills/{loaded_skill_name}/{rel_path}".replace("\\", "/")
                                
                                logger.debug(f"[ STAGING ] Uploading: {remote_path}")
                                content = script_path.read_bytes()
                                await asyncio.to_thread(sb.files.write, remote_path, content)
                                uploaded_files.append(remote_path)
                        
                        if uploaded_files:
                            skill_scripts_msg = f"\nTECHNICAL SCRIPTS STAGED IN SANDBOX:\n" + "\n".join([f"- {f}" for f in uploaded_files])
                            logger.info(f"[ STAGING ] {len(uploaded_files)} scripts uploaded to sandbox.")
            except Exception as e:
                logger.error(f"[ STAGING ] Failed to stage skill scripts: {e}", exc_info=True)
                skill_scripts_msg = f"\n⚠️ WARNING: Failed to stage technical scripts in sandbox. Error: {str(e)}"

        # 2. Build Specialist System Instruction
        system_content = f"ROLE: You are the {display_name.replace('_', ' ')} specialist. Perform the task and return a concise result to the supervisor.\n"
        if agent_name == "code_agent":
            system_content += _CODE_AGENT_VERIFY_PROTOCOL
        if agent_name == "browser_agent":
            system_content += _BROWSER_AGENT_PREAUTH
        if loaded_skill and loaded_skill_name:
            system_content += (
                f"\n### 🟢 ACTIVE SKILL: {loaded_skill_name}\n"
                f"You MUST follow the instructions in the skill SOP below exactly. "
                f"This is your technical execution blueprint for this task.\n\n"
                f"{loaded_skill}\n"
            )
            if skill_scripts_msg:
                system_content += skill_scripts_msg + "\n"
            
            try:
                from src.core.skill_system.skill_loader import get_skill_loader
                loader = get_skill_loader()
                entry = loader.get_entry(loaded_skill_name)
                if entry:
                    if entry.runtime:
                        system_content += f"RUNTIME: This task uses `{entry.runtime}`.\n"
                    if entry.install and not entry.install.strip().lower().startswith("none"):
                        system_content += f"SETUP: You MUST run this command in the sandbox before executing code: `{entry.install}`\n"
            except Exception:
                pass

        # 3. Config Propagations
        sub_config = config.copy()
        from src.shared.config_manager import ConfigManager
        default_limit = ConfigManager.get("recursion_limit") or ConfigManager.get("orchestration.recursion_limit") or 100
        from src.core.orchestration.agents.specialist_graph import get_specialist_max_steps
        default_limit = max(default_limit, 2 * get_specialist_max_steps(agent_name) + 4)
        if "recursion_limit" not in sub_config or sub_config["recursion_limit"] < default_limit:
            sub_config["recursion_limit"] = default_limit

        parent_id = sub_config.get("configurable", {}).get("thread_id", "default")
        sub_config["configurable"] = {
            **sub_config.get("configurable", {}),
            "thread_id": f"{parent_id}_{agent_name}"
        }

        # Get existing messages from sub-agent thread to avoid duplicating system prompt
        existing_messages = []
        existing_browser_progress = None
        try:
            sub_state = await agent_graph.aget_state(sub_config)
            if sub_state and sub_state.values:
                existing_messages = sub_state.values.get("messages", [])
                existing_browser_progress = sub_state.values.get('browser_progress')
        except Exception as e:
            logger.debug(f"[ HANDOFF ] Could not fetch subagent state: {e}")

        has_system_msg = any(isinstance(m, SystemMessage) for m in existing_messages)

        messages = []
        if not has_system_msg:
            messages.append(SystemMessage(content=system_content))
        evidence_context = render_evidence(state.get("scout_evidence") or config.get("configurable", {}).get("scout_evidence"), task)
        messages.append(HumanMessage(content=task + evidence_context))
        sub_agent_input = {"messages": messages, "budget_exceeded": False, "specialist_steps": 0, "pending_approval": None}
        if agent_name == 'browser_agent':
            from src.core.orchestration.agents.browser_state import delegation_input
            sub_agent_input.update(delegation_input(task, tool_call_id, existing_browser_progress))
            sub_agent_input['browser_task'].update(source_evidence=evidence_context,
                                                   specialist_guidance=system_content)

        # 4. Loop prevention — counts FAILURES, not delegations.
        # It used to increment on every call, so a specialist that succeeded three
        # times was then refused with "has failed 3 times" — punishing the model for
        # work that went fine, and lying to it about why. The counter is now bumped
        # after a failure (below) and cleared on success.
        handoff_state = sub_config.get("configurable", {}).get("handoff_state", {})
        handoff_key = f"{parent_id}_{agent_name}"
        current_count = handoff_state.get(handoff_key, 0)

        if current_count >= int(limits.get("delegate_failures_max")):
            error_msg = failure.delegate_blocked(display_name, current_count)
            event_bus.publish(ToolCompleted(
                tool_name=target_tool_call["name"],
                result=error_msg,
                success=False,
                error=error_msg,
                correlation_id=tool_call_id,
            ))
            return {"messages": [ToolMessage(content=error_msg, tool_call_id=tool_call_id, name=target_tool_call["name"])]}

        sub_config["configurable"]["handoff_state"] = handoff_state

        # 5. Execute Isolated Subgraph
        from src.core.orchestration.context import agent_name_var, parent_correlation_id_var
        t_agent = agent_name_var.set(display_name.replace('_', ' ').title())
        t_parent = parent_correlation_id_var.set(tool_call_id)

        swapped = await _swap_for_subagent(agent_name)
        pending = None
        browser_budget_exhausted = False
        try:
            result = await agent_graph.ainvoke(sub_agent_input, config=sub_config)
            browser_budget_exhausted = bool(result.get("browser_budget_exhausted"))

            # A gated write action paused inside the specialist's own graph
            # (issue #279/#325) — forward the pending-approval payload as a
            # TOP-LEVEL state key, exactly like nodes/tool_node.py does for the
            # supervisor's own atomic tool calls. This node (subagent_node) is
            # wired straight to "supervisor" via workflow.add_edge — an
            # UNCONDITIONAL edge, not routed through route_after_tools — so
            # returning the pending payload only inside the ToolMessage content
            # (the previous #325 fix) was insufficient: nothing ever inspected
            # that content, the graph always looped back into the supervisor's
            # own model, and the model just narrated "waiting on your input"
            # instead of the turn actually pausing for a real ApprovalCard. See
            # supervisor.py's conditional edge for agent_name nodes.
            pending = result.get("pending_approval")
            if pending:
                content = _pending_approval_tool_message(pending)
            else:
                inner_msgs = result.get("messages", [])
                if not inner_msgs:
                    content = failure.delegate_empty(display_name)
                else:
                    # Return only the specialist's natural-language summary — strip any trailing
                    # tool calls / non-text blocks so we don't bloat the supervisor's context.
                    content = _clean_subagent_response(inner_msgs)
                    if agent_name == "ui_automation_agent":
                        content += _desktop_evidence(inner_msgs, task)
                    content = _budget_progress_report(result, agent_name, content)
                    if agent_name == "code_agent" and content:
                        from src.tools.filesystem.html_builder_tool import HtmlBuilderTool
                        clean_candidate = HtmlBuilderTool._strip_markdown_fences(content)
                        if ("<!doctype html" in clean_candidate.lower() or "<html" in clean_candidate.lower()) and "</html" in clean_candidate.lower():
                            already_saved = any(
                                isinstance(m, ToolMessage) and getattr(m, "name", "") == "build_html_file"
                                for m in inner_msgs
                            )
                            if not already_saved:
                                try:
                                    builder = HtmlBuilderTool()
                                    match_fname = re.search(r'([a-zA-Z0-9_\-]+\.html)', task)
                                    filename = match_fname.group(1) if match_fname else f"artifact_{uuid.uuid4().hex[:8]}.html"
                                    saved_res = await builder.execute(html_content=clean_candidate, filename=filename)
                                    if saved_res.get("success"):
                                        content = (
                                            f"Deliverable '{filename}' successfully generated and registered as an HTML artifact.\n"
                                            f"{saved_res.get('result', '')}\n"
                                            f"NOTE: In Chad, HTML deliverables are packaged directly into the artifacts registry (as '{filename}'). "
                                            f"This fulfills any plan requirement to save as '{filename}'. Do NOT search for remote/Linux paths like /home/user/ on this system."
                                        )
                                except Exception as auto_save_err:
                                    logger.debug(f"[ AUTO_ARTIFACT ] Auto-save of HTML from code_agent failed: {auto_save_err}")
        except asyncio.TimeoutError:
            logger.error(f"[ HANDOFF ] {display_name} timed out")
            content = failure.delegate_timeout(display_name)
        except Exception as e:
            # A crash inside the specialist subgraph (model error, tool exception,
            # etc.) must degrade to a ToolMessage the supervisor's model can react
            # to — not propagate up and abort the whole turn (#216).
            # BUT: an *infrastructure* failure (the subagent's model isn't loaded /
            # doesn't exist / server unreachable) will fail identically on every retry,
            # and the supervisor — seeing a generic "ERROR, try again" — re-delegates each
            # turn, producing the ~1h cross-turn swap loop we hit when code_agent pointed at a
            # non-existent model id. Detect that class and return a NON-RETRYABLE directive so
            # the supervisor abandons this delegate and takes another path (or fails the step).
            if failure.is_infra_failure(e):
                content = failure.delegate_unavailable(display_name, e)
            else:
                content = failure.delegate_crashed(display_name, e)
            logger.error(f"[ HANDOFF ] delegate {display_name} failed: {e}", exc_info=True)
        finally:
            await _swap_back(swapped)
            agent_name_var.reset(t_agent)
            parent_correlation_id_var.reset(t_parent)

        # Close out the ToolStarted above so the UI bubble resolves to done/error
        # instead of sitting on "Working..." indefinitely (see the matching publish
        # near the top of this function for why this pairing is required).
        # Every delegate failure is now a rendered failure envelope, so detection is
        # the shared signature check rather than a list of ad-hoc string prefixes.
        handoff_failed = failure.failure_signature(content) is not None

        # Book the outcome against the failure counter, and warn BEFORE the ceiling
        # so the model can change tack while it still has attempts left.
        if handoff_failed:
            current_count += 1
            handoff_state[handoff_key] = current_count
            soft = int(limits.get("delegate_failures_soft"))
            hard = int(limits.get("delegate_failures_max"))
            if soft <= current_count < hard:
                content += (
                    f"\n\nNOTE: {display_name.replace('_', ' ')} has now failed "
                    f"{current_count} time(s) for this task; after {hard} it will be "
                    f"refused. Change the brief or use another route."
                )
        else:
            handoff_state.pop(handoff_key, None)
        event_bus.publish(ToolCompleted(
            tool_name=target_tool_call["name"],
            result=content,
            success=not handoff_failed,
            error=content if handoff_failed else None,
            correlation_id=tool_call_id,
            awaiting_user=bool(pending),
        ))

        # 6. Package output as a clean ToolMessage
        output: Dict[str, Any] = {
            "pending_approval": None,
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call_id,
                    name=target_tool_call["name"]
                )
            ]
        }
        if pending:
            output["pending_approval"] = pending
            output["final_response"] = pending.get("title") or pending.get("prompt") or ""
        if browser_budget_exhausted:
            output["browser_budget_exhausted"] = True
        return output

    return subagent_node


# ---------------------------------------------------------------------------
# Parallel sub-agent fan-out (issue #513) — run 2+ independent delegate_to_* calls
# concurrently, gated by whether their target models can actually coexist in VRAM.
# ---------------------------------------------------------------------------
# routing.py sends here instead of a single agent_name node when the supervisor emits more
# than one delegate_to_* call in one turn. Reuses `subagent_node` per target UNCHANGED (each
# is a pure function of (state, config), re-entrant, no shared mutable state — see
# specialist_graph.py) rather than reimplementing handoff plumbing (skill staging, loop
# prevention, VRAM swap, event bus, pending_approval) for the concurrent path.

def _parallel_delegate_cap() -> int:
    """Max concurrent handoffs per batch — mirrors `_parallel_research_cap`."""
    from src.shared.config_manager import ConfigManager
    try:
        return max(1, int(ConfigManager.get("subagents.parallel_delegate_max_workers", 4) or 4))
    except (TypeError, ValueError):
        return 4


def _delegate_model_id(agent_name: str) -> str:
    """Resolve the model id a delegate target will run on, falling back to the main
    supervisor's active model for agents with no per-agent pin (e.g. system_agent)."""
    from src.core.orchestration.agents.factory import resolve_agent_model_id
    model_id = resolve_agent_model_id(agent_name)
    if model_id:
        return model_id
    from src.shared.config_manager import ConfigManager
    active = ConfigManager.get("local_models.active_model")
    if isinstance(active, dict):
        return active.get("name") or active.get("model_name") or "unknown"
    return str(active or "unknown")


def _partition_for_concurrency(agent_names: List[str], cap: int) -> Tuple[List[str], List[str]]:
    """Split delegate targets into (concurrent_batch, serial_rest).

    Greedy: walk `agent_names` in order, growing the concurrent batch while
    `would_coexist(models so far)` still holds and the cap isn't hit. Everything else
    (VRAM-incompatible, unknown-size, or beyond the cap) falls back to running serially —
    identical to today's one-at-a-time behavior, just deferred to after the batch. This is
    deliberately conservative: it never forces a load that could OOM the box.
    """
    from src.core.intelligence.model_lifecycle import would_coexist

    concurrent: List[str] = []
    serial: List[str] = []
    for name in agent_names:
        if len(concurrent) >= cap:
            serial.append(name)
            continue
        candidate_ids = [_delegate_model_id(n) for n in concurrent + [name]]
        if would_coexist(candidate_ids):
            concurrent.append(name)
        else:
            serial.append(name)
    return concurrent, serial


def create_parallel_delegate_adapter(sub_agents_dict: Dict[str, Any]) -> Callable:
    """Creates the `parallel_delegate` node: resolves ALL `delegate_to_*` calls in the last
    supervisor message (not just the first), partitions them by VRAM eligibility, runs the
    coexist-eligible subset concurrently and the rest serially, and returns every resulting
    `ToolMessage` in one graph step so the supervisor sees all of them resolved together.
    """
    subagent_nodes = {
        agent_name: create_subagent_adapter(agent_name, agent_graph)
        for agent_name, agent_graph in sub_agents_dict.items()
    }

    async def parallel_delegate_node(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
        last_msg = state["messages"][-1]
        agent_names = []
        for tc in last_msg.tool_calls:
            if not tc["name"].startswith("delegate_to_"):
                continue
            name = tc["name"].replace("delegate_to_", "")
            if name in sub_agents_dict and name not in agent_names:
                agent_names.append(name)

        if not agent_names:
            raise ValueError(
                "parallel_delegate_node reached with no resolvable delegate_to_* tool calls "
                "in the last message — routing.py should not have routed here."
            )

        cap = _parallel_delegate_cap()
        concurrent_names, serial_names = _partition_for_concurrency(agent_names, cap)
        logger.info(
            f"[ PARALLEL_DELEGATE ] {len(agent_names)} delegate call(s): "
            f"{concurrent_names} concurrent, {serial_names} serial (VRAM-gated)."
        )

        all_messages: List[Any] = []
        browser_budget_exhausted = bool(state.get("browser_budget_exhausted"))

        # Concurrent batch — VRAM-eligible targets, same shape as parallel_research's
        # asyncio.gather-over-namespaced-graphs fan-out, applied to subagent_node instead.
        if concurrent_names:
            results = await asyncio.gather(
                *[subagent_nodes[name](state, config) for name in concurrent_names]
            )
            for r in results:
                all_messages.extend(r.get("messages", []))
                browser_budget_exhausted |= bool(r.get("browser_budget_exhausted"))

        # Serial fallback — VRAM-incompatible or over-cap targets, run one at a time exactly
        # like today's single-delegate path (each subagent_node call swaps VRAM as needed).
        for name in serial_names:
            result = await subagent_nodes[name](
                {**state, "browser_budget_exhausted": browser_budget_exhausted}, config
            )
            all_messages.extend(result.get("messages", []))
            browser_budget_exhausted |= bool(result.get("browser_budget_exhausted"))

        output = {"messages": all_messages}
        if browser_budget_exhausted:
            output["browser_budget_exhausted"] = True
        return output

    return parallel_delegate_node



