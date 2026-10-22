"""
Conversation Orchestrator - High-Level Turn Management
Extracted from SessionManager to enable clean, agent-centric conversation orchestration.

This orchestrator handles:
- Turn lifecycle management (start, process, finalize)
- Coordination between Model, Tool, and State coordinators
- Response routing and processing
- System-initiated turns (for future cron/heartbeat features)
- Turn completion logic

Design Philosophy:
- Cloud-first: Designed for always-on AI agent with autonomous capabilities
- Event-driven: Publishes events for all significant turn milestones
- Coordinator-based: Delegates to specialized coordinators for execution
- Testable: All dependencies injected, no global state
"""

import asyncio
from contextlib import AsyncExitStack
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from enum import Enum

from src.shared.logger import setup_logger
from src.shared.context import RequestContext
from src.shared.exceptions import ModelError
from langgraph.errors import NodeTimeoutError
from src.core.orchestration.coordinators import ToolCoordinator, ModelCoordinator
from src.core.orchestration.state_coordinator import StateCoordinator
from src.core.intelligence.memory_engine import MemoryEngine
from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.intelligence.reply_parser import get_reply_parser, ParsedResponse, ReplyDirective
from src.core.orchestration.hooks_manager import get_hooks_manager, HookContext, HookType
from src.core.orchestration.conversation import (
    DirectiveProcessor,
    AutonomousActivityReporter,
    SessionLockManager,
    TurnResponseRouter,
    TurnConcurrencyGate,
)

from src.core.orchestration.conversation.directive_processor import handle_learn_directive
from src.application.events import (
    EventBus,
    TurnStarted,
    TurnCompleted,
    ErrorOccurred,
    ResponseFragment,
    ModelLoading,
    TokenUsageUpdate,
)
from src.core.orchestration.langgraph_engine import LangGraphEngine, ChadState
from src.core.orchestration.turn_coordinator import TurnCoordinator
from src.shared.action_scratchpad import ActionScratchpadMixin
from src.core.orchestration.context import agent_name_var, parent_correlation_id_var
from src.shared.config_manager import ConfigManager

logger = setup_logger("ConversationOrchestrator")


class TurnInitiator(Enum):
    """Who/what initiated this turn"""
    USER = "user"              # User input (text or voice)
    SYSTEM = "system"          # System-initiated (cron, heartbeat)
    SKILL = "skill"            # Autonomous skill trigger
    FOLLOWUP = "followup"      # Model requested followup
    WEBHOOK = "webhook"        # External webhook trigger


@dataclass
class TurnContext:
    """Context for a single conversation turn"""
    turn_id: str
    initiator: TurnInitiator
    category: str
    user_input: Optional[str] = None
    platform: str = "unknown"
    session_key: Optional[str] = None  # OpenClaw pattern: scope:account:channel:peer
    delivery: Optional[str] = None
    start_time: float = 0.0
    tool_count: int = 0
    tools_executed: List[str] = None
    model_response: Optional[str] = None
    parsed_response: Optional[ParsedResponse] = None  # Parsed reply directives (Phase 2)
    content_blocks: Optional[List[Dict[str, Any]]] = None
    image_base64: Optional[str] = None
    audio_played: bool = False
    completed: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: Optional[int] = None
    context_window: Optional[int] = None
    time_to_first_token: Optional[float] = None
    stop_reason: Optional[str] = None
    speed: Optional[float] = None

    def __post_init__(self):
        if self.tools_executed is None:
            self.tools_executed = []
        if self.start_time == 0.0:
            self.start_time = time.time()
        if self.session_key is None:
            # Default to main GUI session
            from src.core.intelligence.session_key_manager import SessionKeyManager
            self.session_key = SessionKeyManager.DEFAULT_SESSION_KEY


