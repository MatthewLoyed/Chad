"""One request through routing, tool selection, prompt assembly, and execution.

Each pipeline owns one turn's state. Shared model, cache, telemetry, and tool
services live on TurnCoordinator; helper methods keep the phases explicit.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from src.shared.logger import setup_logger
from src.shared.exceptions import ToolSelectionError

from src.core.orchestration.langgraph_engine import LangGraphEngine, ChadState
from src.core.utils.token_counter import TokenCounter

from src.core.orchestration.turn.perf import TurnPerfTracker
from src.core.orchestration.turn.tracing import TracingSpan
from src.core.orchestration.turn.telemetry import TurnTelemetry
from src.core.orchestration.turn.event_capture import TurnEventCapture
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.core.orchestration.turn.response_processor import TurnResponsePostProcessor
from src.core.orchestration.turn.scratch import TurnScratch
from src.core.orchestration.turn import metrics as turn_metrics
from src.core.orchestration.turn.status_labels import (
    PROCESSING_LABELS,
    FORMULATING_LABELS,
    CHAT_LABELS,
)

from src.application.events.events import (
    TokenUsageUpdate,
    TurnStartEvent,
    TurnCompleteEvent,
)

if TYPE_CHECKING:
    from src.shared.context import RequestContext
    from src.core.intelligence.adapters.ai_adapter_base import AIAdapter
    from src.core.orchestration.turn_coordinator import TurnCoordinator

logger = setup_logger("TurnPipeline")


def is_background_turn(category: Optional[str], platform: Optional[str]) -> bool:
    """True for a turn that isn't a live interactive user waiting on a reply — heartbeat/cron
    (category="autonomous") or system/background platforms. Drives model_lifecycle.current_priority()
    via turn_priority_var: these turns queue behind (and never evict) an active interactive
    turn's resident models.

    ``platform="eval"`` is deliberately NOT background: eval harnesses (dry-run / live /
    workflow) take absolute precedence in development — they load as interactive and
    ``eval_exclusivity`` blocks other turns for the duration. See evals/live.py."""
    return category == "autonomous" or platform in ("system", "background")


def is_eval_turn(platform: Optional[str]) -> bool:
    """True when the harness tagged ``platform="eval"`` — a distinct mode from live users.

    Canonical policy lives in ``eval_exclusivity`` (exclusive owner of the machine in
    development, keep 9B resident, interactive priority). Call sites here skip
    *production* residency choreography that would thrash the harness: post-turn
    ``schedule_warm`` of the main chat model on SpyAdapter dry-runs, etc. (#632).
    """
    return platform == "eval"


class TurnPipeline:
    """Executes exactly one turn. Not reusable — construct a new one per turn."""

    # A ToolSelectionError is almost always a transient backend hiccup (LM Studio/gate
    # momentarily overloaded, mid-model-swap) rather than a real "selector is down"
    # state — so the whole selection call is retried a couple of times with a short
    # backoff. This is a coarser safety net than llm_selector's own in-request 5xx
    # retry: it also covers timeouts, connection errors, and any other failure mode
    # that narrower retry doesn't match.
    TOOL_SELECTION_MAX_ATTEMPTS = 3

    def __init__(
        self,
        coordinator: "TurnCoordinator",
        scratch: TurnScratch,
        *,
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
    ):
        self._c = coordinator
        self.scratch = scratch

        # Request
        self.prompt = prompt
        self.context = context
        self.adapter = adapter
        self.content_blocks = content_blocks
        self.image_base64 = image_base64
        self.category = category or "agent"
        self.max_tool_calls = max_tool_calls
        self.on_token = on_token
        self.on_thinking = on_thinking
        self.thinking_level = thinking_level
        self.metadata = metadata
        self.initiator = initiator

        # Working state, filled in as the phases run
        self.session_id: str = context.session_id or "default"
        self.delivery: Optional[str] = None
        self.active_adapter: Optional["AIAdapter"] = None
        self.history: List[Dict[str, Any]] = []
        self.tools: Optional[List[Any]] = None
        self.sys_instr: str = ""
        self.bundled_skill_content: Optional[str] = None
        self.bundled_skill_name: Optional[str] = None
        self.skill_command = None
        self.lc_messages: Optional[list] = None
        self.checkpoints_exist: bool = False
        self.cache_eligible: bool = False
        self.mode_source: str = ""
        self.turn_start_time: float = 0.0
        self.perf = TurnPerfTracker()
        self.span: Optional[TracingSpan] = None
        self._ls_outputs: Dict[str, Any] = {}

    # -- convenience ----------------------------------------------------
    @property
    def _emitter(self):
        return self._c._emitter

    @property
    def _event_bus(self):
        return self._c.event_bus

    @property
    def turn_mode(self) -> str:
        return self.scratch.mode

    # -- the turn -------------------------------------------------------
    async def run(self) -> str:
        """Phase list for one turn. Each step may short-circuit with a response."""
        from src.core.skill_system.skill_commands import prepare_skill_turn
        self.skill_command = None
        if (self.prompt or "").lstrip().startswith("/"):
            self.skill_command, self.category, skill_adapter = await prepare_skill_turn(
                self.prompt, self.category, self._c,
            )
            if skill_adapter is not None:
                self.adapter = skill_adapter
        cached = self._prepare()
        if cached is not None:
            return cached
        if self.skill_command and self.skill_command.response is not None:
            return self._complete_from_quick_lane(self.skill_command.response, "skill_command")

        fast_lane_response = await self._try_voice_fast_lane()
        if fast_lane_response is not None:
            return fast_lane_response

        quick_tool_response = await self._try_voice_quick_tool_lane()
        if quick_tool_response is not None:
            return quick_tool_response

        # An approval is continuation of an exact saved call, not a new task.
        # Avoid the mode router, tool-selector LLM, and history compaction LLM.
        if not self.skill_command and await self._prepare_approval_continuation():
            self._open_span()
            return await self._invoke()

        await self._resolve_mode()
        self._open_span()

        failed = await self._resolve_tools()
        if failed is not None:
            return failed

        self._build_prompt()
        await self._prepare_graph()
        return await self._invoke()

    async def _prepare_approval_continuation(self) -> bool:
        engine = self._c._langgraph_engine
        if not engine or not inspect.iscoroutinefunction(getattr(engine, "get_approval_resume_state", None)):
            return False
        saved = await engine.get_approval_resume_state(self.session_id)
        if not saved:
            return False
        from src.core.orchestration.context import turn_category_var
        self._approval_continuation = True
        self.cache_eligible = False
        self.scratch.mode = "agent"
        self.category = "agent"
        self.context.category = self.category
        turn_category_var.set(self.category)
        self.mode_source = "approved_tool_continuation"
        self.tools = saved.get("tools") or []
        self.sys_instr = saved.get("system_instruction") or ""
        self.bundled_skill_content = saved.get("loaded_skill")
        self.bundled_skill_name = saved.get("loaded_skill_name")
        self.checkpoints_exist = True
        self.lc_messages = self._compose_graph_messages()
        return True

    # -- phases ---------------------------------------------------------
    def _prepare(self) -> Optional[str]:
        """Bind turn contextvars and check the near-repeat cache.

        Returns a cached response to short-circuit the turn, else ``None``.
        """
        from src.shared.turn_delivery import resolve_delivery
        from src.core.orchestration.context import (
            turn_delivery_var,
            turn_category_var,
            turn_priority_var,
            turn_id_var,
            session_id_var,
        )

        self.delivery = self.context.delivery or resolve_delivery(self.category)
        self.context.delivery = self.delivery
        self.context.category = self.category
        turn_delivery_var.set(self.delivery)
        turn_category_var.set(self.category)
        # Set from the RAW incoming category/platform, before mode resolution below
        # overwrites turn_category_var with the resolved chat/agent/plan lane — this is
        # what model_lifecycle.current_priority() reads for the whole rest of the turn.
        turn_priority_var.set(
            "background"
            if is_background_turn(self.category, self.context.platform)
            else "interactive"
        )
        if self.context.turn_id:
            turn_id_var.set(self.context.turn_id)
        if self.context.session_id:
            session_id_var.set(self.context.session_id)

        if self.category in ("voice", "contact"):
            self.thinking_level = "minimal"
        self.scratch.is_simple = False
        self.scratch.mode = "agent"

        self.is_retry = bool(getattr(self.context, "retry", False))
        if self.is_retry and hasattr(self._c, "_semantic_cache") and self._c._semantic_cache:
            try:
                self._c._semantic_cache.clear(self.session_id)
            except Exception as e:
                logger.debug(f"[ RETRY ] Failed to clear semantic cache: {e}")

        self.cache_eligible = (
            False
            if self.is_retry or self.skill_command
            else self._c._semantic_cache_eligible(
                self.category, self.content_blocks, self.image_base64
            )
        )
        from src.core.orchestration.approval_store import has_resolved_decision
        if has_resolved_decision(self.session_id):
            self.cache_eligible = False
        if self.cache_eligible:
            cached = self._c._semantic_cache.lookup(self.session_id, self.prompt)
            if cached is not None:
                return self._c._complete_from_semantic_cache(
                    cached, self.session_id, self.delivery, self.context, self.on_token
                )

        self.turn_start_time = time.time()
        self.scratch.metrics = turn_metrics.blank_metrics()

        self.active_adapter = self.adapter or self._c.default_adapter
        if not self.active_adapter:
            raise ValueError(
                "No AI Adapter provided to TurnCoordinator and no default active."
            )

        # Voice quick lanes (tier 1/2) run immediately after ``_prepare``. Emitting a
        # "Processing…" / "Let me think…" status here made the Limb TTS those labels
        # before "Opening youtube." — false canned filler on instant opens. Defer
        # status until after the quick-lane miss (see ``_resolve_mode``).
        if self.category != "voice":
            self._emitter.status(random.choice(PROCESSING_LABELS), self.delivery)
        return None

    def _complete_from_quick_lane(self, ack: str, stop_reason: str) -> str:
        """Shared short-circuit completion for both voice quick-command tiers
        (issue #654 problem 3): publish the same events a normal turn would, skip
        everything else. Mirrors ``_complete_from_semantic_cache``'s pattern.
        """
        self.scratch.metrics.update({
            "stop_reason": stop_reason,
            "duration": round(time.time() - self.turn_start_time, 2),
        })
        if self._event_bus:
            self._event_bus.publish(TurnCompleteEvent(
                turn_id=self.session_id,
                response=ack,
                delivery=self.delivery,
                client_turn_id=self.context.turn_id,
            ))
            self._event_bus.publish(TokenUsageUpdate(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                stop_reason=stop_reason,
            ))
        if self.on_token:
            try:
                self.on_token(ack)
            except Exception:
                pass
        return ack

    async def _try_voice_fast_lane(self) -> Optional[str]:
        """Tier 1, no-LLM quick-command short circuit for voice turns (issue #654
        problem 3).

        Runs BEFORE mode resolution, knowledge fetch, tool selection, and the model
        preload below — all of that exists to set up an LLM call this path never
        makes. Skipping it is the actual latency fix: those steps (specifically
        ``_preload_main_model`` JIT-loading the 9B voice model in `_invoke`) were
        adding seconds of wait AND fighting LM Studio's VRAM budget against whatever
        else was resident, on every single "open youtube"/"search for X" — live
        testing traced the reported "tool doesn't open instantly" + LM Studio OOM
        both back to this unconditional preload running ahead of a fast-lane hit.
        """
        if self.category != "voice":
            return None

        from src.core.orchestration.nodes.voice_fast_lane import execute_fast_lane

        tool_registry = getattr(self._c, "tool_registry", None)
        ack = await execute_fast_lane(self.prompt, tool_registry)
        if ack is None:
            return None

        logger.info(f"[ VOICE FAST LANE ] tier-1 hit for {self.prompt!r} — no model touched.")
        return self._complete_from_quick_lane(ack, "fast_lane")

    async def _try_voice_quick_tool_lane(self) -> Optional[str]:
        """Tier 2 of the voice quick-command lane (issue #654 problem 3): tier 1
        is instant but only catches phrasings its regex/embedding rules recognize
        ("can you open youtube" doesn't match a `^open ` anchored regex). This asks
        the resident voice model (`local_models.category_models.voice`) to call ONE
        of a small fixed tool set with NO other context — no persona, history,
        memory, or RAG shortlist.

        Benchmarked directly (`evals/voice_quick_model_bench.py`): 82% accuracy,
        3-8s latency (mean ~5.4s) — confirmed that's the floor for ANY local model
        call on this hardware regardless of prompt size, not something a leaner
        prompt fixes. Still a fraction of tier 3's full `escalate_to_agent` path
        (gemma-4-26b + hybrid RAG + full persona/history), which stays reserved for
        real multi-step work.
        """
        if self.category != "voice":
            return None

        from src.shared.config_manager import ConfigManager
        from src.core.orchestration.nodes.voice_quick_tool import try_quick_tool_call

        local_models = ConfigManager.get("local_models", {}) or {}
        model = local_models.get("category_models", {}).get("voice")
        if not model:
            return None
        base_url = local_models.get("providers", {}).get("lmstudio", {}).get(
            "base_url", "http://localhost:1234/v1"
        )

        tool_registry = getattr(self._c, "tool_registry", None)
        ack = await try_quick_tool_call(
            self.prompt, tool_registry, model=model, base_url=base_url,
        )
        if ack is None:
            return None

        logger.info(f"[ VOICE QUICK TOOL LANE ] tier-2 hit for {self.prompt!r}.")
        return self._complete_from_quick_lane(ack, "quick_tool_lane")

    async def _resolve_mode(self) -> None:
        """Load history, route the turn to a lane, and wait for MCP if it matters."""
        from src.core.orchestration.context import turn_category_var

        # Voice skipped the early processing status so tier-1/2 opens stay silent
        # until their ack. Once we miss both quick lanes, emit it like other categories.
        if self.category == "voice":
            self._emitter.status(random.choice(PROCESSING_LABELS), self.delivery)

        if getattr(self, "is_retry", False):
            from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
            try:
                repo = ChatHistoryRepository()
                await repo.truncate_conversation_after(self.session_id, getattr(self.context, "keep_count", None))
                logger.info(
                    f"[ RETRY ] Pruned conversation {self.session_id} history to keep_count={getattr(self.context, 'keep_count', None)}"
                )
            except Exception as e:
                logger.warning(f"[ RETRY ] Failed to prune conversation history: {e}")

        self.history = await self._c._history_loader.load(self.session_id)
        self.perf.mark("history")
        self._emitter.phase("history", self.delivery)

        mode, self.mode_source, confidence = await self._c._mode_resolver.resolve(
            prompt=self.prompt,
            category=self.category,
            content_blocks=self.content_blocks,
            image_base64=self.image_base64,
            history=self.history,
            initiator=self.initiator,
            platform=self.context.platform,
        )
        self.scratch.mode = mode
        engine = self._c._langgraph_engine
        if engine and inspect.iscoroutinefunction(getattr(engine, "get_execution_resume_mode", None)) and self.category not in ("voice", "contact", "telegram_agent"):
            resume_mode = await engine.get_execution_resume_mode(self.session_id, self.prompt)
            if resume_mode:
                mode, self.mode_source, confidence = resume_mode, "execution_continuation", 1.0
                self.scratch.mode = mode
        # Preserves explicitly-selected lanes (voice) instead of re-deriving the category
        # from the resolved turn_mode, which silently rewrote category="voice" to "agent"
        # right before TurnStateBuilder ran — route_entry then took the agent lane and the
        # whole fast voice responder was unreachable over the real WebSocket path.
        self.category = self._c._mode_resolver.category_for_turn(self.category, mode)
        self.context.category = self.category
        turn_category_var.set(self.category)
        self.perf.mark("intent")
        self._emitter.phase("intent", self.delivery)
        if mode == "chat":
            self._emitter.status(random.choice(CHAT_LABELS), self.delivery)

        if self.mode_source == "auto_router" and not is_eval_turn(self.context.platform):
            from src.core.intelligence.model_lifecycle import schedule_warm
            schedule_warm(turn_coordinator=self._c, canonical_lane=mode)

        if mode == "chat":
            self.thinking_level = "off"
            # Contact (and other explicit lanes) resolve to turn_mode=chat for the
            # conversational graph path, but must KEEP their category_models pin
            # (9B). Swapping to the picker chat adapter here rewrote contact→Gemma.
            if self.category not in ("contact", "voice"):
                chat_adapter = self._c._get_chat_adapter()
                if chat_adapter:
                    self.active_adapter = chat_adapter

        mcp_ready = self._c.mcp_ready_event
        if mcp_ready and not mcp_ready.is_set() and mode != "chat":
            logger.warning("[ TURN ] Waiting for MCP warmup to complete before executing turn...")
            await mcp_ready.wait()
            logger.warning("[ TURN ] MCP warmup complete. Proceeding with turn.")
        self.perf.mark("mcp_wait")
        self._emitter.phase("mcp_wait", self.delivery)

        if self._event_bus:
            from src.application.events import TurnModeResolved
            self._event_bus.publish(TurnModeResolved(
                mode=mode,
                source=self.mode_source,
                confidence=confidence,
                delivery=self.delivery,
            ))

        prompt_preview = (self.prompt or "").replace("\n", " ").strip()
        if len(prompt_preview) > 90:
            prompt_preview = prompt_preview[:87] + "..."

        logger.info(
            f"========== [ TURN START ] Session={self.session_id} | "
            f"Model={self.active_adapter.model_name} | Mode={mode} | Prompt=\"{prompt_preview}\" =========="
        )
        logger.info(
            f"[ TURN ] Details: Category={self.category}, Delivery={self.delivery}, Source={self.mode_source}"
        )

    def _open_span(self) -> None:
        """Open the LangSmith parent run that all child LLM/tool runs attach to.

        ``langsmith.trace`` sets the contextvar so LangChain's auto-tracer picks up
        the parent.
        """
        self.span = TracingSpan(
            name="ChadTurn",
            inputs={"prompt": self.prompt or "", "session_id": self.session_id},
            tags=["chad-turn", f"platform:{self.context.platform or 'unknown'}"],
            metadata={
                "session_id": self.session_id,
                "platform": self.context.platform or "unknown",
                "category": self.category or "unknown",
                "turn_mode": self.turn_mode,
                "model": self.active_adapter.model_name,
            },
        )
        if self._event_bus:
            self._event_bus.publish(TurnStartEvent(turn_id=self.session_id))

    async def _resolve_tools(self) -> Optional[str]:
        """Pick this turn's tool menu. Returns a user-facing message if selection failed."""
        # Contact / telegram_agent resolve to special tool menus (must not take empty chat path).
        if self.category == "contact":
            selection = self._c._tool_selector.contact_schemas()
            self.tools = selection.schemas
            self.scratch.is_simple = selection.is_simple
            self.scratch.tool_selection = selection.selection
            self.scratch.bundled_skills = []
            self.scratch.knowledge_boost = ""
        elif self.category == "telegram_agent":
            selection = self._c._tool_selector.telegram_agent_schemas()
            self.tools = selection.schemas
            self.scratch.is_simple = selection.is_simple
            self.scratch.tool_selection = selection.selection
            self.scratch.bundled_skills = []
            self.scratch.knowledge_boost = ""
        elif self.category == "voice":
            selection = self._c._tool_selector.voice_schemas()
            self.tools = selection.schemas
            self.scratch.is_simple = selection.is_simple
            self.scratch.tool_selection = selection.selection
            self.scratch.bundled_skills = []
            self.scratch.knowledge_boost = ""
        elif self.turn_mode == "chat":
            selection = self._c._tool_selector.chat_schemas()
            self.tools = selection.schemas
            self.scratch.is_simple = selection.is_simple
            self.scratch.tool_selection = selection.selection
            self.scratch.bundled_skills = []
            self.scratch.knowledge_boost = await self._c._fetch_knowledge_for_selection(
                self.prompt, history=self.history, category=self.category,
            )
        else:
            failed = await self._resolve_tools_for_agent()
            if failed is not None:
                return failed

        self.perf.mark("tools")
        self._emitter.phase(
            "chat_tools"
            if self.turn_mode == "chat" and self.category not in ("contact", "telegram_agent")
            else "tools",
            self.delivery,
        )
        if self.turn_mode != "chat":
            self._emitter.tools_menu(self.scratch.tool_selection, self.delivery)
        self._annotate_selection_perf()
        return None

    async def _resolve_tools_for_agent(self) -> Optional[str]:
        """The non-chat selection path: narrator, selector hint, retry, bundled skill."""
        # Phase C: GUI text wait narrator — parallel with tool selection.
        # Text chat only (no voice lane / TTS). Fire-and-forget; never loads/unloads models.
        from src.shared.turn_delivery import DELIVERY_GUI
        if self.delivery == DELIVERY_GUI:
            asyncio.create_task(self._emitter.wait_narrator(self.prompt, self.delivery))

        # Tell the LLM tool selector which model is the main chat model so it can
        # skip unloading if the selector model and the chat model are the same.
        llm_selector = getattr(
            getattr(self._c, "tool_registry", None), "_llm_selector", None
        )
        if llm_selector and hasattr(llm_selector, "set_main_model_hint"):
            llm_selector.set_main_model_hint(self.active_adapter.model_name)

        self.tools, last_error = await self._select_tools_with_retry()
        if last_error is not None:
            return self._fail_tool_selection(last_error)

        if self.skill_command:
            self.bundled_skill_content = self.skill_command.content
            self.bundled_skill_name = self.skill_command.name
        else:
            self.bundled_skill_content, self.bundled_skill_name = (
                await self._c._tool_selector.resolve_bundled_skill(self.scratch.bundled_skills)
            )
        sel = self.scratch.tool_selection
        if sel:
            logger.warning(
                f"[ TURN ] Tool selection path={sel.get('path')} "
                f"llm_available={sel.get('llm_available')} "
                f"final={sel.get('final_tool_names')} "
                f"mcp={sel.get('mcp_tools_in_final')}"
                + (f" reason={sel.get('llm_unavailable_reason')}"
                   if sel.get('llm_unavailable_reason') else "")
            )
            self.span.add_metadata({"tool_selection": sel})
        return None

    async def _select_tools_with_retry(self) -> tuple:
        """``(tools, last_error)`` — retry selection past transient backend failures."""
        # Fetch knowledge once, before the retries — it's independent of the 9B/LM Studio
        # path that actually fails, so re-querying it per attempt would be wasted work.
        knowledge_boost = await self._c._fetch_knowledge_for_selection(
            self.prompt, history=self.history, category=self.category,
        )
        self.scratch.knowledge_boost = knowledge_boost

        last_error: Optional[ToolSelectionError] = None
        selection_query = self.skill_command.selection_query if self.skill_command else self.prompt
        for attempt in range(1, self.TOOL_SELECTION_MAX_ATTEMPTS + 1):
            try:
                tools = await self._c._get_optimized_tools(
                    selection_query,
                    category=self.category,
                    session_id=self.session_id,
                    history=self.history,
                    knowledge_boost=knowledge_boost,
                )
                return tools, None
            except ToolSelectionError as e:
                last_error = e
                if attempt < self.TOOL_SELECTION_MAX_ATTEMPTS:
                    logger.warning(
                        f"[ TURN ] Tool selection failed (attempt {attempt}/"
                        f"{self.TOOL_SELECTION_MAX_ATTEMPTS}): {e} — retrying."
                    )
                    await asyncio.sleep(1.5 * attempt)
        return None, last_error

    def _fail_tool_selection(self, last_error: ToolSelectionError) -> str:
        """Close out a turn whose tool selection never succeeded."""
        # Log the full internal detail (which may include a raw provider error body,
        # e.g. LM Studio/gate HTML on a 500) server-side only — never surface that as
        # the literal chat response. The user sees a clean, actionable message instead.
        err = str(last_error)
        logger.error(
            f"[ TURN ] Tool selection aborted after "
            f"{self.TOOL_SELECTION_MAX_ATTEMPTS} attempts: {err}"
        )
        user_message = (
            "I couldn't figure out which tools to use for that — the local tool "
            "selector didn't respond. Please try again."
        )
        self.scratch.metrics.update({
            "stop_reason": "error",
            "duration": round(time.time() - self.turn_start_time, 2),
        })
        if self._event_bus:
            self._event_bus.publish(TurnCompleteEvent(
                turn_id=self.session_id,
                response=user_message,
                delivery=self.delivery,
                client_turn_id=self.context.turn_id,
            ))
        self.span.close(outputs={"error": err, "stop_reason": "error"})
        return user_message

    def _annotate_selection_perf(self) -> None:
        """Surface isolated 9B selector latency + shortlist size so the "tools" bucket
        can be split into embedding-shortlist vs 9B-inference in the breakdown."""
        try:
            sel = self.scratch.tool_selection or {}
            meta = sel.get("llm_select_meta") or {}
            if meta.get("latency_s") is not None:
                self.perf.annotate(tool_9b_ms=round(float(meta["latency_s"]) * 1000.0, 1))
            if sel.get("shortlist_ms") is not None:
                # The RAG shortlist (CPU embedding + Supabase RPC) — "time to reach the 9B".
                self.perf.annotate(shortlist_ms=sel["shortlist_ms"])
            if sel.get("shortlist_size") is not None:
                self.perf.annotate(shortlist_size=sel["shortlist_size"])
            if sel.get("path"):
                self.perf.annotate(tool_path=sel["path"])
        except Exception:
            pass

    def _build_prompt(self) -> None:
        """Assemble the system instruction and record an up-front token estimate."""
        # Plan lane: inject knowledge context only (memory here; graphrag adds graph/activity).
        # Scout/planner nodes add their own scoped role prompts — no identity/soul/routing.
        self.sys_instr = TurnStateBuilder.build_system_instruction(
            category=self.category,
            turn_mode=self.turn_mode,
            initiator=self.initiator,
            platform=self.context.platform,
            prompt=self.prompt,
            history=self.history,
            memory_engine=self._c.memory_engine,
            session_id=self.session_id,
        )
        self.perf.mark("prompt")
        self._emitter.phase("prompt", self.delivery)

        self._estimate_prompt_tokens()
        self.perf.mark("tokens")
        self._emitter.phase("tokens", self.delivery)

    def _estimate_prompt_tokens(self) -> None:
        """Record a prompt-token estimate and push it to the UI immediately, so the
        counter moves before the first token. Best-effort: never aborts the turn."""
        try:
            est_history = self.history + [{"role": "user", "content": self.prompt or ""}]
            estimated = TokenCounter.count_total(
                system_instruction=self.sys_instr,
                memory_block="",
                history=est_history,
                tools=self.tools,
            )
            logger.warning(f"[ TURN ] Estimated prompt tokens: {estimated}")
            self.scratch.metrics["estimated_prompt_tokens"] = estimated
            if self._event_bus:
                self._event_bus.publish(TokenUsageUpdate(
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    estimated_prompt_tokens=estimated,
                ))
        except Exception as e:
            logger.warning(f"[ TURN ] Failed to calculate estimated prompt tokens: {e}")

    async def _prepare_graph(self) -> None:
        """Preflight-consolidate history, ensure an engine, and compose the messages."""
        self.history = await self._c._consolidator.preflight(
            self.sys_instr,
            self.tools,
            history=self.history,
            session_id=self.session_id,
            adapter=self.active_adapter,
            langgraph_engine=self._c._langgraph_engine,
        )
        self.perf.mark("preflight")
        self._emitter.phase("preflight", self.delivery)

        engine = self._c._langgraph_engine
        if not engine or engine.adapter != self.active_adapter:
            logger.info(
                f"[ TURN ] Initializing fresh LangGraph engine for adapter "
                f"{self.active_adapter.model_name}"
            )
            self._c._langgraph_engine = LangGraphEngine(
                adapter=self.active_adapter,
                tool_executor=self._c.tool_executor,
                mcp_ready_event=self._c.mcp_ready_event,
            )

        if getattr(self, "is_retry", False):
            engine = self._c._langgraph_engine
            if engine:
                try:
                    await engine.clear_checkpoints(self.session_id)
                    logger.info(f"[ RETRY ] Cleared LangGraph checkpoints for thread {self.session_id}")
                except Exception as e:
                    logger.warning(f"[ RETRY ] Failed to clear checkpoints: {e}")

        self.checkpoints_exist = await self._thread_has_checkpoint()
        self.perf.mark("checkpoint")
        self._emitter.phase("checkpoint", self.delivery)
        self.lc_messages = self._compose_graph_messages()

    async def _thread_has_checkpoint(self) -> bool:
        """Whether LangGraph already holds state for this thread. Non-fatal: a lookup
        failure is treated as "no checkpoint", which seeds from the DB instead."""
        try:
            return bool(await self._c._langgraph_engine.get_history(self.session_id))
        except Exception as e:
            logger.warning(f"[ TURN ] Failed to check checkpoints: {e}")
            return False

    def _compose_graph_messages(self) -> list:
        """Build the LangGraph message list.

        With a live checkpoint we pass only the new user message and let the graph resume.
        Without one we seed from external storage — the standard LangGraph pattern, covering
        both a brand-new session and crash recovery where the checkpointer lost state. The
        current user message is not in the DB yet (it is saved after the turn completes), so
        seeding from DB + appending it is always safe.
        """
        user_msg: Dict[str, Any] = {"role": "user", "content": self.prompt or ""}
        if self.content_blocks:
            user_msg["content_blocks"] = self.content_blocks
        if self.image_base64:
            user_msg["image_base64"] = self.image_base64
        lc_user_msg = LangGraphEngine.from_chad_history([user_msg])

        if self.checkpoints_exist:
            logger.info(
                f"[ TURN ] Resuming thread {self.session_id} from checkpoint. "
                "Passing only new user message."
            )
            return lc_user_msg

        if self.history:
            logger.warning(
                f"[ TURN ] No checkpointer state for existing thread {self.session_id} "
                f"({len(self.history)} DB messages). Seeding graph from DB (crash recovery)."
            )
        else:
            logger.info(f"[ TURN ] New session {self.session_id}. Starting fresh.")
        return LangGraphEngine.from_chad_history(self.history) + lc_user_msg

    async def _invoke(self) -> str:
        """Preload the model, run the graph, and turn the final state into a response."""
        telemetry = TurnTelemetry(
            event_bus=self._event_bus,
            metrics=self.scratch.metrics,
            turn_start_time=self.turn_start_time,
            on_token=self.on_token,
            on_thinking=self.on_thinking,
        )
        # Canonical turn id for plan events, tracing, and UI dedup (must match context.turn_id,
        # not session_id — mixing them caused duplicate ExecutionPlan cards in the chat UI).
        turn_id = getattr(self.context, "turn_id", None) or self.session_id

        initial_state: ChadState = TurnStateBuilder.build(
            lc_messages=self.lc_messages,
            turn_id=turn_id,
            session_id=self.session_id,
            category=self.category,
            turn_mode=self.turn_mode,
            platform=self.context.platform,
            is_simple=self.scratch.is_simple,
            sys_instr=self.sys_instr,
            tools=self.tools,
            max_tool_calls=self.max_tool_calls,
            thinking_level=self.thinking_level,
            bundled_skill_content=self.bundled_skill_content,
            bundled_skill_name=self.bundled_skill_name,
            telemetry=telemetry,
            metadata=self.metadata,
            resuming_thread=self.checkpoints_exist and not bool(self.skill_command),
            knowledge_boost=self.scratch.knowledge_boost,
        )

        # Ensure the supervisor model is resident before invoking (attributes the model-load
        # gap behind a visible status instead of silent JIT dead air), then signal "Thinking…".
        if not getattr(self, "_approval_continuation", False):
            await self._c._preload_main_model(self.active_adapter, self.delivery)
        self.perf.mark("preload")
        self._emit_perf_breakdown()
        self._emitter.status(random.choice(FORMULATING_LABELS), self.delivery)

        with TurnEventCapture(self._event_bus) as event_capture:
            try:
                logger.info(f"[ TURN ] Invoking LangGraph engine (session: {self.session_id})")
                final_state = await self._c._langgraph_engine.invoke(
                    initial_state, thread_id=self.session_id
                )

                escalated = await self._maybe_escalate_voice(final_state)
                if escalated is not None:
                    return escalated

                # Post-Turn Persistence lives in ConversationOrchestrator.finalize_turn()
                # to keep this decoupled from SQLite database concerns.
                outcome = TurnResponsePostProcessor.process(
                    final_state,
                    metrics=self.scratch.metrics,
                    turn_start_time=self.turn_start_time,
                )
                self._ls_outputs = dict(
                    outcome.ls_outputs,
                    tool_selection=self.scratch.tool_selection or None,
                )
                telemetry.complete(outcome, session_id=self.session_id, client_turn_id=self.context.turn_id)
                self.scratch.thinking_events = event_capture.consolidate()
                self._schedule_rewarm()
                self._maybe_cache(outcome, final_state)
                duration = round(time.time() - self.turn_start_time, 2)
                logger.info(
                    f"========== [ TURN COMPLETED ] Duration={duration}s | Session={self.session_id} =========="
                )
                return outcome.response
            except Exception as e:
                stop_reason = "error"
                if "recursion" in str(e).lower() or "recursion_limit" in str(e).lower():
                    stop_reason = "max_iterations"
                duration = round(time.time() - self.turn_start_time, 2)
                self.scratch.metrics.update({
                    "stop_reason": stop_reason,
                    "duration": duration,
                })
                self._ls_outputs = {"error": str(e), "stop_reason": stop_reason}
                logger.warning(
                    f"========== [ TURN ENDED ] Duration={duration}s | Status={stop_reason} ({type(e).__name__}: {e}) | Session={self.session_id} =========="
                )
                raise
            finally:
                self.span.close(outputs=self._ls_outputs)

    def _emit_perf_breakdown(self) -> None:
        """Log a ranked [ PERF ] line and stash the breakdown on metrics so it rides
        out on the final token_usage event for probes."""
        try:
            self.scratch.metrics["pre_model_breakdown"] = self.perf.as_dict()
            logger.warning(self.perf.format_line())
        except Exception as e:
            logger.debug(f"[ TURN ] pre-model breakdown emit failed (non-fatal): {e}")

    async def _maybe_escalate_voice(self, final_state) -> Optional[str]:
        """Voice/contact front desk that asked to escalate re-runs on the full agent pipeline."""
        escalate, reason = TurnResponsePostProcessor.check_voice_escalate(
            final_state, self.category
        )
        if not escalate:
            return None

        # Telegram contact desk: deliver the 9B personal ack as its own message
        # BEFORE the Gemma turn (final answer is a separate sendMessage).
        if self.category == "contact" and (self.context.platform or "") == "telegram":
            ack_text = TurnResponsePostProcessor.extract_front_desk_ack(final_state)
            try:
                from src.remote.telegram.telegram_response_router import (
                    get_active_telegram_router,
                )
                router = get_active_telegram_router()
                if router and self.session_id:
                    await router.send_front_desk_ack(
                        session_key=self.session_id,
                        text=ack_text,
                    )
                    # Keep typing while Gemma works.
                    await router.send_typing_for_session(self.session_id)
            except Exception as e:
                logger.warning(f"[ CONTACT ] Failed to deliver front-desk ack: {e}")

        handoff_category = (
            "telegram_agent"
            if self.category == "contact" and (self.context.platform or "") == "telegram"
            else "agent"
        )
        logger.warning(
            f"[ FRONT DESK ] escalate_to_agent triggered ({reason!r}) "
            f"category={self.category!r} — re-running on {handoff_category} pipeline."
        )
        agent_adapter = await self._c._adapter_for_category(handoff_category, None)
        self._c._langgraph_engine = None

        handoff_prompt = self.prompt
        if reason:
            label = (
                "Telegram contact desk escalated"
                if handoff_category == "telegram_agent"
                else "Phone contact desk escalated"
            )
            handoff_prompt = f"({label}: {reason})\n\n{self.prompt}"

        meta = dict(self.metadata or {})
        meta["front_desk_escalate_reason"] = reason or ""
        meta["front_desk_category"] = self.category

        return await self._c._execute_turn_internal(
            prompt=handoff_prompt,
            context=self.context,
            adapter=agent_adapter,
            content_blocks=self.content_blocks,
            image_base64=self.image_base64,
            category=handoff_category,
            max_tool_calls=self.max_tool_calls,
            on_token=self.on_token,
            on_thinking=self.on_thinking,
            thinking_level="medium",
            metadata=meta,
            initiator=self.initiator,
        )

    def _schedule_rewarm(self) -> None:
        """Re-warm both models for the NEXT message (refs #592).

        Whatever the turn just used, only that one is resident when it stops — clean
        finish, awaiting_approval, or awaiting_input all count, because a follow-up in
        ANY of those cases still runs tool selection (there is no bypass for
        continuations), so the selector model is needed again regardless of why this
        turn stopped. Without this, the next message always pays a cold load before its
        first tool-selector call. Fire-and-forget; must never block the response.

        Eval/dry-run turns skip it: re-warming would JIT-load the main chat model (e.g.
        Gemma 26B) alongside the 9B selector and thrash VRAM for a SpyAdapter turn that
        never needs a main model (#632).
        """
        try:
            if not is_eval_turn(self.context.platform):
                from src.core.intelligence.model_lifecycle import schedule_warm
                schedule_warm(turn_coordinator=self._c, ui_mode=self.turn_mode)
        except Exception as e:
            logger.debug(f"[ TURN ] Post-turn re-warm skipped (non-fatal): {e}")

    def _maybe_cache(self, outcome, final_state) -> None:
        """Store a clean, side-effect-free answer for the near-repeat cache (#516)."""
        if not (
            self.cache_eligible
            and final_state.get("total_tool_calls", 0) == 0
            and not outcome.awaiting_approval
            and not outcome.awaiting_input
            and outcome.stop_reason not in ("error", "awaiting_approval", "awaiting_input")
        ):
            return
        try:
            self._c._semantic_cache.store(self.session_id, self.prompt, outcome.response)
        except Exception as e:
            logger.debug(f"[ TURN ] Semantic cache store skipped (non-fatal): {e}")


__all__ = ["TurnPipeline", "is_background_turn", "is_eval_turn"]
