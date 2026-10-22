"""
System Factory
Handles the coordinated construction of core Assistant components,
ensuring dependency injection parity across GUI and Headless deployments.
"""

import os
import time
import asyncio
import logging
from typing import Dict, Optional, Any

from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.application.system_container import SystemContainer
from src.shared.startup_timing import checkpoint as _timing_checkpoint, record_duration as _timing_record

logger = setup_logger("SystemFactory")
logger.setLevel(logging.WARNING)

class SystemFactory:
    """
    Central authority for system initialization.
    Prevents architectural drift by consolidating dependency creation.
    """

    @staticmethod
    def build_core_engine(config: Dict, event_bus: Optional[Any] = None) -> SystemContainer:
        """
        Synchronously builds the fundamental non-blocking software engine.
        Guarantees proper injection of shared components like Memory into Routers and Tools.
        """
        logger.warning("[ FACTORY ] Building core non-blocking engine components...")
        
        # 1. EventBus Integration (Provided or New)
        if event_bus is None:
            from src.application.events import EventBus
            event_bus = EventBus()

        t0 = time.time()
        # 2. Tool Registry
        from src.infrastructure.tools import ToolRegistry
        tool_registry = ToolRegistry(event_bus=event_bus)
        with _timing_checkpoint("tool_registry_discover_all"):
            tool_registry.discover_all(config=config)
        logger.debug(f"ToolRegistry build: {time.time() - t0:.4f}s")

        t1 = time.time()
        # 3. Memory Engine
        from src.core.intelligence.memory_engine import MemoryEngine
        db_path = ConfigManager.get_memory_db_path()
        with _timing_checkpoint("memory_engine_construct"):
            memory_engine = MemoryEngine(db_path=db_path)
        logger.debug(f"MemoryEngine build: {time.time() - t1:.4f}s")

        # -> CRITICAL PARITY FIX: Ensure memory injected into registry tool
        mem_tool = tool_registry.get_tool("manage_user_memory")
        if mem_tool:
            # Tools are flexible, supports both dict and object access
            if hasattr(mem_tool, 'memory'):
                mem_tool.memory = memory_engine
            elif isinstance(mem_tool, dict):
                mem_tool['memory'] = memory_engine
            logger.debug(f"Injected MemoryEngine into tool '{mem_tool if isinstance(mem_tool, str) else 'manage_user_memory'}'")

        t2 = time.time()
        # 5. Model Router
        # -> CRITICAL PARITY FIX: Ensure MemoryEngine injected into Router ALWAYS
        from src.core.intelligence.model_router import ModelRouter
        with _timing_checkpoint("model_router_construct"):
            model_router = ModelRouter(
                config=config,
                tool_registry=tool_registry,
                memory_engine=memory_engine
            )
        logger.debug(f"ModelRouter build: {time.time() - t2:.4f}s")



        t4 = time.time()
        # 7. Shared AI Adapter (LiteLLM)
        adapter = None
        _adapter_t_start = time.perf_counter()
        try:
            from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
            
            if ConfigManager.is_local_model_active():
                local_cfg = ConfigManager.get_local_model_config()
                provider = local_cfg.get("provider", "lmstudio")
                provider_cfg = ConfigManager.get_local_provider_config(provider)
                
                model_name = local_cfg.get("name", "")
                if model_name and "/" not in model_name:
                    model_name = f"{provider}/{model_name}"

                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    base_url=local_cfg.get("base_url") or provider_cfg.get("base_url"),
                    api_key=local_cfg.get("api_key") or provider_cfg.get("api_key"),
                    system_instruction=config.get('system_instruction', '')
                )
            else:
                model_name = ConfigManager.get_active_model()
                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    api_key=os.getenv('GEMINI_API_KEY') if 'gemini' in model_name.lower() else None,
                    system_instruction=config.get('system_instruction', '')
                )

            if adapter:
                tool_registry._adapter = adapter
        except Exception as e:
            logger.error(f"Failed to create shared AI Adapter: {e}")
        _timing_record("ai_adapter_construct", time.perf_counter() - _adapter_t_start)
        logger.debug(f"Adapter build: {time.time() - t4:.4f}s")

        # 8. Lifted Software Orchestrators
        from src.core.utils.consolidation_manager import ConsolidationManager
        from src.core.tools.tool_executor import LocalToolExecutor
        from src.core.interfaces.text_mode import TextModeClient
        from src.core.orchestration.turn_coordinator import TurnCoordinator

        profile = ConfigManager.get("consolidation.profile", "balanced")
        consolidation_manager = ConsolidationManager(memory_engine=memory_engine, profile=profile)

        tool_executor = LocalToolExecutor(
            tool_registry=tool_registry,
            event_bus=event_bus,
            memory_engine=memory_engine
        )

        # Canonical execution loop
        turn_coordinator = TurnCoordinator(
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            memory_engine=memory_engine,
            event_bus=event_bus,
            consolidation_manager=consolidation_manager,
            default_adapter=adapter
        )

        text_mode_client = TextModeClient(
            adapter=adapter,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            memory_engine=memory_engine,
            event_bus=event_bus,
            turn_coordinator=turn_coordinator
        )
        text_mode_client.set_consolidation_manager(consolidation_manager)

        logger.warning("[ SUCCESS ] System core engine successfully constructed.")
        
        container = SystemContainer(
            event_bus=event_bus,
            tool_registry=tool_registry,
            memory_engine=memory_engine,
            model_router=model_router,
            adapter=adapter,
            tool_executor=tool_executor,
            consolidation_manager=consolidation_manager,
            text_mode_client=text_mode_client,
            turn_coordinator=turn_coordinator
        )
        if turn_coordinator:
            turn_coordinator.mcp_ready_event = container.mcp_ready_event
        return container

    @staticmethod
    async def warmup_async_infrastructure(container: SystemContainer, config: Dict) -> None:
        """
        Executes async resource warming (MCP pre-warm, Skill cataloging) concurrently.
        Modifies the provided container in-place.
        """
        logger.warning("[ FACTORY ] Warming async infrastructure (Skills, MCPs)...")
        t_start = time.time()
        
        from src.core.tools.mcp_registry import MCPRegistry
        from src.core.skill_system.skill_loader import get_skill_loader

        # 1. Setup Registries
        mcp_registry = MCPRegistry()
        skill_loader = get_skill_loader()

        # Register MCP servers — only enabled connectors with valid credentials
        from src.infrastructure.connectors.policy import get_effective_mcp_servers
        mcp_config = get_effective_mcp_servers()
        for name, cfg in mcp_config.items():
            env = cfg.get('env', {})
            resolved_env = {}
            for k, v in env.items():
                if isinstance(v, str):
                    # Handle ${VAR} format
                    if v.startswith('${') and v.endswith('}'):
                        resolved_env[k] = os.getenv(v[2:-1], '')
                    # Handle %VAR% format (Windows)
                    elif v.startswith('%') and v.endswith('%'):
                        resolved_env[k] = os.getenv(v[1:-1], '')
                    else:
                        resolved_env[k] = os.path.expandvars(v)
                else:
                    resolved_env[k] = v
            resolved_command = [
                os.path.expandvars(arg) if isinstance(arg, str) else arg
                for arg in cfg['command']
            ]
            mcp_registry.register(name, resolved_command, resolved_env, cfg)

        # 2. Execute Concurrent Warmup
        lifecycle = (config.get("local_models") or {}).get("lifecycle") or {}
        defer_llm_selector_warmup = bool(lifecycle.get("preload_on_connect"))

        async def warm_tool_selector():
            warmed = []
            _t = time.perf_counter()
            if hasattr(container.tool_registry, '_selector') and container.tool_registry._selector:
                logger.warning("[ FACTORY ] Warming tool selector semantic model in background...")
                try:
                    _t_sem = time.perf_counter()
                    await asyncio.to_thread(container.tool_registry._selector._ensure_model)
                    _timing_record("semantic_tool_selector_embedding_warmup_bg", time.perf_counter() - _t_sem)
                    warmed.append("semantic")
                except Exception as e:
                    logger.error(f"[ FACTORY ] Failed to pre-warm semantic tool selector: {e}")
            if (
                hasattr(container.tool_registry, '_llm_selector')
                and container.tool_registry._llm_selector
                and not defer_llm_selector_warmup
            ):
                logger.warning("[ FACTORY ] Warming LLM tool selector model in background...")
                try:
                    _t_llm = time.perf_counter()
                    await asyncio.to_thread(container.tool_registry._llm_selector.warmup)
                    _timing_record("llm_tool_selector_warmup_bg", time.perf_counter() - _t_llm)
                    warmed.append("llm")
                except Exception as e:
                    logger.error(f"[ FACTORY ] Failed to pre-warm LLM tool selector: {e}")
            elif defer_llm_selector_warmup:
                logger.info(
                    "[ FACTORY ] LLM tool-selector boot warmup deferred "
                    "(lifecycle preload_on_connect / preload_selector_in_agent)."
                )
            if warmed:
                logger.warning(f"[ FACTORY ] [ SUCCESS ] Tool selector pre-warmed: {', '.join(warmed)} in {time.perf_counter() - _t:.2f}s (background, not blocking ready signal).")

        async def warm_memory_embedding_model():
            if hasattr(container, 'memory_engine') and container.memory_engine:
                logger.warning("[ FACTORY ] Warming memory engine local embedding model in background...")
                try:
                    _t_mem = time.perf_counter()
                    mem = container.memory_engine
                    fn = getattr(
                        mem,
                        "_ensure_local_embedding_model",
                        getattr(getattr(mem, "_embeddings", None), "_ensure_local_embedding_model", None),
                    )
                    if fn:
                        await asyncio.to_thread(fn)
                        _timing_record("memory_embedding_model_warmup_bg", time.perf_counter() - _t_mem)
                        logger.warning("[ FACTORY ] [ SUCCESS ] Memory engine local embedding model pre-warmed.")
                except Exception as e:
                    logger.error(f"[ FACTORY ] Failed to pre-warm memory engine local embedding model: {e}")

        # NOTE: skill_loader / mcp_registry.prewarm_cache / turn_coordinator.warmup run
        # CONCURRENTLY via gather, so their individual durations below overlap with each
        # other — the gather's own wall-clock total (recorded as a block) is the real
        # cost to total startup time, not the sum of the three.
        _t_skill = {"dur": None}
        _t_mcp_prewarm = {"dur": None}
        _t_turn_warmup = {"dur": None}

        async def _timed_skill_init():
            t = time.perf_counter()
            try:
                await skill_loader.initialize()
            finally:
                _t_skill["dur"] = time.perf_counter() - t

        async def _timed_mcp_prewarm():
            t = time.perf_counter()
            try:
                await mcp_registry.prewarm_cache()
            finally:
                _t_mcp_prewarm["dur"] = time.perf_counter() - t

        async def _timed_turn_warmup():
            t = time.perf_counter()
            try:
                if container.turn_coordinator:
                    await container.turn_coordinator.warmup()
            finally:
                _t_turn_warmup["dur"] = time.perf_counter() - t

        try:
            with _timing_checkpoint("concurrent_gather_skill+mcp_prewarm+turncoord_total"):
                await asyncio.gather(
                    _timed_skill_init(),
                    _timed_mcp_prewarm(),
                    _timed_turn_warmup(),
                )
        except Exception as e:
            logger.error(f"[ FACTORY ] Exception during async infrastructure warmup: {e}")
        finally:
            if _t_skill["dur"] is not None:
                _timing_record("  skill_loader_initialize (overlapped)", _t_skill["dur"])
            if _t_mcp_prewarm["dur"] is not None:
                _timing_record("  mcp_registry_prewarm_cache (overlapped)", _t_mcp_prewarm["dur"])
            if _t_turn_warmup["dur"] is not None:
                _timing_record("  turn_coordinator_warmup_promptregistry (overlapped)", _t_turn_warmup["dur"])

        # Warm up embedding models in non-blocking background tasks
        asyncio.create_task(warm_tool_selector())
        asyncio.create_task(warm_memory_embedding_model())

        # 3. Inject and Merge
        container.mcp_registry = mcp_registry
        container.skill_loader = skill_loader
        # Inject mcp_registry reference for call_mcp_tool (cheap reference assignment,
        # doesn't need discovery to have completed)
        container.tool_registry.mcp_registry = mcp_registry
        # Expose the MCP-ready signal on the shared registry too. The registry is the ONE
        # object guaranteed to reach every orchestration path (GUI ConversationOrchestrator
        # builds its own TurnCoordinator that never receives mcp_ready_event directly), so
        # the LangGraph engine reads readiness from here before binding subagent tool lists.
        # Without this, browser_agent can compile before Playwright registers and be cached
        # toolless for the life of the graph. See LangGraphEngine.get_graph().
        container.tool_registry.mcp_ready_event = container.mcp_ready_event

        # MCP tool discovery (connect + list_tools per server) is, by measurement, the
        # single largest startup cost (~60% of total boot time with several servers
        # configured) — but the tools it finds are only needed for the FIRST turn that
        # actually calls one, not for the server to start accepting connections. Run it
        # as a background task instead of awaiting it here, so `initialization_ready_event`
        # (server ready / GUI can connect) fires immediately after this function returns.
        # `container.mcp_ready_event` (set at the end of this task) is the real gate —
        # TurnCoordinator already awaits it before dispatching any MCP tool call, so a
        # turn that needs MCP tools simply waits for discovery to finish instead of the
        # whole server being unreachable until discovery finishes.
        async def _discover_mcp_tools_background():
            try:
                with _timing_checkpoint("mcp_tool_discovery_into_registry"):
                    await container.tool_registry.discover_mcp_servers(mcp_registry)

                try:
                    with _timing_checkpoint("mcp_index_builder_build_all"):
                        from src.tools.system.mcp.mcp_index_builder import MCPIndexBuilder
                        from src.shared.paths import resolve_path
                        mcp_root = ConfigManager.get("mcp.schema_root", "mcp")
                        resolved_mcp_root = resolve_path(mcp_root)
                        builder = MCPIndexBuilder(
                            mcp_registry=mcp_registry,
                            tool_registry=container.tool_registry,
                            mcp_root=resolved_mcp_root
                        )
                        await builder.build_all()
                except Exception as build_err:
                    logger.error(f"[ FACTORY ] Failed to build MCP indexes: {build_err}", exc_info=True)

                # Propagate MCP registry into session manager if already attached
                if container.session_manager:
                    container.session_manager.mcp_registry = mcp_registry

                # Log categories for diagnostics
                try:
                    mcp_categories = [k for k in container.tool_registry._categories if container.tool_registry._categories[k]]
                    logger.warning(f"[ MCP ] Post-discovery categories with tools: {mcp_categories}")
                except Exception as log_err:
                    logger.debug(f"Failed to log mcp categories: {log_err}")
            finally:
                # Signal that async infrastructure warmup is fully complete
                container.mcp_ready_event.set()
                logger.warning("[ FACTORY ] [ SUCCESS ] MCP ready event signaled.")

        asyncio.create_task(_discover_mcp_tools_background())

        # 4. Resolve and wire the centralized ToolCoordinator (Action Seam)
        tool_coordinator = None
        if container.session_manager and hasattr(container.session_manager, 'tool_coordinator'):
            tool_coordinator = container.session_manager.tool_coordinator
        else:
            from src.core.orchestration.coordinators.tool_coordinator import ToolCoordinator
            tool_coordinator = ToolCoordinator(
                tool_registry=container.tool_registry,
                tool_executor=container.tool_executor,
                event_bus=container.event_bus
            )

        from src.core.intelligence.background_agents import BackgroundAgentManager
        container.background_agent_manager = BackgroundAgentManager(
            turn_coordinator=container.turn_coordinator,
            tool_coordinator=tool_coordinator,
            mcp_registry=container.mcp_registry,
            tool_registry=container.tool_registry,
            show_notification_callback=container.session_manager.show_notification if container.session_manager else None,
            on_complete_callback=None
        )



        # 5. Spin Up Continuous Background Data Ingestion Sync (Chrome History, Docs)
        if ConfigManager.get("daemons.ingestion_sync", False):
            try:
                from src.core.intelligence.ingestion.sync_manager import start_background_sync_loop
                # Spawn silent daemon to run periodic incremental personal index refreshes
                asyncio.create_task(
                    start_background_sync_loop(memory_engine=container.memory_engine)
                )
                logger.warning("[ FACTORY ] Spawning Background Ingestion Sync Loop (Chrome/Gmail/Drive)...")
            except Exception as daemon_fail:
                logger.error(f"[ FACTORY ] Failed to spin up background sync daemon: {daemon_fail}")
        else:
            logger.info("[ FACTORY ] Background Ingestion Sync Loop disabled (daemons.ingestion_sync=false).")

        logger.warning(f"[ SUCCESS ] Async infrastructure warmed in {time.time() - t_start:.2f}s.")
