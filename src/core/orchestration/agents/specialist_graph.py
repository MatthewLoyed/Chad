"""
Specialist subagent graph builder — a HITL-aware replacement for
`langgraph.prebuilt.create_react_agent` (issue #279 follow-up).

Bug this fixes: `create_react_agent`'s built-in routing only checks whether the
last AI message has tool_calls; it has no concept of `_pending_approval` (the
signal `evaluate_approval_gate`/browser_safety emit when a tool call — e.g. a
browser_click classified as a write — must pause for a human decision). A
specialist built on the stock prebuilt agent just fed that pending-approval
JSON back to its own model and kept looping, so the write executed anyway (or
the model hallucinated around it) and no ApprovalCard ever reached the UI,
because the pause never propagated out of the subagent's isolated thread.

This mirrors the pattern already proven for the supervisor's own graph
(nodes/tool_node.py sets `state["pending_approval"]`; nodes/routing.py's
`route_after_tools` exits to the user when it's set) and for the planner's
scout gather loop (nodes/scout_node.py breaks its manual loop on
`_pending_approval`). Reusing `make_tool_node` here means a pending approval
in a specialist's own tool call sets `pending_approval` in the specialist's
returned state exactly like it does for the supervisor, and the graph exits
immediately instead of continuing the ReAct loop — `handoffs.py` then reads
that key off the subgraph's result and forwards it to the supervisor.
"""

import logging
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

logger = logging.getLogger("SpecialistGraph")

from src.core.orchestration.agents.agent_utils import create_fault_tolerant_tool_node
from src.core.orchestration.graph.state import ChadState
from src.core.orchestration.nodes.tool_node import make_tool_node


def get_specialist_max_steps(name: Optional[str]) -> int:
    """Shared by the local loop and handoff recursion allowance."""
    from src.shared.config_manager import ConfigManager
    if name == "browser_agent":
        value = ConfigManager.get("orchestration.browser_agent_max_steps") or ConfigManager.get("tools.browser_max_actions_per_task") or 60
    elif name == "ui_automation_agent":
        value = ConfigManager.get("orchestration.ui_automation_agent_max_steps", 40)
    else:
        value = ConfigManager.get("orchestration.specialist_max_steps", 30)
    return max(1, int(value))


def build_specialist_graph(
    model: Any,
    tools: List[Any],
    prompt: str,
    checkpointer: Optional[Any] = None,
    name: Optional[str] = None,
) -> Any:
    """
    Compile a two-node (agent <-> tools) ReAct graph for one specialist subagent.

    Behaviorally equivalent to `create_react_agent(model, tools, prompt, checkpointer)`
    for the common case (loop until the model stops calling tools), except the
    tools node can halt the graph early by setting `pending_approval` in state.
    """
    from src.shared.config_manager import ConfigManager
    if name == 'browser_agent' and ConfigManager.get('orchestration.browser_controller_enabled', False):
        from src.core.orchestration.agents.browser_controller import build_browser_graph
        return build_browser_graph(model, tools, prompt, checkpointer)
    bound_model = model.bind_tools(tools) if tools else model
    system_prompt = prompt or ""

    async def agent_node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        messages = list(state.get("messages") or [])
        if system_prompt:
            messages = [SystemMessage(content=system_prompt)] + messages
        # When budget is exhausted, call model without tools to synthesize final summary
        if state.get("budget_exceeded"):
            synthesis_prompt = (
                f"This delegation stopped after {state.get('specialist_steps', 0)} tool rounds "
                "because a runtime budget was reached. Do not invent a different limit. "
                "Report back progress to the supervisor. "
                "Do NOT attempt to call any more tools or output tool tags. Synthesize a structured, "
                "informative report detailing: "
                "(1) What actions were completed, "
                "(2) Actual returned target names/IDs and current page/window/application state, "
                "(3) Why the task stopped or what remains to be done, "
                "(4) Which requested postconditions are observed versus still unverified. "
                "Input success alone is not an application outcome. Do not suggest guessed "
                "executable names as proof that a launcher instance did or did not start."
            )
            synthesis_messages = messages + [HumanMessage(content=synthesis_prompt)]
            response = await model.ainvoke(synthesis_messages, config=config)
            if getattr(response, "tool_calls", None):
                response.tool_calls = []
            text_body = getattr(response, "content", None)
            if not text_body or not str(text_body).strip():
                response.content = (
                    "Reached the runtime tool limit. No final outcome was verified by this report; "
                    "review the recorded tool evidence before claiming completion."
                )
        else:
            response = await bound_model.ainvoke(messages, config=config)
        return {"messages": [response]}

    base_tools_node = make_tool_node(create_fault_tolerant_tool_node(tools))

    async def wrapped_tools_node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        result = await base_tools_node(state, config)
        steps = state.get("specialist_steps", 0) + 1
        result["specialist_steps"] = steps
        max_steps = get_specialist_max_steps(name)
        if steps >= max_steps:
            logger.warning(
                f"[ SPECIALIST ] Specialist step cap reached for {name or 'subagent'} ({steps}/{max_steps}). "
                "Forcing synthesis and returning control to supervisor."
            )
            result["budget_exceeded"] = True
        return result

    def route_after_agent(state: dict) -> str:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if getattr(last, "tool_calls", None) and not state.get("budget_exceeded"):
            return "tools"
        return END

    def route_after_tools(state: dict) -> str:
        if state.get("pending_approval"):
            return END
        return "agent"

    workflow = StateGraph(ChadState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", wrapped_tools_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    workflow.add_conditional_edges("tools", route_after_tools, {"agent": "agent", END: END})

    return workflow.compile(checkpointer=checkpointer, name=name)
