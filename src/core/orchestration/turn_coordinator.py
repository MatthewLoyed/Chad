from __future__ import annotations

import os
import asyncio
from typing import Optional, Dict, Any, List, Callable, TYPE_CHECKING

from src.shared.logger import setup_logger
from src.shared.benchmark import timer

from src.core.orchestration.turn import (
    TurnHistoryLoader,
    TurnModeResolver,
    TurnToolSelector,
    TurnConsolidator,
    TurnStatusEmitter,
    TurnModelPreloader,
    TurnToolPreparation,
    metrics as turn_metrics,
)
from src.core.orchestration.turn.pipeline import (
    TurnPipeline,
    is_background_turn,
    is_eval_turn,
)
from src.core.orchestration.turn.scratch import (
    ScratchField,
    TurnScratch,
    begin_turn,
    current_scratch,
)
from src.application.events.events import (
    TokenUsageUpdate,
    TurnStartEvent,
    TurnCompleteEvent,
)

if TYPE_CHECKING:
    from src.core.orchestration.langgraph_engine import LangGraphEngine
    from src.core.orchestration.session_manager import SessionManager
    from src.shared.context import RequestContext
    from src.core.intelligence.adapters.ai_adapter_base import AIAdapter
    from src.infrastructure.tools import ToolRegistry
    from src.core.tools.tool_executor import LocalToolExecutor
    from src.core.intelligence.memory_engine import MemoryEngine
    from src.application.events.event_bus import EventBus
    from src.core.utils.consolidation_manager import ConsolidationManager

logger = setup_logger("TurnCoordinator")


# Categories eligible for the semantic response cache (#516) — deliberately narrow.
# Voice/planning/autonomous/system/eval turns are excluded: voice answers are meant
# to be re-generated fresh each time, planning turns emit structured plans (not a
# plain cacheable answer), and autonomous/system/eval turns aren't interactive
# repeats a user would notice a savings on.
_SEMANTIC_CACHE_CATEGORIES = {"chat", "agent", "conversation"}


# Re-exported: these classify a turn and are used by the pipeline. Kept importable
# from here because model_lifecycle and tests reference the old location.
_is_background_turn = is_background_turn
_is_eval_turn = is_eval_turn


