from contextvars import ContextVar
from typing import Optional, Callable, Any
import asyncio

# ContextVars for passing non-serializable callbacks through the graph
on_token_var: ContextVar[Optional[Callable[[str], Any]]] = ContextVar("on_token", default=None)
on_thinking_var: ContextVar[Optional[Callable[[str, str, bool], Any]]] = ContextVar("on_thinking", default=None)
on_usage_var: ContextVar[Optional[Callable[[dict], Any]]] = ContextVar("on_usage", default=None)
agent_name_var: ContextVar[str] = ContextVar("agent_name", default="Supervisor")
parent_correlation_id_var: ContextVar[Optional[str]] = ContextVar("parent_correlation_id", default=None)

# Shared, task-scoped search-result budget (issue #524). Set once when the supervisor
# delegates to deep_research_agent (src/core/orchestration/handoffs.py handoff_fn) and
# read by WebSearchTool.execute() (src/tools/web/web_search.py). asyncio Tasks copy the
# current Context at creation time, so every research_discover/parallel_research worker
# task spawned under the deep_research_agent turn shares the same SearchBudget instance
# without threading it through RunnableConfig/exec_params across every layer.
search_budget_var: ContextVar[Optional[Any]] = ContextVar("search_budget", default=None)

# LangGraph runtime.heartbeat() for the active timed node attempt.
progress_heartbeat_var: ContextVar[Optional[Callable[[], None]]] = ContextVar(
    "progress_heartbeat", default=None
)

# ContextVar for tracking turn query and embedding mapping for OATS
current_turn_tool_queries_var: ContextVar[Optional[dict]] = ContextVar("current_turn_tool_queries", default=None)

# ContextVar for tracking programmatic turn termination via the generic
# requires_approval tool gate (src/core/orchestration/approval_store.py).
# Holds {token, tool_name, decision_type, prompt, options} while a decision is pending.
pending_approval_var: ContextVar[Optional[dict]] = ContextVar("pending_approval", default=None)

# Per-turn thinking override from the UI toggle.
# None = use per-model config default. True/False = explicit user override for this turn.
thinking_enabled_var: ContextVar[Optional[bool]] = ContextVar("thinking_enabled", default=None)

# The kind of client that STARTED this turn ("desktop" | "browser" | "eval"), resolved
# from the socket the message arrived on (LimbMessageHandler, issue #711).
#
# HybridToolExecutor reads it to decide whether a Limb-partitioned tool can be proxied to
# the client at all. A browser/PWA has no local tool registry — it answers TOOL_REQUEST
# with "Local tools are unavailable in the browser client" — so proxying a phone turn's
# `read_file` could only ever fail. The Brain runs on the same machine those tools target,
# so it runs them itself instead. None (autonomous/cron turns) keeps the proxy behaviour.
client_type_var: ContextVar[Optional[str]] = ContextVar("client_type", default=None)

# Turn routing / delivery — who should receive stream events (gui vs voice vs silent).
turn_delivery_var: ContextVar[Optional[str]] = ContextVar("turn_delivery", default=None)
turn_category_var: ContextVar[Optional[str]] = ContextVar("turn_category", default=None)
turn_id_var: ContextVar[Optional[str]] = ContextVar("turn_id", default=None)
# Set ONCE from the raw incoming category at the very top of a turn, before mode resolution
# (turn_coordinator._mode_resolver.resolve) overwrites turn_category_var with the resolved
# chat/agent/plan lane. Model-lifecycle priority (model_lifecycle.current_priority()) depends
# on this surviving mode resolution intact — reading turn_category_var directly would see
# "agent"/"chat"/"plan" instead of "autonomous" by the time a tool-selection or model-preload
# call happens, silently defeating the background-priority admission queue.
turn_priority_var: ContextVar[str] = ContextVar("turn_priority", default="interactive")
# Conversation/session id (LangGraph thread_id) for the active turn. Unlike turn_id,
# this is stable across the multi-turn pending-approval handshake (request -> resolve),
# which is what the approval gate keys on (src/core/orchestration/approval_store.py).
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
# Controller delegation scope. Approval identity remains conversation-scoped;
# browser budgets and duplicate-failure guards reset only for a NEW delegation.
browser_run_id_var: ContextVar[Optional[str]] = ContextVar('browser_run_id', default=None)
browser_action_metrics_var: ContextVar[Optional[dict]] = ContextVar('browser_action_metrics', default=None)


def record_browser_mcp_call() -> None:
    metrics = browser_action_metrics_var.get()
    if metrics is not None:
        metrics['mcp_calls'] = metrics.get('mcp_calls', 0) + 1


def browser_scope_key() -> Optional[str]:
    session, run = session_id_var.get(), browser_run_id_var.get()
    return f'{session}:{run}' if run else session
# Active domain-agent scope for the turn (e.g. "financial"); None = general/untagged tier.
# Memory *writers* (GraphRAGClient.ingest, MemoryEngine.save_memory) read this to stamp what
# they persist, which is why it is a ContextVar rather than a threaded argument -- tools do
# not receive RequestContext on that path. Do not confuse with turn_category_var (turn lane).
domain_var: ContextVar[Optional[str]] = ContextVar("domain", default=None)


def current_turn_delivery() -> str:
    from src.shared.turn_delivery import DELIVERY_GUI, resolve_delivery

    explicit = turn_delivery_var.get()
    if explicit:
        return explicit
    return resolve_delivery(category=turn_category_var.get())


def emit_node_progress() -> None:
    """Reset LangGraph idle timeout during long-running work inside a timed node."""
    heartbeat = progress_heartbeat_var.get()
    if heartbeat:
        try:
            heartbeat()
        except Exception:
            pass


async def await_with_progress_heartbeat(awaitable, interval: float = 10.0):
    """
    Await a coroutine while emitting progress heartbeats at a fixed interval.
    Used when an LLM call is non-streaming and cannot refresh the idle clock via tokens.
    """
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except asyncio.TimeoutError:
                emit_node_progress()
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception:
        if not task.done():
            task.cancel()
        raise

