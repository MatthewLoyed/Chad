from typing import Dict, Any
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy, TimeoutPolicy, Command
from langgraph.errors import NodeError, NodeTimeoutError

from src.shared.logger import setup_logger
from src.core.orchestration.handoffs import create_handoff_tools, create_subagent_adapter, create_parallel_delegate_adapter
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.activity_rag_client import ActivityRAGClient
from src.core.intelligence.drive_catalog_client import DriveCatalogClient
from src.core.intelligence.session_history_client import SessionHistoryClient
from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient
from src.core.intelligence.entity_grounding_client import EntityGroundingClient
from src.core.orchestration.langgraph_engine import ChadState, wrap_node_with_validator
from src.core.orchestration.nodes import (
    make_planner_node,
    make_scout_node,
    make_validator_node,
    make_conversational_node,
    make_voice_node,
    make_contact_node,
    make_graphrag_node,
    make_tool_node,
    make_supervisor_node,
    make_approval_wait_node,
    route_entry,
    route_after_scout,
    route_after_planner,
    route_after_approval_wait,
    route_after_validator,
    make_route_after_supervisor,
    route_after_tools,
    route_after_voice_responder,
    route_after_contact_responder,
    route_after_conversational_responder,
)

logger = setup_logger("ChadSupervisor")

# SOURCE OF TRUTH: this local prompt IS the standard — every deployment runs on it whether or
# not Supabase is configured. An optional Supabase override (persona_config, scope=
# deep_research_agent) can layer on top for a given install, but this is not a "cold-start
# fallback" waiting to be replaced; it must stay complete and correct on its own (local-first
# open-source default, see docs/strategy/OPEN_SOURCE_STRATEGY.md).
_DEEP_RESEARCH_AGENT_FALLBACK_PROMPT = (
    "ROLE: You are the deep research specialist. You OWN the whole research workflow for "
    "whatever task the supervisor delegates to you — you NEVER call web_search yourself.\n\n"
    "You have exactly two tools:\n"
    "- `research_discover`: ONE fast worker call that finds and LISTS real item names matching "
    "criteria (breadth). Use this FIRST whenever you don't already know the concrete items to "
    "research (e.g. the task says 'find N caterers/products/companies' without naming them).\n"
    "- `parallel_research`: fans out N concurrent workers, one per item, for the actual depth "
    "research (prices, facts, comparisons — whatever fields were asked for). Write ONE "
    "self-contained brief per item (name + exact fields to find + return format) and pass them "
    "all in `research_briefs`. Even a single already-known item goes through this with a "
    "one-brief list — never skip straight to answering from your own knowledge.\n\n"
    "WORKFLOW:\n"
    "1. If the task already names the specific items, skip straight to step 2.\n"
    "   Otherwise call `research_discover` once to get the list of real item names.\n"
    "2. Write one self-contained research brief per item and call `parallel_research` once with "
    "all of them.\n"
    "3. Synthesize the workers' curated summaries into ONE clean, well-organized report "
    "(a comparison table if the task compares multiple items) and return it as your final "
    "answer — the supervisor relays this directly to the user, so make it complete and readable "
    "on its own. CITATIONS ARE MANDATORY, not optional polish: every worker result you were "
    "given already includes a source URL for each fact — carry those forward, never reconstruct "
    "them from memory or drop them for readability. Mark each claim inline with a bracketed "
    "number (e.g. 'Riu Palace runs $1,233–$2,353/pp [1]'), and end the report with a '## Sources' "
    "list mapping each number to its URL as a markdown link (e.g. '[1] [honeymoonsinc.com](URL)'). "
    "If a fact has no source URL from a worker, do not cite it — state it as unverified or omit it, "
    "never invent a citation.\n"
)