class TurnCoordinator:
    """
    Canonical execution engine for Chad's ReAct loop.

    Orchestrates a single turn end-to-end: turn-mode routing, tool selection,
    prompt assembly, preflight consolidation, LangGraph invocation, telemetry,
    and persistence. The cohesive sub-concerns are delegated to focused
    collaborators in :mod:`src.core.orchestration.turn` (composition over
    inheritance), keeping this class a thin coordinator.
    """

    def __init__(
        self,
        tool_registry: Optional["ToolRegistry"] = None,
        tool_executor: Optional["LocalToolExecutor"] = None,
        memory_engine: Optional["MemoryEngine"] = None,
        event_bus: Optional["EventBus"] = None,
        consolidation_manager: Optional["ConsolidationManager"] = None,
        session_manager: Optional["SessionManager"] = None,
        model_coordinator: Optional[Any] = None,
        default_adapter: Optional["AIAdapter"] = None,
        mcp_ready_event: Optional[asyncio.Event] = None,
    ):
        # Robust initialization supporting direct injection or SessionManager extraction
        if session_manager:
            self.tool_registry = tool_registry or getattr(session_manager, 'tool_registry', getattr(session_manager, 'tools', None))
            self.tool_executor = tool_executor or getattr(session_manager, 'tool_executor', None)
            self.memory_engine = memory_engine or getattr(session_manager, 'memory_engine', getattr(session_manager, 'memory', None))
            self.event_bus = event_bus or getattr(session_manager, 'event_bus', None)
            self.session_manager = session_manager
            self.model_coordinator = model_coordinator or getattr(session_manager, 'model_coordinator', None)
        else:
            self.tool_registry = tool_registry
            self.tool_executor = tool_executor
            self.memory_engine = memory_engine
            self.event_bus = event_bus
            self.session_manager = None
            self.model_coordinator = model_coordinator

        self.consolidation_manager = consolidation_manager
        self.default_adapter = default_adapter
        self.mcp_ready_event = mcp_ready_event

        # Internal LangGraph state
        self._langgraph_engine: Optional[LangGraphEngine] = None
        self._chat_adapter: Optional["AIAdapter"] = None

        # Fallback for reads outside any turn (startup, unrelated task).
        self._detached_scratch = TurnScratch()

        # Near-repeat response cache (#516) — skips inference entirely for a
        # side-effect-free query that's a semantic repeat within the same session.
        from src.core.intelligence.semantic_cache import SemanticResponseCache
        self._semantic_cache = SemanticResponseCache()

        # Composed collaborators (single-responsibility helpers)
        self._history_loader = TurnHistoryLoader()
        self._mode_resolver = TurnModeResolver()
        self._tool_selector = TurnToolSelector(self.tool_registry)
        self._emitter = TurnStatusEmitter(self.event_bus, self.tool_registry)
        self._model_preloader = TurnModelPreloader(self._emitter)
        self._tool_prep = TurnToolPreparation(tool_registry=self.tool_registry)
        self._consolidator = TurnConsolidator(
            self.consolidation_manager, self.event_bus, self._history_loader
        )

    # Per-turn state, proxied to the in-flight turn's ContextVar scratch (see
    # turn/scratch.py). Names are unchanged because UIStateSyncer, evals/inprocess
    # and text_mode read them off this shared instance directly.
    last_turn_metrics = ScratchField("metrics")
    last_turn_thinking_events = ScratchField("thinking_events")
    _current_turn_mode = ScratchField("mode")
    _current_turn_is_simple = ScratchField("is_simple")
    _last_tool_selection = ScratchField("tool_selection")
    _last_bundled_skills = ScratchField("bundled_skills")
    _last_knowledge_boost = ScratchField("knowledge_boost")

    @property
    def _scratch(self) -> TurnScratch:
        return current_scratch() or self._detached_scratch

    async def warmup(self) -> None:
        """Warm up the prompt registry cache and intent router on startup."""
        logger.info("[ TURN ] Warming up prompt registry...")
        from src.infrastructure.repositories.prompt_registry import PromptRegistry
        await PromptRegistry.get_instance().warmup()
        await self._mode_resolver.warmup()

    async def dispatch(
        self,
        prompt: str,
        context: "RequestContext",
        adapter: Optional["AIAdapter"] = None,
        content_blocks: Optional[list] = None,
        image_base64: Optional[str] = None,
        category: Optional[str] = None,
        max_tool_calls: int = 20,
        on_token: Optional[Callable] = None,
        on_thinking: Optional[Callable] = None,
        thinking_level: str = "medium",
        metadata: Optional[Dict[str, Any]] = None,
        initiator: Optional[str] = None,
    ) -> str:
        """Consolidated entry point for executing a complete ReAct turn."""
        with timer("Turn Execution"):
            return await self._execute_turn_internal(
                prompt=prompt,
                context=context,
                adapter=adapter,
                content_blocks=content_blocks,
                image_base64=image_base64,
                category=category,
                max_tool_calls=max_tool_calls,
                on_token=on_token,
                on_thinking=on_thinking,
                thinking_level=thinking_level,
                metadata=metadata,
                initiator=initiator,
            )

    def _semantic_cache_eligible(
        self, category: Optional[str], content_blocks: Optional[list], image_base64: Optional[str]
    ) -> bool:
        """Gate for the near-repeat response cache (#516) — deliberately narrow: text-only
        chat/agent turns only, and only when the config toggle is on."""
        if category not in _SEMANTIC_CACHE_CATEGORIES or content_blocks or image_base64:
            return False
        from src.shared.config_manager import ConfigManager
        return bool(ConfigManager.get("cache.semantic_response_cache_enabled", True))

    def _complete_from_semantic_cache(
        self,
        cached_response: str,
        session_id: str,
        delivery: Optional[str],
        context: "RequestContext",
        on_token: Optional[Callable],
    ) -> str:
        """Short-circuit a turn with a cached near-repeat answer — publishes the same
        events a normal turn would (so UI/telemetry consumers see nothing unusual) but
        skips history load, tool selection, and model invocation entirely."""
        logger.info(f"[ TURN ] Semantic cache hit for session {session_id} — inference skipped.")
        self.last_turn_metrics = turn_metrics.blank_metrics()
        self.last_turn_metrics.update({"stop_reason": "task_completed", "duration": 0.0})
        if self.event_bus:
            self.event_bus.publish(TurnStartEvent(turn_id=session_id))
            self.event_bus.publish(TurnCompleteEvent(
                turn_id=session_id,
                response=cached_response,
                delivery=delivery,
                client_turn_id=context.turn_id,
            ))
            self.event_bus.publish(TokenUsageUpdate(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                stop_reason="task_completed",
            ))
        if on_token:
            try:
                on_token(cached_response)
            except Exception:
                pass
        return cached_response

    async def cancel_current_turn(self, session_id: str) -> None:
        """
        Cancel the current LangGraph turn for the given session.

        Deletes the graph checkpoint for ``session_id`` so that a dangling
        intermediate state (e.g., an AIMessage with pending tool_calls but
        no ToolMessage response) is not resumed on the next user turn.

        This is the fix for the "stop button wakeup" bug (#80): without this
        cleanup, stopping mid-tool-call leaves a stale checkpoint that causes
        LangGraph to re-invoke the model on the following request.
        """
        if self._langgraph_engine:
            await self._langgraph_engine.clear_thread_state(session_id)
        else:
            logger.debug("[ TURN ] cancel_current_turn called but no LangGraph engine is available.")

    async def _execute_turn_internal(
        self,
        prompt: str,
        context: "RequestContext",
        adapter: Optional["AIAdapter"] = None,
        content_blocks: Optional[list] = None,
        image_base64: Optional[str] = None,
        category: Optional[str] = None,
        max_tool_calls: int = 20,
        on_token: Optional[Callable] = None,
        on_thinking: Optional[Callable] = None,
        thinking_level: str = "medium",
        metadata: Optional[Dict[str, Any]] = None,
        initiator: Optional[str] = None,
    ) -> str:
        from src.core.orchestration.context import current_turn_tool_queries_var
        import uuid
        turn_id = context.turn_id if hasattr(context, "turn_id") and context.turn_id else str(uuid.uuid4())
        if hasattr(context, "turn_id") and not context.turn_id:
            context.turn_id = turn_id
        # Fresh per-turn state, isolated from any concurrent turn. Deliberately not
        # reset afterwards — that's what lets the awaiting caller read this turn's
        # metrics after dispatch returns. See turn/scratch.py.
        scratch = begin_turn()
        token = current_turn_tool_queries_var.set({
            "__query__": prompt,
            "__turn_id__": turn_id,
        })
        try:
            pipeline = TurnPipeline(
                self,
                scratch,
                prompt=prompt,
                context=context,
                adapter=adapter,
                content_blocks=content_blocks,
                image_base64=image_base64,
                category=category,
                max_tool_calls=max_tool_calls,
                on_token=on_token,
                on_thinking=on_thinking,
                thinking_level=thinking_level,
                metadata=metadata,
                initiator=initiator,
            )
            return await pipeline.run()
        finally:
            current_turn_tool_queries_var.reset(token)

    def _get_chat_adapter(self) -> Optional["AIAdapter"]:
        if os.getenv("CHAD_TESTING") == "true":
            # Dry-run/eval must never construct a real chat LiteLLMAdapter — that would
            # JIT-load the main chat model (Gemma 26B) on a SpyAdapter turn (#632).
            return None
        if self._chat_adapter is not None:
            return self._chat_adapter
        from src.shared.config_manager import ConfigManager
        from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
        cfg = ConfigManager.get_local_model_config("chat")
        if not cfg or not cfg.get("name"):
            return None
        self._chat_adapter = LiteLLMAdapter(
            model_name=cfg["name"],
            base_url=cfg.get("base_url"),
            api_key=cfg.get("api_key"),
            system_instruction="",
        )
        return self._chat_adapter





    async def _get_optimized_tools(
        self,
        user_input: str,
        category: str = 'autonomous',
        session_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        knowledge_boost: Optional[str] = None,
    ) -> List[Any]:
        """Resolve the per-turn tool menu, surfacing selection state on ``self``.

        Kept as a thin method (rather than calling the selector directly) so that
        the integration tests which monkeypatch ``_get_optimized_tools`` continue
        to intercept the live selection path.

        ``knowledge_boost`` should be prefetched by the caller (once per turn) so
        selection retries do not re-query the knowledge layer.
        """
        if knowledge_boost is None:
            knowledge_boost = await self._fetch_knowledge_for_selection(
                user_input, history=history, category=category,
            )
        self._last_knowledge_boost = knowledge_boost or ""


        selection = await self._tool_selector.select(
            user_input,
            category=category,
            session_id=session_id,
            langgraph_engine=self._langgraph_engine,
            history=history,
            knowledge_context=knowledge_boost or None,
        )
        self._current_turn_is_simple = selection.is_simple
        self._last_tool_selection = selection.selection
        self._last_bundled_skills = selection.bundled_skills
        schemas = selection.schemas

        # Local models render the bound tool schemas right after the system prompt,
        # before conversation history — a set that varies turn-to-turn invalidates the
        # KV-cache prefix for the entire history that follows on every single turn
        # (#38). Cloud providers use server-side prompt caching and aren't affected the
        # same way, so only stabilize when a local model is actually active.
        if session_id:
            from src.shared.config_manager import ConfigManager
            if ConfigManager.is_local_model_active():
                schemas = self._tool_prep.stabilize_local_tools(session_id, schemas)

        return schemas

    async def _fetch_knowledge_for_selection(
        self,
        user_input: str,
        *,
        history: Optional[List[Dict[str, Any]]] = None,
        category: str = "autonomous",
    ) -> str:
        """Delegate to :class:`TurnToolPreparation`, supplying the current turn mode.

        Retained as a method because the turn pipeline prefetches through it and unit
        tests stub it out here.
        """
        return await self._tool_prep.fetch_knowledge(
            user_input,
            history=history,
            category=category,
            turn_mode=self._current_turn_mode,
        )

    async def _preload_main_model(self, adapter, delivery: Optional[str] = None) -> None:
        """Delegate to :class:`TurnModelPreloader` (see #592).

        Retained as a method because several tests patch ``_preload_main_model`` on the
        coordinator to keep a dry-run turn from touching LM Studio.
        """
        await self._model_preloader.preload(adapter, delivery)

    async def _adapter_for_category(self, category: str, fallback: "AIAdapter | None" = None):
        mc = self.model_coordinator
        if not mc and self.session_manager:
            mc = getattr(self.session_manager, "model_coordinator", None)
        if mc:
            try:
                model = await mc.select_model(category=category)
                if model:
                    return model
            except Exception as e:
                logger.warning(f"[ TURN ] Could not select model for {category}: {e}")
        return fallback

    async def _run_compaction(self, session_id: str, history: List[Dict[str, Any]], adapter: "AIAdapter") -> bool:
        """Delegate to the consolidator. Retained on the coordinator because a unit
        test invokes it directly with the engine set on ``self``."""
        return await self._consolidator.run_compaction(
            session_id, history, adapter, self._langgraph_engine
        )

    async def force_compaction(self, session_id: str, adapter: "AIAdapter") -> bool:
        """Manually force compaction for a session."""
        return await self._consolidator.force(session_id, adapter, self._langgraph_engine)