class ConversationOrchestrator(ActionScratchpadMixin):
    """
    Orchestrates high-level conversation flow and turn management.

    Design principles:
    - Coordinates between specialized coordinators (Model, Tool, State)
    - Event-driven communication (publishes turn lifecycle events)
    - Supports both user-initiated and system-initiated turns
    - Clean separation: No direct model/tool/audio access
    - Future-ready: Designed for cron jobs, heartbeats, autonomous skills
    """

    def __init__(
        self,
        model_coordinator: ModelCoordinator,
        tool_coordinator: ToolCoordinator,
        state_coordinator: StateCoordinator,
        memory_engine: MemoryEngine,
        event_bus: EventBus,
        config: Dict[str, Any]
    ):
        """
        Initialize ConversationOrchestrator.

        Args:
            model_coordinator: Handles model selection and lifecycle
            tool_coordinator: Handles tool execution
            state_coordinator: Manages conversation state machine
            memory_engine: Memory service for context retrieval
            event_bus: Event bus for publishing turn events
            config: Configuration dictionary
        """
        super().__init__()
        self.model_coord = model_coordinator
        self.tool_coord = tool_coordinator
        self.state_coord = state_coordinator
        self.memory = memory_engine
        self.event_bus = event_bus
        self.config = config

        # Instantiate centralized TurnCoordinator for execution delegation
        self.turn_coord = TurnCoordinator(
            tool_registry=self.tool_coord.tools,
            tool_executor=self.tool_coord.executor,
            memory_engine=self.memory,
            event_bus=self.event_bus,
            model_coordinator=self.model_coord,
        )

        # Per-session turn state (issue #208 concurrency).
        # A single orchestrator instance serves concurrent turns that run in parallel
        # when they have different session ids (a user GUI turn vs a cron/heartbeat/
        # webhook autonomous turn — the per-session lock below only serialises turns
        # that share a lock_key). If turn state lived in single instance slots, a
        # background turn starting mid-flight would clobber the user turn's context:
        # finalize_turn would read the wrong current_turn and misroute (or drop) the
        # user's reply, memory would cross-contaminate, and a stop would cancel the
        # wrong task. So every per-turn slot is keyed by the turn's session id,
        # resolved from session_id_var (pinned by process_request). asyncio tasks copy
        # the contextvar snapshot at creation, so each turn's coroutine resolves its
        # own bucket — exactly how UIStateSyncer buckets its broadcast state.
        self._turn_counter: int = 0
        self._turns_by_session: Dict[str, TurnContext] = {}
        self._pending_calls_by_session: Dict[str, List[Dict[str, Any]]] = {}
        self._pending_responses_by_session: Dict[str, List[Dict[str, Any]]] = {}
        self._user_transcript_by_session: Dict[str, str] = {}
        self._model_transcript_by_session: Dict[str, str] = {}
        # Active turn task reference — stored so cancel_turn() can cancel the streaming coroutine
        self._active_task_by_session: Dict[str, asyncio.Task] = {}

        # Concurrency Control (per-session serialisation + background priority gate)
        self._session_lock_mgr = SessionLockManager()
        self._concurrency_gate = TurnConcurrencyGate()


        # Reply directive parser (Phase 2 - OpenClaw pattern)
        self._reply_parser = get_reply_parser()

        # Directive dispatch — registered handlers live in DirectiveProcessor
        self._directive_proc = DirectiveProcessor()

        # Autonomous-turn observability (run log + dashboard feed)
        self._activity_reporter = AutonomousActivityReporter()

        # Response-type routing (audio/tool_call/tool_call_batch/turn_complete/thinking)
        self._response_router = TurnResponseRouter(
            tool_coord=self.tool_coord,
            state_coord=self.state_coord,
            event_bus=self.event_bus,
        )

        # Hooks manager (Phase 2 - OpenClaw pattern)
        self._hooks_manager = get_hooks_manager()

        # Register default directive handlers
        self._directive_proc.register("learn", handle_learn_directive)

        self._current_turn_is_simple = False
        
        # Local Adapter Persistence (for fast local model switching)
        self._local_adapter = None
        self._local_adapter_config = None # (model_name, base_url, api_key)
        self._local_adapter_lock = asyncio.Lock()

        # Strong refs for fire-and-forget background tasks (e.g. memory persistence) —
        # without this, asyncio can GC a task mid-flight once finalize_turn returns.
        self._background_tasks: set = set()

        logger.debug("ConversationOrchestrator initialized")

    @property
    def hooks_manager(self):
        """The shared HooksManager. Register/unregister lifecycle hooks directly on this."""
        return self._hooks_manager

    # ------------------------------------------------------------------
    # Per-session turn state (issue #208). All accessors resolve the bucket
    # from session_id_var, which process_request pins per turn. Because
    # contextvars are task-local, each concurrent turn reads/writes its own
    # slot instead of racing on one shared attribute.
    # ------------------------------------------------------------------

    @staticmethod
    def _session_now() -> str:
        """Session id of the turn whose code is currently executing."""
        from src.core.orchestration.context import session_id_var
        return session_id_var.get() or "default"

    @property
    def current_turn(self) -> Optional[TurnContext]:
        return self._turns_by_session.get(self._session_now())

    @current_turn.setter
    def current_turn(self, value: Optional[TurnContext]) -> None:
        sid = self._session_now()
        if value is None:
            self._turns_by_session.pop(sid, None)
        else:
            self._turns_by_session[sid] = value

    @property
    def _pending_tool_calls(self) -> List[Dict[str, Any]]:
        return self._pending_calls_by_session.setdefault(self._session_now(), [])

    @property
    def _pending_tool_responses(self) -> List[Dict[str, Any]]:
        return self._pending_responses_by_session.setdefault(self._session_now(), [])

    @property
    def _current_user_transcript(self) -> Optional[str]:
        return self._user_transcript_by_session.get(self._session_now())

    @_current_user_transcript.setter
    def _current_user_transcript(self, value: Optional[str]) -> None:
        sid = self._session_now()
        if value is None:
            self._user_transcript_by_session.pop(sid, None)
        else:
            self._user_transcript_by_session[sid] = value

    @property
    def _current_model_transcript(self) -> Optional[str]:
        return self._model_transcript_by_session.get(self._session_now())

    @_current_model_transcript.setter
    def _current_model_transcript(self, value: Optional[str]) -> None:
        sid = self._session_now()
        if value is None:
            self._model_transcript_by_session.pop(sid, None)
        else:
            self._model_transcript_by_session[sid] = value

    @property
    def _active_turn_task(self) -> Optional[asyncio.Task]:
        return self._active_task_by_session.get(self._session_now())

    @_active_turn_task.setter
    def _active_turn_task(self, value: Optional[asyncio.Task]) -> None:
        sid = self._session_now()
        if value is None:
            self._active_task_by_session.pop(sid, None)
        else:
            self._active_task_by_session[sid] = value

    def _fire_and_forget(self, coro, label: str) -> None:
        """Schedule `coro` without blocking the caller; failures are logged, never raised."""
        async def _runner():
            try:
                await coro
            except Exception as e:
                logger.error(f"[ ORCHESTRATOR ] Background {label} failed: {e}", exc_info=True)

        task = asyncio.ensure_future(_runner())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _is_turn_complete(self, text: str) -> bool:
        """
        Analyze model response for explicit completion signals.
        Supports mandatory final states via robust regex.
        """
        if not text:
            return False
            
        import re
        
        # 1. Check for explicit keywords (Preferred)
        # Matches TASK_COMPLETED, TASK_FAILED, NEEDS_HUMAN as whole words anywhere.
        # Also matches TASK as a whole word at the very end (allowing for terminal punctuation).
        patterns = [
            r"\bTASK_COMPLETED\b",
            r"\bTASK_FAILED\b",
            r"\bNEEDS_HUMAN\b"
        ]
        
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                logger.info(f"[ ORCHESTRATOR ] Termination signal matching '{pattern}' detected")
                return True
            
        # 2. Check for [[done]] directive via parser (Backup)
        parsed = self._reply_parser.parse(text)[1]
        if any(d.directive_type.value == "done" for d in parsed):
            logger.info("[ ORCHESTRATOR ] Termination signal [[done]] detected")
            return True
            
        return False


    async def process_request(
        self,
        prompt: str,
        category: str = 'agent',
        initiator: TurnInitiator = TurnInitiator.USER,
        context: Optional[RequestContext] = None,
        content_blocks: Optional[list] = None,
        image_base64: Optional[str] = None
    ) -> str:
        """
        Process a user request using the unified LiteLLM orchestrator architecture.

        Automatically routes to the correct model (Cloud or Local) via the
        LiteLLMAdapter, ensuring a consistent interface for all turn processing.

        Args:
            prompt: User's input text
            category: Turn category (autonomous, chat, web, system)
            initiator: Who initiated the turn
            context: Request context (session, platform)

        Returns:
            Model's final text response
        """
        if not context:
            context = RequestContext(platform="unknown")

        category = category or 'agent'
        from src.core.monitoring.daemon_status import daemons_master_enabled
        if initiator in (TurnInitiator.SYSTEM, TurnInitiator.SKILL, TurnInitiator.WEBHOOK) and not daemons_master_enabled():
            return ""
        delivery = getattr(context, "delivery", None)
        if not delivery:
            from src.shared.turn_delivery import resolve_delivery
            delivery = resolve_delivery(category)
            context.delivery = delivery
        logger.info(f"[ ORCHESTRATOR ] Processing {category} request (delivery={delivery})")

        # Eval harness turns (platform="eval") own the machine in development: they run at
        # interactive model-lifecycle priority, and while any are in flight every other
        # turn waits here so desktop/cron cannot race them for VRAM.
        from src.core.orchestration.eval_exclusivity import eval_turn_guard
        async with eval_turn_guard(context.platform):
            return await self._process_request_locked(
                prompt, category, initiator, context, content_blocks, image_base64, delivery,
            )

    async def _process_request_locked(
        self,
        prompt: str,
        category: str,
        initiator: TurnInitiator,
        context: RequestContext,
        content_blocks: Optional[list],
        image_base64: Optional[str],
        delivery: str,
    ) -> str:
        # 1. Start Turn via State Machine
        session_id = context.session_id or "default"
        # Pin the session id for this turn's context so every downstream stream event
        # (published synchronously within this task's contextvars) can be bucketed by
        # (session_id, delivery) in LimbCoordinator. This is what keeps a concurrent
        # autonomous turn (cron/heartbeat/webhook — a distinct session_id) from sharing
        # broadcast state with an active user turn (issue #208).
        from src.core.orchestration.context import session_id_var, domain_var
        session_id_var.set(session_id)
        # Domain-agent scope for every read AND write in this turn. Bound here rather than
        # only at the websocket handler so non-GUI lanes (voice, cron, eval) are scoped too —
        # they reach this method without passing through limb_message_handler. Turns that
        # never set a domain get None, i.e. the general tier, which is the pre-feature
        # behaviour. NOTE: `category` above is the turn lane and is unrelated.
        from src.shared.domain_scope import normalize_domain
        turn_domain = normalize_domain(getattr(context, "domain", None))
        domain_var.set(turn_domain)
        from src.shared.turn_delivery import lock_key
        lock = self._session_lock_mgr.get(lock_key(session_id, delivery))
        
        # Queue the request if another turn is active for this session
        if lock.locked():
            logger.info(f"[ ORCHESTRATOR ] Queuing request for session {session_id} (another turn is active)")

        is_background = (
            delivery != "gui" and (
                category in ("autonomous", "system")
                or initiator in (TurnInitiator.SYSTEM, TurnInitiator.SKILL, TurnInitiator.WEBHOOK)
            )
        )
        is_local = ConfigManager.is_local_model_active()

        async with AsyncExitStack() as stack:
            # Concurrency priority gating:
            # Background turns (webhooks, cron, heartbeat) must queue behind active user turns.
            # Local model turns enforce single-flight mutual exclusion to protect GPU/LM Studio.
            await stack.enter_async_context(
                self._concurrency_gate.turn_guard(
                    is_background=is_background,
                    is_local=is_local,
                    session_id=session_id,
                )
            )
            await stack.enter_async_context(lock)

            # A background request may have queued while actions were enabled.
            # Recheck after waiting, before it can acquire the model or run tools.
            from src.core.monitoring.daemon_status import daemons_master_enabled
            if initiator in (TurnInitiator.SYSTEM, TurnInitiator.SKILL, TurnInitiator.WEBHOOK) and not daemons_master_enabled():
                return ""

            self._active_turn_task = asyncio.current_task()

            try:
                # [ DEBUG ] Log incoming request parameters
                logger.info(f"[ ORCHESTRATOR ] -> PROMPT: '{prompt[:100] if prompt else 'None'}' (len={len(prompt) if prompt else 0})")
                logger.info(
                    f"[ ORCHESTRATOR ] -> CATEGORY: {category}, SESSION: {session_id}, "
                    f"DOMAIN: {turn_domain or 'general'}"
                )

                turn = await self.start_turn(
                    user_input=prompt,
                    category=category,
                    initiator=initiator,
                    session_key=context.session_id,
                    platform=context.platform,
                    content_blocks=content_blocks,
                    image_base64=image_base64,
                    delivery=delivery,
                )
                
                self._reset_scratchpad() # Reset at start of turn

                # Seed the memory engine's fallback session scope. The authoritative
                # per-turn scope now rides session_id_var (set above), which MemoryEngine
                # .session_key prefers — so concurrent turns no longer race on this shared
                # attribute during retrieval. This assignment just covers reads that happen
                # outside the turn's contextvar.
                self.memory.session_key = session_id

                # 2. Select Model for this turn
                is_cached = self.model_coord.router.is_model_cached(category)
                is_local = ConfigManager.is_local_model_active()
                
                if not is_cached and is_local:
                    logger.info(f"[ ORCHESTRATOR ] Model for category '{category}' not cached. Transitioning to model_loading.")
                    await self.state_coord.transition_to('model_loading')
                    model_info = ConfigManager.get_local_model_config(category)
                    from src.application.events import ModelLoading
                    from src.core.orchestration.context import current_turn_delivery
                    self.event_bus.publish(ModelLoading(
                        model_name=model_info.get('name', 'Unknown'),
                        provider=model_info.get('provider', 'local'),
                        delivery=delivery,
                    ))

                model_instance = await self.model_coord.select_model(category=category)
                if not model_instance:
                    raise RuntimeError(f"No model available for {category} execution")
                
                # Transition back to thinking if we were loading
                if self.state_coord.current_state == 'model_loading':
                    await self.state_coord.transition_to('thinking')

                logger.info(f"[ ORCHESTRATOR ] Selected model for turn: {model_instance.model_name}")

                # 3. Dispatch execution to centralized TurnCoordinator
                final_text = await self.turn_coord.dispatch(
                    prompt=prompt,
                    context=context,
                    adapter=model_instance,
                    content_blocks=content_blocks,
                    image_base64=image_base64,
                    category=category,
                    initiator=initiator.value if hasattr(initiator, "value") else initiator
                )

                # 4. Finalize Turn
                await self.finalize_turn(model_response=final_text)
                return final_text or "Task completed successfully."

            except asyncio.CancelledError:
                logger.warning("[ ORCHESTRATOR ] Turn cancelled by user (CancelledError propagated from streaming task)")
                raise
            except ModelError as me:
                logger.warning(f"[ ORCHESTRATOR ] ModelError during execution: {me}")
                # Transition to ready state if we failed
                await self.state_coord.transition_to("ready")
                # Ensure turn is cleaned up even on failure
                await self.cancel_turn(reason=str(me))
                error_str = str(me)
                if "Cannot reach LM Studio" in error_str:
                    return "⚠️ LM Studio is not running. Please make sure LM Studio is open and the local server is enabled (usually at http://localhost:1234)."
                elif "LM Studio model" in error_str and "is NOT loaded" in error_str:
                    return f"⚠️ {error_str.replace('[ ROUTER ] ', '')}"
                return f"⚠️ Model Error: {error_str.replace('[ ROUTER ] ', '')}"
            except NodeTimeoutError as te:
                logger.warning(
                    f"[ ORCHESTRATOR ] Node '{te.node}' timed out ({te.kind}, {te.elapsed:.1f}s)"
                )
                await self.state_coord.transition_to("ready")
                await self.cancel_turn(reason=str(te))
                if te.kind == "idle":
                    return (
                        f"⚠️ This step took too long without a response (over {te.timeout:.0f}s idle). "
                        "This can happen during large code generation when the model is still working. "
                        "Try breaking the request into smaller tasks, or retry."
                    )
                return (
                    f"⚠️ This step exceeded the maximum allowed time ({te.timeout:.0f}s). "
                    "Try breaking the request into smaller tasks."
                )
            except Exception as e:
                logger.error(f"[ ORCHESTRATOR ] Autonomous execution failed: {e}", exc_info=True)
                # Transition to ready state if we failed
                await self.state_coord.transition_to("ready")
                # Ensure turn is cleaned up even on failure
                await self.cancel_turn(reason=str(e))
                return f"⚠️ I encountered an error during execution: {str(e)}"
            finally:
                self._active_turn_task = None


    async def start_turn(
        self,
        user_input: Optional[str] = None,
        category: str = 'chat',
        initiator: TurnInitiator = TurnInitiator.USER,
        session_key: Optional[str] = None,
        platform: str = "unknown",
        content_blocks: Optional[list] = None,
        image_base64: Optional[str] = None,
        delivery: Optional[str] = None,
    ) -> TurnContext:
        """
        Start a new conversation turn.

        This is the main entry point for beginning a turn, whether initiated by:
        - User input (text or voice)
        - System events (cron jobs, heartbeats)
        - Autonomous skills
        - Model-requested followups

        Args:
            user_input: User's input text (None for system-initiated turns)
            category: Category for model routing (chat, web, system, etc.)
            initiator: Who/what initiated this turn
            intent: Optional pre-classified intent
            session_key: Session key for conversation isolation (format: scope:account:channel:peer)

        Returns:
            TurnContext object for this turn
        """
        # Generate turn ID
        self._turn_counter += 1
        turn_id = f"turn_{self._turn_counter}_{int(time.time() * 1000)}"

        # Trigger before_turn_start hook (Phase 2 - OpenClaw pattern)
        try:
            hook_context = HookContext(
                hook_type=HookType.BEFORE_TURN_START,
                turn_id=turn_id,
                initiator=initiator.value,
                session_key=session_key or "default",
                category=category,
                user_input=user_input
            )
            hook_context = await self._hooks_manager.trigger(HookType.BEFORE_TURN_START, hook_context)

            # Update parameters from hook context (hooks can modify)
            category = hook_context.category
            user_input = hook_context.user_input
        except Exception as e:
            logger.error(f"before_turn_start hook failed: {e}", exc_info=True)

        from src.shared.turn_delivery import resolve_delivery as _resolve_delivery
        _delivery = delivery or _resolve_delivery(category)

        # Create turn context
        self.current_turn = TurnContext(
            turn_id=turn_id,
            initiator=initiator,
            category=category,
            user_input=user_input,
            session_key=session_key,
            platform=platform,
            content_blocks=content_blocks,
            image_base64=image_base64,
            delivery=_delivery,
        )

        logger.info(
            f"[ ORCHESTRATOR ] Starting turn {turn_id} "
            f"(initiator: {initiator.value}, category: {category}, delivery: {_delivery})"
        )

        # Reset coordinator turn tracking
        self.tool_coord.reset_turn()

        # Store user transcript for memory
        if user_input:
            attachment_summaries = []
            if content_blocks:
                for block in content_blocks:
                    b_type = block.get('type')
                    if b_type == 'image':
                        attachment_summaries.append("[Image Attached]")
                    elif b_type == 'document':
                        mime = block.get('source', {}).get('media_type', 'unknown')
                        attachment_summaries.append(f"[Document Attached: {mime}]")
            elif image_base64:
                attachment_summaries.append("[Image Attached]")

            pruned_user_text = (
                f"{user_input}\n\nAttachments: {' '.join(attachment_summaries)}"
                if attachment_summaries else user_input
            )
            self._current_user_transcript = pruned_user_text

        # Transition to appropriate state
        if user_input:
            await self.state_coord.transition_to("thinking")
        else:
            await self.state_coord.transition_to("listening")

        # Publish turn started event
        self.event_bus.publish(TurnStarted(
            turn_id=turn_id,
            initiator=initiator.value,
            category=category,
            user_input=user_input,
            delivery=_delivery,
        ))

        # Trigger after_turn_start hook (Phase 2 - OpenClaw pattern)
        try:
            hook_context = HookContext(
                hook_type=HookType.AFTER_TURN_START,
                turn_id=turn_id,
                initiator=initiator.value,
                session_key=self.current_turn.session_key,
                category=category,
                user_input=user_input
            )
            await self._hooks_manager.trigger(HookType.AFTER_TURN_START, hook_context)
        except Exception as e:
            logger.error(f"after_turn_start hook failed: {e}", exc_info=True)

        logger.info(f"[ ORCHESTRATOR ] Turn {turn_id} started successfully")
        return self.current_turn

    async def process_response(self, response: Dict[str, Any]) -> None:
        """
        Process a response from the model.

        Routes the response to appropriate handlers based on type:
        - audio: Model speech output
        - tool_call/tool_call_batch: Tool execution requests
        - turn_complete: Turn finalization
        - thinking/reasoning: Intermediate processing updates

        Args:
            response: Response dictionary from model
        """
        if not self.current_turn:
            logger.warning("[ ORCHESTRATOR ] Received response but no active turn")
            return

        transcript = await self._response_router.route(
            response, self.current_turn, self._pending_tool_calls, self._pending_tool_responses
        )
        if transcript:
            self._current_model_transcript = transcript

    async def finalize_turn(self, model_response: Optional[str] = None) -> None:
        """
        Finalize the current turn.

        Performs cleanup:
        - Store turn in memory (user input + model response + tools)
        - Reset turn tracking
        - Publish turn completed event
        - Transition state back to ready

        Args:
            model_response: Final model response text (for memory storage)
        """
        if not self.current_turn:
            logger.warning("[ ORCHESTRATOR ] finalize_turn called but no active turn")
            return

        turn = self.current_turn
        turn.completed = True
        turn.model_response = model_response

        # Trigger before_turn_complete hook (Phase 2 - OpenClaw pattern)
        try:
            hook_context = HookContext(
                hook_type=HookType.BEFORE_TURN_COMPLETE,
                turn_id=turn.turn_id,
                initiator=turn.initiator.value,
                session_key=turn.session_key,
                category=turn.category,
                user_input=turn.user_input,
                metadata={'model_response': model_response, 'tool_count': turn.tool_count}
            )
            await self._hooks_manager.trigger(HookType.BEFORE_TURN_COMPLETE, hook_context)
        except Exception as e:
            logger.error(f"before_turn_complete hook failed: {e}", exc_info=True)

        # Parse reply directives (Phase 2 - OpenClaw pattern)
        if model_response:
            try:
                from src.core.intelligence.reply_parser import parse_response
                turn.parsed_response = parse_response(model_response)

                if turn.parsed_response.directives:
                    logger.info(
                        f"[ ORCHESTRATOR ] Parsed {len(turn.parsed_response.directives)} "
                        f"reply directives from model response"
                    )

                    # Dispatch directives through DirectiveProcessor
                    await self._directive_proc.process(turn.parsed_response)

            except Exception as e:
                logger.error(f"[ ORCHESTRATOR ] Failed to parse reply directives: {e}")

        logger.info(f"[ ORCHESTRATOR ] Finalizing turn {turn.turn_id} (tools: {turn.tool_count}, duration: {time.time() - turn.start_time:.2f}s)")

        # Store in memory if we have both user and model transcripts.
        # Fire-and-forget: nothing downstream in this turn needs the Supabase write to
        # finish, and _reset_turn_tracking() below clears the pending-tool lists — so we
        # snapshot copies now rather than handing the background task live mutable refs.
        if self._current_user_transcript and model_response:
            self._fire_and_forget(
                self._store_turn_in_memory(
                    user_message=self._current_user_transcript,
                    model_response=model_response,
                    tool_calls=list(self._pending_tool_calls),
                    tool_responses=list(self._pending_tool_responses),
                    session_key=self._session_now(),
                ),
                "store_turn_in_memory"
            )

        # Durable run log + dashboard feed for autonomous turns (non-USER initiators).
        # Both are fire-and-forget; they never block finalize_turn.
        if turn.initiator != TurnInitiator.USER:
            self._fire_and_forget(
                self._activity_reporter.record_run(
                    initiator=turn.initiator.value,
                    category=turn.category,
                    user_input=turn.user_input,
                    model_response=model_response,
                    tools_executed=turn.tools_executed,
                    duration_sec=time.time() - turn.start_time,
                ),
                "record_autonomous_run",
            )
            if turn.tools_executed:
                self._fire_and_forget(
                    self._activity_reporter.surface_to_dashboard(
                        initiator=turn.initiator.value,
                        category=turn.category,
                        session_key=turn.session_key,
                        tools_executed=turn.tools_executed,
                        model_response=model_response,
                    ),
                    "surface_autonomous_action",
                )

        # Self-improvement signal: note successful multi-tool sequences so recurring
        # procedures can be suggested as a reusable skill (throttled internally).
        successful_tools = [r.get("tool_name") for r in self._pending_tool_responses
                            if r.get("success") and r.get("tool_name")]
        if len(set(successful_tools)) >= 2:
            try:
                from src.core.intelligence.capability_patterns import WorkflowPatternTracker
                self._fire_and_forget(
                    WorkflowPatternTracker.note_turn(
                        successful_tools, registry=getattr(self.tool_coord, "tools", None)),
                    "capability_pattern_note_turn")
            except Exception as e:
                logger.error(f"[ ORCHESTRATOR ] Failed to note tool pattern: {e}")

        # Publish turn completed event
        self.event_bus.publish(TurnCompleted(
            turn_id=turn.turn_id,
            turn_context=turn,
            duration=time.time() - turn.start_time,
            tool_count=turn.tool_count,
            model_response=model_response
        ))

        # Reset turn tracking
        await self._reset_turn_tracking()

        # Transition back to ready state
        await self.state_coord.transition_to("ready")

        # Trigger after_turn_complete hook (Phase 2 - OpenClaw pattern)
        try:
            hook_context = HookContext(
                hook_type=HookType.AFTER_TURN_COMPLETE,
                turn_id=turn.turn_id,
                initiator=turn.initiator.value,
                session_key=turn.session_key,
                category=turn.category,
                metadata={
                    'model_response': model_response,
                    'tool_count': turn.tool_count,
                    'duration': time.time() - turn.start_time
                }
            )
            await self._hooks_manager.trigger(HookType.AFTER_TURN_COMPLETE, hook_context)
        except Exception as e:
            logger.error(f"after_turn_complete hook failed: {e}", exc_info=True)

        logger.info(f"[ ORCHESTRATOR ] Turn {turn.turn_id} finalized")

    async def cancel_turn(self, reason: str = "user_requested") -> None:
        """
        Cancel the current turn.

        Used for:
        - User interruptions
        - Timeouts
        - Error conditions

        Args:
            reason: Reason for cancellation
        """
        if not self.current_turn:
            return

        session_key = self.current_turn.session_key
        logger.info(f"[ ORCHESTRATOR ] Canceling turn {self.current_turn.turn_id} (reason: {reason}, session: {session_key})")

        # Cancel any active tool executions (covers legacy/manual tool flow)
        await self.tool_coord.cancel_active_tools()

        # Clear the LangGraph checkpoint for this session thread.
        # Without this, a dangling AIMessage with pending tool_calls (already
        # written to the checkpoint before the cancel) causes LangGraph to
        # re-invoke the model on the next user turn — the "stop button wakeup" bug.
        if self.turn_coord and session_key:
            try:
                await self.turn_coord.cancel_current_turn(session_key)
            except Exception as e:
                logger.warning(f"[ ORCHESTRATOR ] Failed to clear LangGraph checkpoint: {e}")

        # Publish error event if this was unexpected
        if reason != "user_requested":
            self.event_bus.publish(ErrorOccurred(
                error_type="turn_cancelled",
                error_message=f"Turn cancelled: {reason}"
            ))

        # Reset turn tracking
        await self._reset_turn_tracking()

        # Transition to ready
        await self.state_coord.transition_to("ready")

        # Cancel the streaming task so the LLM adapter stops reading from the model.
        # This must happen AFTER cleanup so state is consistent if the task re-enters.
        task = self._active_turn_task
        self._active_turn_task = None
        if task and not task.done():
            logger.warning("[ ORCHESTRATOR ] Cancelling active streaming task")
            task.cancel()

    async def cancel_turn_for_session(
        self, session_id: str, reason: str = "user_requested"
    ) -> bool:
        """Cancel an in-flight turn for ``session_id`` from another task (e.g. Telegram /stop).

        Must not go through ``process_request`` / the session lock — OpenClaw lesson:
        control commands that queue behind the active turn never interrupt it.
        """
        from src.core.orchestration.context import session_id_var

        if not session_id:
            return False
        token = session_id_var.set(session_id)
        try:
            has_turn = session_id in self._turns_by_session
            has_task = session_id in self._active_task_by_session
            if not has_turn and not has_task:
                return False
            if has_turn:
                await self.cancel_turn(reason=reason)
            else:
                task = self._active_task_by_session.pop(session_id, None)
                if task and not task.done():
                    logger.warning(
                        f"[ ORCHESTRATOR ] Cancelling orphan task for session {session_id}"
                    )
                    task.cancel()
            return True
        finally:
            session_id_var.reset(token)

    def is_turn_active(self) -> bool:
        """Check if a turn is currently active."""
        return self.current_turn is not None and not self.current_turn.completed

    def get_current_turn_context(self) -> Optional[TurnContext]:
        """Get the current turn context (for debugging/monitoring)."""
        return self.current_turn

    # ==========================================
    # PRIVATE METHODS - Response Handlers
    # ==========================================
    # Routing logic lives in TurnResponseRouter (src/core/orchestration/conversation/);
    # these thin wrappers exist because voice_session.py invokes them directly for
    # its own tool-call flow, outside the normal process_response() path.

    async def _handle_tool_call(self, response: Dict[str, Any]) -> None:
        """Handle single tool call (proxied to TurnResponseRouter)."""
        if not self.current_turn:
            return
        await self._response_router.handle_tool_call(
            response, self.current_turn, self._pending_tool_calls, self._pending_tool_responses
        )

    async def _handle_tool_batch(self, response: Dict[str, Any]) -> None:
        """Handle batch of tool calls (proxied to TurnResponseRouter)."""
        if not self.current_turn:
            return
        await self._response_router.handle_tool_batch(
            response, self.current_turn, self._pending_tool_calls, self._pending_tool_responses
        )

    async def _store_turn_in_memory(
        self,
        user_message: str,
        model_response: str,
        tool_calls: List[Dict[str, Any]],
        tool_responses: List[Dict[str, Any]],
        session_key: Optional[str] = None,
    ) -> None:
        """
        Store completed turn in MemoryEngine for long-term archival and RAG.

        IMPORTANT: This is secondary storage. The authoritative conversation 
        history for the active ReAct loop is now managed by LangGraph's 
        Postgres checkpointer. We maintain this for backward compatibility 
        with RAG retrieval and legacy logging.
        """
        try:
            # Format tool execution summary for memory
            tool_summary = ""
            if tool_calls:
                tool_summary = "\n[Tools Used: " + ", ".join([
                    call.get('name', 'unknown') for call in tool_calls
                ]) + "]"

            # Store in memory engine. log_turn is a blocking Supabase call — run it off
            # the event loop thread so it can't stall other coroutines while it's in flight.
            # Pass the turn's own session_key explicitly: self.memory.session_key is a shared
            # global that a concurrent autonomous turn may have re-pointed, so relying on it
            # here would log this turn under the wrong conversation (issue #208).
            await asyncio.to_thread(
                self.memory.log_turn, role='user', content=user_message, session_key=session_key
            )
            await asyncio.to_thread(
                self.memory.log_turn, role='assistant',
                content=model_response + tool_summary, session_key=session_key,
            )

            logger.debug(f"[ ORCHESTRATOR ] Turn stored in memory (tools: {len(tool_calls)})")

        except Exception as e:
            logger.error(f"[ ORCHESTRATOR ] Failed to store turn in memory: {e}", exc_info=True)



    async def _reset_turn_tracking(self) -> None:
        """Reset all turn-level tracking for the current session's turn."""
        sid = self._session_now()
        self._turns_by_session.pop(sid, None)
        self._pending_calls_by_session.pop(sid, None)
        self._pending_responses_by_session.pop(sid, None)
        self._user_transcript_by_session.pop(sid, None)
        self._model_transcript_by_session.pop(sid, None)

        logger.debug(f"[ ORCHESTRATOR ] Turn tracking reset (session={sid})")

    # =========================================================================
    # REPLY DIRECTIVE HANDLING — public proxy API (Phase 2 - OpenClaw Pattern)
    # Dispatch logic lives in DirectiveProcessor; these methods keep the
    # external surface stable so server.py / SessionManager need no changes.
    # =========================================================================

    def register_directive_handler(self, directive_type: str, handler: Callable) -> None:
        """Register *handler* for *directive_type* (proxied to DirectiveProcessor)."""
        self._directive_proc.register(directive_type, handler)

    def unregister_directive_handler(self, directive_type: str) -> None:
        """Unregister the handler for *directive_type* (proxied to DirectiveProcessor)."""
        self._directive_proc.unregister(directive_type)

    def list_directive_handlers(self) -> List[str]:
        """Return the directive type names that have registered handlers."""
        return self._directive_proc.list_handlers()

    def get_current_directives(self) -> Optional[ParsedResponse]:
        """Return the parsed directives from the current turn, or None."""
        if self.current_turn and self.current_turn.parsed_response:
            return self.current_turn.parsed_response
        return None

    async def cleanup(self) -> None:
        """
        Clean up orchestrator resources, including persistent local model adapters.
        """
        async with self._local_adapter_lock:
            if self._local_adapter:
                logger.info(f"[ ORCHESTRATOR ] Cleaning up local adapter {self._local_adapter_config[2]}...")
                await self._local_adapter.close()
                self._local_adapter = None
                self._local_adapter_config = None

