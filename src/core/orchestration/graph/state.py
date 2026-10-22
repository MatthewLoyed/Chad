"""ChadState schema and pre-checkpoint sanitization helpers.

Extracted from langgraph_engine.py (#302) — keeps the state schema and the
serialization-hardening reducers/wrappers that guard it in one focused module.
"""
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Callable, Annotated, Sequence, Union
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.channels.delta import DeltaChannel
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from src.core.orchestration.context import progress_heartbeat_var


def append_messages_bulk(state: List[AnyMessage], writes: Sequence[Union[List[AnyMessage], AnyMessage]]) -> List[AnyMessage]:
    """
    A bulk reducer for LangGraph DeltaChannel compatibility.
    Flattens the sequence of writes and delegates merging to the robust built-in add_messages.
    """
    flat_writes = []
    for w in writes:
        if isinstance(w, list):
            flat_writes.extend(w)
        else:
            flat_writes.append(w)
    return add_messages(state, flat_writes)

logger = logging.getLogger("LangGraphEngine")


class UntrackedValue:
    """
    Wrapper for runtime-only, ephemeral state values in ChadState.
    These values exist in memory during graph execution but are stripped
    by the pre-checkpoint validator before serialization.
    """
    def __init__(self, value: Any):
        self.value = value

    def __repr__(self) -> str:
        return f"UntrackedValue({type(self.value).__name__})"


def merge_variables(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reducer that merges dictionary updates for state['variables']."""
    res = dict(old or {})
    if new:
        res.update(new)
    return res


def pre_checkpoint_validator(variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates that all values inside state['variables'] are JSON serializable.
    Strips any UntrackedValue wrappers or non-JSON-serializable objects,
    logging warnings for non-serializable fields.
    """
    if not isinstance(variables, dict):
        return {}

    sanitized = {}
    for k, v in variables.items():
        # Handle UntrackedValue
        if isinstance(v, UntrackedValue):
            logger.debug(f"[ VALIDATOR ] Stripping UntrackedValue key '{k}' (type: {type(v.value).__name__}) before checkpoint serialization.")
            continue

        # Handle standard JSON serialization test
        try:
            json.dumps(v)
            sanitized[k] = v
        except (TypeError, OverflowError) as e:
            # Emit WARNING log with offending key and type
            logger.warning(
                f"[ VALIDATOR ] State serialization failure for key '{k}' with type '{type(v).__name__}'. "
                f"Error: {e}. Stripping value to prevent checkpointer fallback."
            )

    return sanitized


def wrap_node_with_validator(node_fn: Callable) -> Callable:
    """
    Wraps a LangGraph node function to automatically validate and sanitize
    the state['variables'] and top-level fields before checkpointing.
    Also wires LangGraph runtime.heartbeat() so adapters can refresh idle timeouts.
    """
    async def wrapped_fn(
        state: Dict[str, Any],
        config: RunnableConfig = None,
        *,
        runtime: Runtime,
    ) -> Dict[str, Any]:
        hb_token = progress_heartbeat_var.set(runtime.heartbeat)
        try:
            if asyncio.iscoroutinefunction(node_fn):
                result = await node_fn(state, config)
            else:
                result = node_fn(state, config)
        finally:
            progress_heartbeat_var.reset(hb_token)

        if not isinstance(result, dict):
            return result

        # Sanitize result updates
        if isinstance(result, dict):
            # Compute prospective variables by merging current state['variables'] and returned updates
            current_vars = dict(state.get("variables") or {})
            returned_vars = result.get("variables")

            if returned_vars is not None:
                current_vars.update(returned_vars)
                # Sanitize the prospective variables
                result["variables"] = pre_checkpoint_validator(current_vars)
            elif "variables" in state:
                # If variables are in state but not returned, make sure they are sanitized in case of in-place mutation
                sanitized_existing = pre_checkpoint_validator(state["variables"])
                if sanitized_existing != state["variables"]:
                    result["variables"] = sanitized_existing

            # Also handle top-level UntrackedValue objects returned by the node
            for k in list(result.keys()):
                if isinstance(result[k], UntrackedValue):
                    logger.debug(f"[ VALIDATOR ] Stripping top-level UntrackedValue key '{k}' from node returned updates.")
                    result.pop(k)

        return result

    return wrapped_fn


class ChadState(TypedDict):
    """
    The state of the Chad agent during a single turn.
    """
    messages: Annotated[
        List[AnyMessage],
        DeltaChannel(reducer=append_messages_bulk, snapshot_frequency=50)
    ]

    # Orchestration Context
    turn_id: str
    session_id: str
    category: str
    turn_mode: str
    platform: str
    is_simple: bool
    iteration_count: int  # 👈 track how many loops we've done

    # Planning State
    active_plan: Optional[List[Dict[str, Any]]]
    plan_step: str
    # The user's original request, captured at plan time and RE-INJECTED into every
    # supervisor execution round. During execution the latest HumanMessage is the approval
    # ("proceed"), so the real objective is otherwise buried far up the message history and
    # the model loses the thread over a long multi-round run. This is its north star.
    plan_goal: Optional[str]
    failure_context: Optional[str]
    validation_feedback: Optional[str]
    plan_replanned: Optional[bool]
    paused_for_input: Optional[bool]
    pending_approval: Optional[Dict[str, Any]]
    budget_exceeded: Optional[bool]
    browser_budget_exhausted: Optional[bool]
    specialist_steps: Optional[int]
    browser_task: Optional[Dict[str, Any]]
    browser_progress: Optional[Dict[str, Any]]

    # Scout gather (plan lane — planner-directed)
    gather_brief: Optional[str]
    confirmed_tool_menu: Optional[List[Dict[str, str]]]
    # Brief from the planner telling Scout what environment facts to gather.
    scout_gather_request: Optional[str]
    scout_correlation_id: Optional[str]
    scout_decision: Optional[str]
    scout_evidence: Optional[List[Dict[str, Any]]]
    scout_coverage: Optional[str]
    scout_episode_id: Optional[str]
    # How many times the planner has dispatched Scout this plan-lane episode.
    scout_dispatch_count: Optional[int]

    # Injection Data (Scratchpad, etc.)
    system_instruction: str
    # Per-turn personal knowledge (graph + activity RAG). Kept SEPARATE from
    # system_instruction so the system prompt stays byte-stable across turns —
    # volatile RAG in the system prefix invalidates the local-model KV cache for
    # the entire conversation history. The supervisor injects this into the
    # trailing user turn instead (uncached tail). See graphrag_node / supervisor_node.
    knowledge_boost: str
    tools: Optional[List[Dict[str, Any]]]

    # Execution Metadata
    total_tool_calls: int
    max_tool_calls: int
    metadata: Optional[Dict[str, Any]]

    # Final Result
    final_response: Optional[str]
    tool_results: List[Dict[str, Any]]

    # Configuration
    temperature: float
    thinking_level: str

    # Active Skill Context (set by load_skill tool, cleared per-turn)
    loaded_skill: Optional[str]
    loaded_skill_name: Optional[str]

    # Voice lane handoff (escalate_to_agent → full agent pipeline)
    voice_escalate: Optional[bool]
    voice_escalate_reason: Optional[str]
    voice_tool_rounds: Optional[int]
    chat_tool_rounds: Optional[int]

    # Temporal variables dictionary (hardened against serialization failures)
    variables: Annotated[
        Dict[str, Any],
        merge_variables
    ]

    # Note: Callbacks (on_token, on_thinking) are excluded from state to allow serialization
    # They should be passed via config instead.