def _build_scout_model(main_model):
    """
    Build the Scout's model. Scout does fast, read-only gather work, so it runs on the
    configured secondary model, independently of the tool selector. Falls back to the main model on any failure.
    """
    from src.shared.config_manager import ConfigManager

    if not ConfigManager.get("planner.scout_use_secondary_model", True):
        return main_model

    try:
        from src.shared.model_selection import secondary_config
        secondary = secondary_config()
        model_name, base_url, api_key = secondary.get("name"), secondary.get("base_url"), secondary.get("api_key")

        from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
        from src.core.intelligence.lc_wrapper import ChatChad

        adapter = LiteLLMAdapter(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            supports_tools=True,
        )
        logger.info(f"[ INIT ] Scout using secondary model: {model_name}")
        return ChatChad(adapter=adapter)
    except Exception as exc:
        logger.warning(f"[ INIT ] Scout secondary model build failed ({exc}); using main model.")
        return main_model


def create_chad_supervisor(model, sub_agents_dict, tool_registry, checkpointer):
    """
    Creates and compiles the top-level supervisor graph using a custom StateGraph.
    Now supports dynamic tool binding based on RAG results in the state.
    """
    logger.info(f"[ INIT ] Creating native supervisor with {len(sub_agents_dict)} sub-agents.")

    # 0. Build deep_research_agent (#510): an orchestrator-only specialist that owns the WHOLE
    # research workflow (breadth discovery -> depth fan-out -> synthesis) so the main
    # supervisor's own thread stays a single delegate-in/report-out instead of accumulating
    # dozens of raw web_search calls itself (the exact failure mode that made a "research 10
    # caterers" turn burn 25 iterations / 44 web_search calls in the main supervisor's context).
    # Runs on the SAME model as the main supervisor (no VRAM swap needed — Gemma-4-26B-A4B is
    # MoE with only ~4B active params, already cheap and already resident) but is NEVER given
    # web_search or any other search tool directly — its only tools are `research_discover`
    # (breadth, one 9B worker) and `parallel_research` (depth, N concurrent 9B workers), both
    # closures over the compiled research_agent graph. Built here rather than via
    # create_specialist_agent's domain-tool lookup because these two tools aren't static
    # registry entries.
    if "research_agent" in sub_agents_dict:
        from src.core.orchestration.handoffs import create_research_discover_tool, create_parallel_research_tool
        from src.core.orchestration.agents.specialist_graph import build_specialist_graph
        from src.core.intelligence.prompt_builder import SystemPromptBuilder

        _dra_tools = [
            create_research_discover_tool(sub_agents_dict["research_agent"]),
            create_parallel_research_tool(sub_agents_dict["research_agent"]),
        ]
        try:
            _dra_prompt = SystemPromptBuilder.build(base_instruction="", scope="deep_research_agent")
        except Exception as e:
            logger.warning(f"[ INIT ] deep_research_agent prompt registry miss ({e}); using fallback prompt.")
            _dra_prompt = _DEEP_RESEARCH_AGENT_FALLBACK_PROMPT
        sub_agents_dict = dict(sub_agents_dict)
        sub_agents_dict["deep_research_agent"] = build_specialist_graph(
            model=model,
            tools=_dra_tools,
            prompt=_dra_prompt,
            checkpointer=checkpointer,
            name="deep_research_agent",
        )

    # 1. Initialize Specialist Handoffs — deep_research_agent above is now in sub_agents_dict, so
    # this naturally creates a `delegate_to_deep_research_agent` tool for it (research_agent
    # itself stays excluded — see create_handoff_tools). research_discover/parallel_research are
    # NOT bound here or in the ToolNode below: they live only inside deep_research_agent's own
    # subgraph tool set (built above), never directly reachable by the main supervisor.
    handoff_tools = create_handoff_tools(sub_agents_dict)

    # 2. Initialize ALL Registry Tools for the ToolNode
    # The ToolNode must be able to execute any tool the model might call.
    all_registry_tools = tool_registry.get_all_langchain_tools()

    # Handoff tools are handled natively by dedicated nodes; ToolNode runs the remaining atomic
    # registry tools (web_search, file tools, etc.).
    tool_node = ToolNode(
        [t for t in all_registry_tools if t is not None],
        handle_tool_errors=True,
    )
    
    # --- Custom Tool Node with Schema Injection ---
    wrapped_tool_node = make_tool_node(tool_node, tool_registry=tool_registry)
    
    # --- Instantiate Nodes via Factories ---
    planner_node = make_planner_node(model, tool_registry, handoff_tools)
    scout_node = make_scout_node(_build_scout_model(model), tool_registry, handoff_tools)
    validator_node = make_validator_node(model)
    conversational_responder = make_conversational_node(model)
    voice_responder = make_voice_node(model, tool_registry)
    contact_responder = make_contact_node(model, tool_registry)
    approval_wait_node = make_approval_wait_node()
    
    graph_client = GraphRAGClient()
    activity_client = ActivityRAGClient()
    drive_client = DriveCatalogClient()
    session_client = SessionHistoryClient()
    calendar_client = CalendarScheduleClient()
    entity_client = EntityGroundingClient(tool_registry=tool_registry)
    graphrag_injector_node = make_graphrag_node(
        graph_client,
        activity_client,
        drive_client,
        session_client,
        calendar_client,
        entity_client=entity_client,
    )
    
    call_supervisor = make_supervisor_node(model, sub_agents_dict, tool_registry, handoff_tools)
    
    # --- Instantiate Routers ---
    route_after_supervisor = make_route_after_supervisor(sub_agents_dict)
    
    # 3. Build Graph
    workflow = StateGraph(ChadState)
    
    # Define Timeout & Retry Policies
    HEAVY_TIMEOUT = TimeoutPolicy(
        run_timeout=1800.0,  # 30-minute cap for sandbox coding / traversal / large docs
        idle_timeout=600.0   # 10-minute inactivity watchdog (local LLM ainvoke often silent)
    )
    STANDARD_TIMEOUT = TimeoutPolicy(
        run_timeout=300.0,   # 5-minute total cap
        # 5-minute inactivity watchdog (was 2 min) — a healthy third-party-service call
        # (e.g. generate_presentation's Presenton request) can legitimately go silent for a
        # while; tools that expect longer silent stretches should also emit heartbeats via
        # await_with_progress_heartbeat/emit_node_progress (see generate_presentation.py)
        # rather than relying solely on this cap.
        idle_timeout=300.0
    )
    QUICK_TIMEOUT = TimeoutPolicy(
        run_timeout=180.0,   # 3-minute total cap
        idle_timeout=90.0    # 90-second inactivity watchdog
    )
    # Planner/supervisor run LLM ainvoke (not streaming), so idle progress signals often don't fire
    # during long local-model thinking. Use a generous idle window.
    PLANNER_TIMEOUT = TimeoutPolicy(
        run_timeout=900.0,   # 15-minute wall-clock cap for complex multi-step plans
        idle_timeout=600.0   # 10-minute inactivity watchdog
    )
    SUPERVISOR_TIMEOUT = TimeoutPolicy(
        run_timeout=1800.0,  # 30-minute wall-clock cap
        idle_timeout=600.0   # 10-minute inactivity watchdog
    )

    def _is_retryable(exc: BaseException) -> bool:
        return not isinstance(exc, NodeTimeoutError)

    DEFAULT_RETRY = RetryPolicy(
        max_attempts=3,
        retry_on=_is_retryable
    )

    def make_agent_error_handler(agent_name: str):
        """Generates a standard native error_handler for a specialist agent node."""
        def handler(state: ChadState, error: NodeError) -> Command:
            display_name = agent_name.replace("_agent", "").replace("_", " ").title()
            error_msg = (
                f"⚠️ CRITICAL: Specialized agent '{display_name}' failed to complete its task.\n"
                f"ERROR TYPE: {type(error.error).__name__}\n"
                f"DETAILS: {str(error.error)}\n\n"
                "INSTRUCTIONS FOR SUPERVISOR:\n"
                "1. Read the failure reason above to understand why this specialist failed.\n"
                "2. Do NOT delegate back to this specialist for the same task in this turn.\n"
                "3. Attempt an alternative specialist, use direct atomic tools, or report the limitation cleanly to the user."
            )
            last_msg = state["messages"][-1] if state.get("messages") else None
            pending_call_id = None
            tool_name = f"delegate_to_{agent_name}"
            if last_msg and getattr(last_msg, "tool_calls", None):
                for tc in last_msg.tool_calls:
                    if tc.get("name") in (tool_name, agent_name):
                        pending_call_id = tc.get("id")
                        tool_name = tc.get("name")
                        break
                if not pending_call_id and last_msg.tool_calls:
                    pending_call_id = last_msg.tool_calls[-1].get("id")
                    tool_name = last_msg.tool_calls[-1].get("name")

            if pending_call_id:
                msg = ToolMessage(
                    content=error_msg,
                    tool_call_id=pending_call_id,
                    name=tool_name
                )
            else:
                msg = AIMessage(content=error_msg, name=f"{agent_name}_error_boundary")
            
            # Native Command: update message history and route back to 'supervisor'
            return Command(
                update={"messages": [msg]},
                goto="supervisor"
            )
        return handler

    def supervisor_error_handler(state: ChadState, error: NodeError) -> Command:
        exc = error.error
        if isinstance(exc, NodeTimeoutError):
            if exc.kind == "idle":
                user_msg = (
                    f"⚠️ This step took too long without a response (over {exc.timeout:.0f}s idle). "
                    "This can happen during large code generation when the model is still working. "
                    "Try breaking the request into smaller tasks, or retry."
                )
            else:
                user_msg = (
                    f"⚠️ This step exceeded the maximum allowed time ({exc.timeout:.0f}s). "
                    "Try breaking the request into smaller tasks."
                )
            logger.warning(f"[ SUPERVISOR ] {user_msg} ({type(exc).__name__}: {exc})")
        else:
            user_msg = (
                f"⚠️ Supervisor encountered an error: {type(exc).__name__}: {exc}"
            )
            logger.error(f"[ SUPERVISOR ] Unhandled node error: {exc}", exc_info=exc)

        msg = AIMessage(content=user_msg)
        return Command(
            update={"messages": [msg], "final_response": user_msg},
            goto=END,
        )

    workflow.add_node(
        "voice_responder",
        wrap_node_with_validator(voice_responder),
        timeout=QUICK_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
    )
    workflow.add_node(
        "contact_responder",
        wrap_node_with_validator(contact_responder),
        timeout=QUICK_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
    )
    workflow.add_node(
        "conversational_responder", 
        wrap_node_with_validator(conversational_responder),
        timeout=QUICK_TIMEOUT,
        retry_policy=DEFAULT_RETRY
    )
    workflow.add_node(
        "scout",
        wrap_node_with_validator(scout_node),
        timeout=PLANNER_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
    )
    workflow.add_node(
        "planner", 
        wrap_node_with_validator(planner_node),
        timeout=PLANNER_TIMEOUT,
        retry_policy=DEFAULT_RETRY
    )
    workflow.add_node(
        "approval_wait",
        wrap_node_with_validator(approval_wait_node),
        timeout=QUICK_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
    )
    workflow.add_node(
        "supervisor", 
        wrap_node_with_validator(call_supervisor),
        timeout=SUPERVISOR_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
        error_handler=supervisor_error_handler,
    )
    workflow.add_node(
        "validator", 
        wrap_node_with_validator(validator_node),
        timeout=QUICK_TIMEOUT,
        retry_policy=DEFAULT_RETRY
    )
    workflow.add_node(
        "tools",
        wrap_node_with_validator(wrapped_tool_node),
        # STANDARD_TIMEOUT: this node only runs atomic registry tools now. `parallel_research`
        # (and `research_discover`) moved into deep_research_agent's own subgraph node (#510),
        # which gets HEAVY_TIMEOUT via the specialist-node loop below — so the heavy multi-batch
        # research fan-out that used to need a bigger cap here no longer runs in this node at all.
        timeout=STANDARD_TIMEOUT,
        retry_policy=DEFAULT_RETRY
    )
    workflow.add_node("graphrag_injector", wrap_node_with_validator(graphrag_injector_node))
    
    # 4. Establish Connections
    workflow.set_entry_point("graphrag_injector")
    
    # Entry point fork now triggers AFTER injection
    workflow.add_conditional_edges(
        "graphrag_injector",
        route_entry,
        {
            "conversational": "conversational_responder",
            "voice_responder": "voice_responder",
            "contact_responder": "contact_responder",
            "planner": "planner",
            "scout": "scout",
            "supervisor": "supervisor",
        }
    )

    workflow.add_conditional_edges(
        "scout",
        route_after_scout,
        {
            "planner": "planner",
            "__end__": END,
        },
    )

    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "scout": "scout",
            "planner": "planner",
            "supervisor": "supervisor",
            "approval_wait": "approval_wait",
            "__end__": END,
        }
    )

    workflow.add_conditional_edges(
        "approval_wait",
        route_after_approval_wait,
        {
            "planner": "planner",
            "supervisor": "supervisor",
        }
    )

    workflow.add_conditional_edges(
        "validator",
        route_after_validator,
        {
            "planner": "planner",
            "__end__": END
        }
    )
    
    # Mount specialist subgraphs as first-class Native Subgraph Nodes
    for agent_name, agent_graph in sub_agents_dict.items():
        logger.debug(f"[ GRAPH ] Registering specialist subgraph node: '{agent_name}'")
        subagent_node = create_subagent_adapter(agent_name, agent_graph)
        
        # Select appropriate timeout policy. deep_research_agent needs HEAVY_TIMEOUT (#510) —
        # it internally runs research_discover + parallel_research, whose depth fan-out can run
        # multiple sequential worker batches and already needed the heavy cap on its own.
        timeout_policy = HEAVY_TIMEOUT if agent_name in [
            "code_agent", "browser_agent", "research_agent", "traversal_agent", "deep_research_agent", "canvas_agent",
        ] else STANDARD_TIMEOUT
        
        workflow.add_node(
            agent_name, 
            wrap_node_with_validator(subagent_node),
            timeout=timeout_policy,
            retry_policy=DEFAULT_RETRY,
            error_handler=make_agent_error_handler(agent_name)
        )
        
        # Return to supervisor after specialist completes its task — UNLESS a
        # gated write action paused inside the specialist (issue #279/#325):
        # then exit to the user instead of looping back into the supervisor's
        # own model, mirroring route_after_tools for the "tools" node below.
        workflow.add_conditional_edges(
            agent_name,
            route_after_tools,
            {
                "supervisor": "supervisor",
                "voice_responder": "voice_responder",
                "contact_responder": "contact_responder",
                "__end__": END,
            },
        )

    # Mount the parallel_delegate node (#513) — routing.py sends here instead of a single
    # agent_name node when the supervisor emits more than one delegate_to_* call in one turn.
    # Same timeout/retry/error-handler shape as the specialist nodes above; HEAVY_TIMEOUT
    # because it can run a heavy specialist (e.g. code_agent) serially inside its fallback path.
    parallel_delegate_node = create_parallel_delegate_adapter(sub_agents_dict)
    workflow.add_node(
        "parallel_delegate",
        wrap_node_with_validator(parallel_delegate_node),
        timeout=HEAVY_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
        error_handler=make_agent_error_handler("parallel_delegate"),
    )
    workflow.add_conditional_edges(
        "parallel_delegate",
        route_after_tools,
        {
            "supervisor": "supervisor",
            "voice_responder": "voice_responder",
            "contact_responder": "contact_responder",
            "__end__": END,
        },
    )

    # Build conditional path map mapping each agent name to its node
    supervisor_path_map = {
        "supervisor": "supervisor",
        "tools": "tools",
        "planner": "planner",
        "scout": "scout",
        "validator": "validator",
        "parallel_delegate": "parallel_delegate",
        "__end__": END
    }
    for agent_name in sub_agents_dict:
        supervisor_path_map[agent_name] = agent_name

    workflow.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        supervisor_path_map
    )
    
    workflow.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "supervisor": "supervisor",
            "voice_responder": "voice_responder",
            "contact_responder": "contact_responder",
            "conversational_responder": "conversational_responder",
            "__end__": END,
        },
    )
    workflow.add_conditional_edges(
        "voice_responder",
        route_after_voice_responder,
        {"tools": "tools", "__end__": END},
    )
    workflow.add_conditional_edges(
        "contact_responder",
        route_after_contact_responder,
        {"tools": "tools", "__end__": END},
    )
    workflow.add_conditional_edges(
        "conversational_responder",
        route_after_conversational_responder,
        {"tools": "tools", "__end__": END},
    )
    
    # Pause only after initial plans reach approval_wait. Autonomous replans skip that node.
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_after=["approval_wait"],
    )
