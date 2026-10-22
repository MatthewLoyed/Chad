import json
import logging
import asyncio
import os
from typing import Dict, Any, List, Optional
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langchain_core.runnables import RunnableConfig
from src.core.intelligence.adapters.ai_adapter_base import AIAdapter
from src.core.intelligence.lc_wrapper import ChatChad
from src.core.orchestration.agents import create_specialist_agents
from src.shared.logger import setup_logger

from src.core.orchestration.context import agent_name_var, pending_approval_var

# --- Re-exported collaborators (moved out under #302 to keep this file a thin
# coordinator; several other modules and tests import these symbols from here) ---
from src.core.orchestration.graph.state import (
    UntrackedValue,
    merge_variables,
    pre_checkpoint_validator,
    wrap_node_with_validator,
    ChadState,
)
from src.core.orchestration.graph.checkpointing import SafeMemorySaver, SafeAsyncPostgresSaver, SafeAiosqliteSaver
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.core.orchestration.graph.message_conversion import to_chad_messages, from_chad_history
from src.core.orchestration.graph.event_stream import run_turn_stream

logger = setup_logger("LangGraphEngine")
logging.getLogger("langgraph").setLevel(logging.DEBUG)


class LangGraphEngine:
    """
    Unified orchestration engine using LangGraph.
    Provides a robust ReAct loop with state persistence and modularity.

    This class is a thin coordinator: graph compilation delegates to
    `create_chad_supervisor`, state schema/sanitization lives in
    `graph/state.py`, checkpointer hardening in `graph/checkpointing.py`,
    message format conversion in `graph/message_conversion.py`, and the
    per-turn event-stream consumption in `graph/event_stream.py`.
    """

    def __init__(self, adapter: AIAdapter, tool_executor: Any, mcp_ready_event: Optional[Any] = None):
        self.adapter = adapter
        self.tool_executor = tool_executor
        # MCP tool discovery runs as a background task at boot; subagents bind their tool
        # lists at graph-compile time. If the graph is ever compiled before discovery
        # finishes (e.g. a chat-first turn that skips the per-turn MCP wait), MCP-dependent
        # specialists like browser_agent get cached with ZERO of their tools. Awaiting this
        # event before building the agents closes that race. See get_graph().
        self.mcp_ready_event = mcp_ready_event
        self._checkpointer: Optional[AsyncPostgresSaver] = None
        self._pool: Optional[AsyncConnectionPool] = None
        self._compiled_graph = None
        self._compiled_disabled_agents = set()

        # Initialize LangChain wrapper for the adapter
        self.lc_model = ChatChad(adapter=adapter)

        logger.info(f"[ GRAPH ] Initialized with adapter: {adapter.__class__.__name__}")

    async def _get_checkpointer(self) -> AsyncPostgresSaver:
        """Initialize and return the checkpointer (local sqlite by default; Postgres opt-in)."""
        if self._checkpointer:
            return self._checkpointer

        # Speed up unit tests by bypassing connection attempts
        if os.getenv("CHAD_TESTING") == "true":
            logger.info("[ GRAPH ] Fast-track checkpointer to MemorySaver for unit testing.")
            self._checkpointer = SafeMemorySaver()
            return self._checkpointer

        from src.shared.config_manager import ConfigManager
        checkpointer_storage = ConfigManager.get("langgraph.checkpointer_storage", "local")

        # Local-first default (same policy as memory.storage / drive_catalog.storage):
        # conversation checkpoints stay on-disk unless explicitly opted into Postgres via
        # langgraph.checkpointer_storage: supabase. Zero-setup, no per-graph-step network
        # round-trip, durable across restarts (issue #416), and lets SessionHistoryIngestor
        # read real conversation history straight off disk instead of needing a Postgres path.
        if checkpointer_storage != "supabase" or not get_supabase_manager().enabled:
            logger.info("[ GRAPH ] Using local sqlite checkpointer (local-first).")
            import aiosqlite
            local_store_path = os.getenv("CHAD_LOCAL_CHECKPOINT_DB_PATH") or os.path.join("data", "local_langgraph_checkpoints.db")
            os.makedirs(os.path.dirname(local_store_path) or ".", exist_ok=True)
            conn = await aiosqlite.connect(local_store_path)
            self._checkpointer = SafeAiosqliteSaver(conn)
            await self._checkpointer.setup()
            return self._checkpointer

        # Explicit opt-in (langgraph.checkpointer_storage: supabase): checkpoint directly
        # to Postgres instead — e.g. for live inspection via LangGraph Studio/Supabase.
        # 1. Prioritize direct SUPABASE_DB_URL from .env
        conn_string = os.getenv("SUPABASE_DB_URL")

        if not conn_string:
            # Fallback to manual construction
            db_pass = os.getenv("SUPABASE_DB_PASSWORD", "").strip('"')
            supabase_url = os.getenv("SUPABASE_URL", "")
            project_id = supabase_url.split("//")[1].split(".")[0] if "//" in supabase_url else ""
            conn_string = f"postgresql://postgres.{project_id}:{db_pass}@aws-1-us-east-2.pooler.supabase.com:5432/postgres"

        # 2. HARDENING: Inject TCP Keepalive parameters to sustain connections during long compute pauses.
        # Prevents 'SSL SYSCALL error: EOF detected' during intensive nodes like Deep Research.
        sep = "&" if "?" in conn_string else "?"
        conn_string += f"{sep}keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"

        logger.info("[ GRAPH ] Connecting to Postgres checkpointer with TCP keepalives active...")

        # 3. Create a persistent pool with required LangGraph settings
        self._pool = AsyncConnectionPool(
            conninfo=conn_string,
            max_size=10,
            min_size=1,
            max_idle=300.0,  # Prune dead connections after 5 mins idle
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
            }
        )

        # Open with timeout so we fail fast if Supabase is unreachable
        try:
            await self._pool.open(wait=True, timeout=30)
        except (asyncio.TimeoutError, Exception) as e:
            msg = f"[ GRAPH ] Database pool initialization failed: {e}. Falling back to MemorySaver (Ephemeral history)."
            if os.name == 'nt' and isinstance(e, asyncio.TimeoutError):
                msg += " On Windows, ensure you are using WindowsSelectorEventLoopPolicy()."
            logger.warning(msg)
            print(msg) # Ensure visibility in terminal

            # FALLBACK to MemorySaver
            self._checkpointer = SafeMemorySaver()
            return self._checkpointer

        # 4. Create the saver using the pool
        self._checkpointer = SafeAsyncPostgresSaver(self._pool)

        # Setup tables if they don't exist
        await self._checkpointer.setup()

        logger.info("[ GRAPH ] Checkpointer ready.")
        return self._checkpointer

    def _resolve_mcp_ready_event(self):
        """Find the MCP-ready event on the shared tool registry (GUI-path fallback).

        system_factory tags container.tool_registry with `mcp_ready_event`. The engine's
        tool_executor is either that registry or wraps it (.registry / .tools), so probe
        those in order. Returns None if no event is reachable (e.g. tests with no MCP).
        """
        for obj in (self.tool_executor,
                    getattr(self.tool_executor, "registry", None),
                    getattr(self.tool_executor, "tools", None)):
            event = getattr(obj, "mcp_ready_event", None)
            if event is not None:
                return event
        return None

    async def get_graph(self):
        """Compile the supervisor-led graph with checkpointing."""
        from src.shared.config_manager import ConfigManager
        disabled_agents = set(ConfigManager.get("disabled_subagents") or [])

        if self._compiled_graph:
            if getattr(self, "_compiled_disabled_agents", set()) == disabled_agents:
                return self._compiled_graph
            else:
                logger.info("[ GRAPH ] Disabled subagents preference changed. Recompiling supervisor graph...")
                self._compiled_graph = None

        logger.info("[ GRAPH ] Starting graph compilation...")

        # Do not bind subagent tool lists until MCP discovery has finished registering
        # its tools — otherwise MCP-dependent specialists (browser_agent → Playwright)
        # compile toolless and stay that way for the life of this cached graph.
        # The event may be passed in directly, but on the GUI path the TurnCoordinator that
        # built this engine never receives it — so we also fall back to the shared tool
        # registry, which system_factory tags with container.mcp_ready_event.
        mcp_ready_event = self.mcp_ready_event or self._resolve_mcp_ready_event()
        if mcp_ready_event is not None and not mcp_ready_event.is_set():
            logger.warning("[ GRAPH ] Waiting for MCP discovery before compiling specialist agents...")
            await mcp_ready_event.wait()
            logger.warning("[ GRAPH ] MCP discovery complete. Proceeding with compilation.")

        self._compiled_disabled_agents = disabled_agents.copy()
        checkpointer = await self._get_checkpointer()

        # 0. Initialize Skill Loader (ensure catalog is populated)
        from src.core.skill_system.skill_loader import get_skill_loader
        skill_loader = get_skill_loader()
        await skill_loader.initialize()

        # 1. Initialize Specialized Agent Team
        logger.info("[ GRAPH ] Initializing specialized agents...")

        # Determine tools to pass to agents
        agent_tools = None
        if self.tool_executor:
            if hasattr(self.tool_executor, 'get_langchain_tools_by_domain'):
                agent_tools = self.tool_executor
            elif hasattr(self.tool_executor, 'registry') and hasattr(self.tool_executor.registry, 'get_langchain_tools_by_domain'):
                agent_tools = self.tool_executor.registry
            elif hasattr(self.tool_executor, 'tools') and hasattr(self.tool_executor.tools, 'get_langchain_tools_by_domain'):
                agent_tools = self.tool_executor.tools

        if not agent_tools:
            logger.warning("[ GRAPH ] No tool_executor or tool_registry provided. Agents will have NO tools.")
            # We need an object that has get_langchain_tools_by_domain
            class DummyRegistry:
                def get_langchain_tools_by_domain(self, domain): return []
                def get_all_langchain_tools(self): return []
            agent_tools = DummyRegistry()

        agents_dict = create_specialist_agents(self.lc_model, agent_tools, checkpointer)
        logger.info(f"[ GRAPH ] {len(agents_dict)} agents initialized.")

        # 2. Compile Hierarchical Supervisor
        logger.info("[ GRAPH ] Creating supervisor...")
        from src.core.orchestration.supervisor import create_chad_supervisor
        self._compiled_graph = create_chad_supervisor(
            self.lc_model,
            agents_dict,
            agent_tools,
            checkpointer
        )

        logger.info(f"[ GRAPH ] Hierarchical Supervisor compiled with {len(agents_dict)} specialists.")
        return self._compiled_graph

    # --- UTILS ---

    def _to_chad_messages(self, messages: List[AnyMessage]) -> List[Dict[str, Any]]:
        """Convert LangChain messages to Chad's internal dict format."""
        return to_chad_messages(messages)

    @staticmethod
    def from_chad_history(history: List[Dict[str, Any]]) -> List[AnyMessage]:
        """Convert Chad's history dicts to LangChain messages."""
        return from_chad_history(history)

    def _to_chad_messages_with_lc(self, lc_messages: List[AnyMessage]) -> List[Dict[str, Any]]:
        """Internal helper for ChatChad to use if needed."""
        return self._to_chad_messages(lc_messages)

    async def clear_thread_state(self, thread_id: str) -> None:
        """
        Delete the LangGraph checkpoint for the given thread_id.

        Called during turn cancellation to prevent a dangling graph state
        (e.g., an AIMessage with pending tool_calls but no ToolMessage reply)
        from being resumed on the next user turn with the same session thread.

        Without this, stopping a turn mid-tool-call causes the model to "wake up"
        on the following turn because LangGraph loads the stale checkpoint and sees
        an unanswered tool invocation.
        """
        if not self._checkpointer:
            logger.debug(f"[ GRAPH ] No checkpointer active — nothing to clear for thread: {thread_id}")
            return

        try:
            if hasattr(self._checkpointer, "adelete_thread"):
                await self._checkpointer.adelete_thread(thread_id=thread_id)
                logger.info(f"[ GRAPH ] Async-deleted checkpoint for cancelled thread: {thread_id}")
            elif hasattr(self._checkpointer, "delete_thread"):
                self._checkpointer.delete_thread(thread_id=thread_id)
                logger.info(f"[ GRAPH ] Deleted checkpoint for cancelled thread: {thread_id}")
            else:
                logger.warning(
                    f"[ GRAPH ] Checkpointer does not support delete_thread — "
                    f"dangling state may remain for thread: {thread_id}"
                )
        except Exception as e:
            logger.warning(f"[ GRAPH ] Failed to clear thread state for {thread_id}: {e}")

    async def invoke(self, initial_state: ChadState, thread_id: Optional[str] = None) -> ChadState:
        """
        Run the graph with the given initial state and optional thread_id for persistence.
        """
        graph = await self.get_graph()

        # Ensure we have a thread_id for the checkpointer if one is active
        effective_thread_id = thread_id or initial_state.get("session_id")

        # Extract callbacks from state (they are not serializable so we move them to config)
        initial_state = initial_state.copy()
        on_token = initial_state.pop("on_token", None)
        on_thinking = initial_state.pop("on_thinking", None)
        on_usage = initial_state.pop("on_usage", None)

        # Hardening: initialize 'variables' if not present, and sanitize initial 'variables'
        if "variables" not in initial_state:
            initial_state["variables"] = {}
        else:
            initial_state["variables"] = pre_checkpoint_validator(initial_state["variables"])

        # Hardening: strip any top-level UntrackedValue objects from initial_state
        for k in list(initial_state.keys()):
            if isinstance(initial_state[k], UntrackedValue):
                logger.debug(f"[ VALIDATOR ] Stripping top-level UntrackedValue key '{k}' from initial_state.")
                initial_state.pop(k)

        # Ensure iteration_count is present
        if "iteration_count" not in initial_state:
            initial_state["iteration_count"] = 0

        # Set ContextVars for the duration of the graph execution
        t3 = agent_name_var.set("Supervisor")
        t_pending_approval = pending_approval_var.set(None)

        config: RunnableConfig = {}

        # Attach LangSmith metadata so every child run is filterable by session, platform, category.
        config["run_name"] = "ChadTurn"
        config["tags"] = ["chad-turn"]
        config["metadata"] = {
            "session_id": str(effective_thread_id or ""),
            "platform": str(initial_state.get("platform") or "unknown"),
            "category": str(initial_state.get("category") or "unknown"),
        }

        if effective_thread_id:
            config["configurable"] = {"thread_id": str(effective_thread_id)}
            from src.shared.config_manager import ConfigManager
            limit = ConfigManager.get("recursion_limit") or ConfigManager.get("orchestration.recursion_limit") or 100
            config["recursion_limit"] = limit
            logger.info(f"[ GRAPH ] Invoking with thread_id: {effective_thread_id} (recursion_limit: {limit})")
        elif self._checkpointer:
            # If we have a checkpointer but no thread_id, we MUST provide one or it will fail
            effective_thread_id = "default-thread"
            config["configurable"] = {"thread_id": effective_thread_id}
            logger.warning(f"[ GRAPH ] No thread_id provided but checkpointer is active. Using fallback: {effective_thread_id}")

        try:
            return await run_turn_stream(
                graph,
                config,
                initial_state,
                effective_thread_id,
                on_token=on_token,
                on_thinking=on_thinking,
                on_usage=on_usage,
            )
        finally:
            agent_name_var.reset(t3)
            pending_approval_var.reset(t_pending_approval)

    async def get_approval_resume_state(self, thread_id: str) -> Optional[dict]:
        """Read the saved call; no model/selector invocation is needed to resume."""
        from src.core.orchestration import approval_store
        if not approval_store.has_resolved_decision(thread_id):
            return None
        graph = await self.get_graph()
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        values = snapshot.values if snapshot else {}
        pending = values.get("pending_approval") or {}
        entry = approval_store.peek(pending.get("token"))
        if pending.get("tool_call") and entry and entry.get("resolved"):
            return values
        return None

    async def get_execution_resume_mode(self, thread_id: str, prompt: str) -> Optional[str]:
        """Short execution follow-ups inherit the task, not a sticky GUI lane."""
        if (prompt or "").strip().lower().rstrip(".! ") not in {
            "proceed", "continue", "keep going", "go ahead", "approved", "yes", "ok", "okay",
        }:
            return None
        if not thread_id:
            return None
        try:
            graph = await self.get_graph()
            snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
            state = snapshot.values if snapshot else {}
            pending = state.get("pending_approval") or {}
            unfinished = any(
                isinstance(s, dict) and s.get("status") in ("pending", "running", "failed")
                for s in state.get("active_plan") or []
            )
            if unfinished or state.get("turn_mode") == "agent" or (
                pending.get("tool_name") and pending["tool_name"] not in {"ask_user_question", "pause_for_input"}
            ):
                return "agent"
        except Exception as exc:
            logger.warning("[ GRAPH ] Could not inspect execution continuation: %s", type(exc).__name__)
        return None

    async def get_history(self, thread_id: str) -> List[AnyMessage]:
        """
        Retrieve previous message history directly from the graph checkpoint.
        Safely falls back to empty list if no history exists or an error occurs.
        """
        if not thread_id:
            return []

        try:
            graph = await self.get_graph()
            config = {"configurable": {"thread_id": str(thread_id)}}
            state = await graph.aget_state(config)
            if state and state.values:
                return state.values.get("messages", [])
            return []
        except Exception as e:
            logger.warning(f"[ GRAPH ] Failed to retrieve history for {thread_id}: {e}")
            return []

    async def get_checkpoint_tools(self, thread_id: str) -> List[Any]:
        """Return tool schemas persisted in the graph checkpoint for this thread."""
        if not thread_id:
            return []
        try:
            graph = await self.get_graph()
            config = {"configurable": {"thread_id": str(thread_id)}}
            state = await asyncio.wait_for(graph.aget_state(config), timeout=5.0)
            if state and state.values:
                tools = state.values.get("tools") or []
                if tools:
                    return list(tools)
            return []
        except Exception as e:
            logger.warning(f"[ GRAPH ] Failed to retrieve checkpoint tools for {thread_id}: {e}")
            return []

    async def clear_checkpoints(self, thread_id: str):
        """
        Delete all checkpoints for a specific thread/session.
        Uses the native checkpointer `adelete_thread` method to maintain consistency.
        """
        if not thread_id:
            return

        checkpointer = await self._get_checkpointer()
        if not checkpointer:
            logger.warning(f"[ GRAPH ] Cannot clear checkpoints for {thread_id} - no checkpointer active.")
            return

        logger.info(f"[ GRAPH ] Clearing checkpoints for thread {thread_id}...")
        try:
            await checkpointer.adelete_thread(thread_id)
            logger.info(f"[ GRAPH ] Successfully cleared checkpoints for thread {thread_id}.")
        except Exception as e:
            logger.error(f"[ GRAPH ] Failed to clear checkpoints for thread {thread_id}: {e}", exc_info=True)

    async def close(self):
        """Clean up the connection pool on shutdown. Call this when Chad exits."""
        if self._pool:
            await self._pool.close()
            logger.info("[ GRAPH ] Connection pool closed.")
