"""
UI State Syncer
Bridges the brain's EventBus (turn lifecycle, streaming tokens, tool events)
to outgoing WebSocket payloads for the active Limb. Owns all per-turn
broadcast state — completion/initiator/conversation buckets keyed by
(session_id, delivery), and the buffered thinking-stream compression state.
Reads shared connection state (active websocket, client_type, turn_coordinator)
through the owning LimbCoordinator.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.shared.logger import setup_logger
from src.remote.protocol import MessageType, ChadMessage
from src.shared.turn_delivery import (
    DELIVERY_GUI,
    DELIVERY_VOICE,
    DELIVERY_SILENT,
    DELIVERY_TELEGRAM,
    resolve_delivery,
    is_gui_delivery,
)
from src.remote.turn_initiator import CHAT_INITIATORS, initiator_to_str, is_autonomous_initiator

logger = setup_logger("UIStateSyncer")


@dataclass
class TurnPersistState:
    """Who started one turn, and what still has to be written when it finishes.

    Captured per BUCKET at USER_INPUT rather than held on the coordinator. As
    coordinator-wide fields these were overwritten by whatever client spoke next:
    the desktop reconnecting mid-turn flipped ``client_type`` to ``desktop`` and the
    phone's turn was never persisted, and two concurrent turns would each write the
    other's user message (issue #711).
    """

    client_type: str = "desktop"
    conversation_id: Optional[str] = None
    user_content: Any = None
    user_text: Optional[str] = None
    user_metadata: Optional[Dict[str, Any]] = None


class UIStateSyncer:
    _INITIATOR_TITLES: Dict[str, str] = {
        "system": "Heartbeat / Cron run",
        "webhook": "Webhook triggered",
        "gmail": "Gmail webhook",
        "skill": "Skill run",
    }

    def __init__(self, limb, event_bus):
        self._limb = limb
        self._event_bus = event_bus

        # --- PER-TURN BROADCAST STATE (issue #208) ---
        # All in-flight turn state is keyed by a BUCKET = (session_id, delivery), not
        # by delivery alone. An autonomous turn (cron/heartbeat/webhook) resolves to
        # delivery="gui" just like a user turn, but carries a DIFFERENT session_id, so
        # bucketing keeps the two from sharing state when they run concurrently — a
        # background turn can no longer flip the user turn's initiator and clip its
        # stream. The bucket is resolved from session_id_var, which process_request
        # pins per turn; because EventBus.publish() invokes handlers synchronously in
        # the publishing turn's contextvars, each stream event resolves to its own
        # turn's bucket.
        self._completed_by_bucket: Dict[Tuple[str, str], bool] = {}
        # Bucket -> conversation_id pinned at USER_INPUT. Autonomous turns never pin
        # one, so their broadcasts have no conversation_id and route to
        # AUTONOMOUS_NOTIFICATION rather than the active chat.
        self._conversation_id_by_bucket: Dict[Tuple[str, str], Optional[str]] = {}
        # Bucket -> initiator of the in-flight turn, set at TurnStarted for EVERY turn.
        # The streaming-event handlers gate on this so an autonomous turn's
        # reasoning/tool stream is never broadcast into the GUI chat.
        self._initiator_by_bucket: Dict[Tuple[str, str], str] = {}
        # Bucket -> who started the turn + what to persist when it finishes. Set at
        # USER_INPUT, consumed and dropped at turn completion (issue #711).
        self._persist_by_bucket: Dict[Tuple[str, str], TurnPersistState] = {}

        # Buffered thinking state for non-lossy stream compression
        self._pending_thinking_broadcast = None
        self._pending_thinking_initiator = None
        self._pending_thinking_type = "thinking"
        self._pending_thinking_append = True
        # Subagent attribution for the pending block (delegation grouping).
        self._pending_thinking_agent_name = None
        self._pending_thinking_parent_id = None
        self._last_thinking_broadcast_time = 0.0
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def _create_task(self, coro):
        """Safely schedule a coroutine onto the event loop from any thread."""
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            return loop.create_task(coro)
        except RuntimeError:
            loop = getattr(self, "_loop", None)
            if loop and loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop)
            logger.debug(f"[ UI STATE ] No active event loop to schedule coroutine: {coro}")
            if hasattr(coro, "close"):
                coro.close()
            return None

    # ------------------------------------------------------------------
    # Bucket helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _delivery_from_event(event) -> str:
        return getattr(event, "delivery", None) or DELIVERY_GUI

    @staticmethod
    def _session_now() -> str:
        """Session id of the turn whose code is publishing the current event.

        Read from the contextvar pinned by ConversationOrchestrator.process_request.
        Safe to call inside an EventBus handler because publish() invokes handlers
        synchronously in the publishing turn's contextvars.
        """
        from src.core.orchestration.context import session_id_var
        return session_id_var.get() or ""

    def _bucket_from_event(self, event, session_id: Optional[str] = None) -> Tuple[str, str]:
        """Resolve the (session_id, delivery) bucket for an event's turn."""
        sid = session_id if session_id is not None else self._session_now()
        deliv = getattr(event, "delivery", None)
        if not deliv:
            if ":telegram:" in sid or getattr(event, "platform", None) == "telegram":
                deliv = DELIVERY_TELEGRAM
            else:
                deliv = DELIVERY_GUI
        elif ":telegram:" in sid or getattr(event, "platform", None) == "telegram":
            deliv = DELIVERY_TELEGRAM
        return (sid, deliv)


    def current_initiator(self) -> str:
        """Initiator of the turn whose code is currently running (resolved from
        contextvars). Used by the direct send_to_active_limb callbacks in
        remote_factory, which fire inside the turn's task context but outside the
        event-bus handlers that carry the bucket explicitly."""
        from src.core.orchestration.context import current_turn_delivery
        bucket = (self._session_now(), current_turn_delivery())
        return self._initiator_by_bucket.get(bucket, "user")

    def _is_chat_turn(self, bucket: Tuple[str, str]) -> bool:
        """True iff this bucket's active turn should stream into the chat UI.

        Requires both a GUI delivery channel AND a user-facing initiator. Autonomous
        turns (cron/heartbeat, skill, gmail webhook) fail the initiator test even though
        they run on delivery="gui", so their live stream is suppressed from the chat.
        """
        if not is_gui_delivery(bucket[1]):
            return False
        return self._initiator_by_bucket.get(bucket, "user") in CHAT_INITIATORS

    def _is_streamable_turn(self, bucket: Tuple[str, str]) -> bool:
        """True iff this bucket's active turn should stream text fragments out.

        Like ``_is_chat_turn`` but also allows voice delivery (epic #291 Phase 2:
        live voice mode speaks sentences as they stream in, instead of waiting
        for the final reply). Autonomous turns are still excluded on either
        channel — their fragments must never leak into a user-facing stream.
        """
        if bucket[1] not in (DELIVERY_GUI, DELIVERY_VOICE):
            return False
        return self._initiator_by_bucket.get(bucket, "user") in CHAT_INITIATORS

    def _is_turn_complete(self, bucket: Tuple[str, str]) -> bool:
        return self._completed_by_bucket.get(bucket, False)

    def _mark_turn_complete(self, bucket: Tuple[str, str]) -> None:
        self._completed_by_bucket[bucket] = True

    def mark_turn_pending(self, bucket: Tuple[str, str]) -> None:
        """Called by LimbMessageHandler when a turn is dispatched, to reset any
        stale completion flag left over from a prior turn on this bucket."""
        self._completed_by_bucket[bucket] = False

    def pin_conversation(self, bucket: Tuple[str, str], conversation_id: Optional[str]) -> None:
        """Called by LimbMessageHandler at USER_INPUT so all downstream broadcasts
        (fragments, thinking, turn-complete) for this bucket carry the right id."""
        self._conversation_id_by_bucket[bucket] = conversation_id

    def pin_turn_client(
        self,
        bucket: Tuple[str, str],
        client_type: str,
        conversation_id: Optional[str] = None,
        user_content: Any = None,
        user_text: Optional[str] = None,
        user_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record, on the TURN, which client started it and what it must persist.

        Called by LimbMessageHandler at USER_INPUT with the type of the socket the
        message actually arrived on — so a different client connecting mid-flight
        can no longer change how this turn behaves (issue #711).
        """
        self._persist_by_bucket[bucket] = TurnPersistState(
            client_type=client_type or "desktop",
            conversation_id=conversation_id,
            user_content=user_content,
            user_text=user_text,
            user_metadata=user_metadata,
        )

    def _turn_client_type(self, bucket: Tuple[str, str]) -> str:
        """Client type captured when this bucket's turn started."""
        entry = self._persist_by_bucket.get(bucket)
        return entry.client_type if entry else "desktop"

    # ------------------------------------------------------------------
    # EVENT BUS BRIDGES — map domain Events -> outgoing socket payloads
    # ------------------------------------------------------------------

    def setup_event_subscriptions(self):
        """Register internal event handlers to the system event bus."""
        from src.application.events import (
            TurnCompleted, TurnStarted, ThinkingUpdate, ErrorOccurred,
            ResponseFragment, ToolStarted, ToolCompleted, ModelLoading,
            TokenUsageUpdate, TaskStoppedByUser, CapabilitySuggestionRaised
        )
        from src.application.events.events import ExecutionPlanEvent, TurnCompleteEvent, TurnModeResolved, StatusUpdate, ToolsMenuEvent, WaitNarratorEvent

        self._event_bus.subscribe(TurnCompleted, self._on_event_turn_end)
        # TurnCompleteEvent is published by turn_coordinator (local/LangGraph path).
        # TurnCompleted is published by conversation_orchestrator (cloud path).
        # Both must be handled so the final non-fragment ai_response reaches the client.
        self._event_bus.subscribe(TurnCompleteEvent, self._on_event_turn_complete_event)
        self._event_bus.subscribe(TurnStarted, self._on_event_turn_start)
        self._event_bus.subscribe(TaskStoppedByUser, self._on_event_stop_active_task)
        self._event_bus.subscribe(ThinkingUpdate, self._on_event_thinking)
        self._event_bus.subscribe(ToolStarted, self._on_event_tool_start)
        self._event_bus.subscribe(ToolCompleted, self._on_event_tool_end)
        self._event_bus.subscribe(ErrorOccurred, self._on_event_error)
        self._event_bus.subscribe(ResponseFragment, self._on_event_token)
        self._event_bus.subscribe(ModelLoading, self._on_event_model_loading)
        self._event_bus.subscribe(TokenUsageUpdate, self._on_event_usage)
        self._event_bus.subscribe(ExecutionPlanEvent, self._on_event_execution_plan)
        self._event_bus.subscribe(TurnModeResolved, self._on_event_turn_mode_resolved)
        self._event_bus.subscribe(StatusUpdate, self._on_event_status)
        self._event_bus.subscribe(ToolsMenuEvent, self._on_event_tools_menu)
        self._event_bus.subscribe(WaitNarratorEvent, self._on_event_wait_narrator)
        self._event_bus.subscribe(CapabilitySuggestionRaised, self._on_event_capability_suggestion)

        logger.info("[ UI STATE ] : Subscribed event listeners to EventBus.")

    def _on_event_turn_end(self, e):
        # Cloud path (TurnCompleted). Resolve the bucket from the turn context's own
        # session_key/delivery so a concurrent autonomous turn's completion can't be
        # mistaken for this one.
        delivery = DELIVERY_GUI
        session_id = self._session_now()
        if e.turn_context:
            delivery = getattr(e.turn_context, "delivery", None) or resolve_delivery(
                getattr(e.turn_context, "category", None)
            )
            session_id = getattr(e.turn_context, "session_key", None) or session_id
            if getattr(e.turn_context, "platform", None) == "telegram" or (session_id and ":telegram:" in session_id):
                delivery = DELIVERY_TELEGRAM
        if not is_gui_delivery(delivery):
            return
        self._create_task(self._broadcast_turn_completed(e, bucket=(session_id, delivery)))

    def _on_event_turn_complete_event(self, e):
        """Handle TurnCompleteEvent (published by turn_coordinator / LangGraph path)."""
        bucket = self._bucket_from_event(e)
        if not is_gui_delivery(bucket[1]) and bucket[1] != DELIVERY_VOICE:
            return
        initiator = self._initiator_by_bucket.get(bucket, "user")
        self._create_task(self._broadcast_turn_complete_event(e, initiator=initiator, bucket=bucket))

    def _on_event_turn_start(self, e):
        bucket = self._bucket_from_event(e)
        initiator_str = initiator_to_str(e.initiator)
        # Record the initiator for EVERY turn so the streaming-event handlers can tell a
        # user chat turn from an autonomous one running on the same (session, delivery)
        # bucket. Because the bucket includes session_id, an autonomous turn no longer
        # clobbers an active user turn's initiator.
        self._initiator_by_bucket[bucket] = initiator_str
        self._completed_by_bucket[bucket] = False
        # Intentionally do NOT seed the thinking stream with a "..." placeholder.
        # That literal used to concatenate onto the first real reasoning tokens
        # ("...Aura farmers…"). The live header already shows Processing/Thinking
        # via StatusUpdate → handleLiveStatus.

    def _on_event_stop_active_task(self, e):
        logger.warning("[ UI STATE ] : Task stopped by user event received. Interrupting GUI turn...")
        task = self._limb.active_tasks.get(DELIVERY_GUI)
        if task and not task.done():
            task.cancel()
            logger.warning("[ UI STATE ] : GUI turn task cancelled.")

        # Broadcast status update back to client
        self._create_task(self._broadcast({
            "type": "status_update",
            "state": "ready"
        }))

    def _on_event_thinking(self, e):
        bucket = self._bucket_from_event(e)
        if not self._is_chat_turn(bucket):
            return
        logger.debug(f"[ WS ] ThinkingUpdate: {len(e.thinking_text)} chars")
        initiator = self._initiator_by_bucket.get(bucket, "user")
        self._create_task(self._broadcast_thinking_update(
            e.thinking_text,
            initiator=initiator,
            append=e.append,
            thinking_type=e.thinking_type,
            agent_name=e.agent_name,
            parent_id=e.parent_id,
        ))

    def _on_event_turn_mode_resolved(self, e):
        if e.source != "auto_router":
            return
        if not is_gui_delivery(self._delivery_from_event(e)):
            return
        from src.shared.turn_mode import normalize_turn_mode, ui_mode_for_canonical
        canonical = normalize_turn_mode(e.mode)
        ui_mode = ui_mode_for_canonical(canonical)
        self._create_task(self._broadcast_turn_mode(ui_mode, canonical, e.source, e.confidence))

    def _on_event_status(self, e):
        """Forward a transient phase label (StatusUpdate) to the active GUI limb as an
        ``ai_thought`` frame with ``state='status'``. The web client renders it in the
        live working indicator so the pre-first-token gap (tool selection, model load)
        doesn't look frozen. Also allows voice delivery (mirrors ``_is_streamable_turn``):
        a voice turn that escalates to the heavy agent pipeline gets no other feedback
        while it waits, so these labels are the only narration available. Autonomous
        turns are still excluded on either channel — their status must never leak into
        a user-facing stream."""
        delivery = getattr(e, "delivery", None)
        if delivery is not None and delivery not in (DELIVERY_GUI, DELIVERY_VOICE):
            return
        label = getattr(e, "status", "") or ""
        if not label:
            return
        self._create_task(self._broadcast_status(
            label, delivery=delivery, client_turn_id=getattr(e, "client_turn_id", None),
        ))

    async def _broadcast_status(
        self, label: str, delivery: Optional[str] = None, client_turn_id: Optional[str] = None,
    ):
        try:
            payload: Dict[str, Any] = {
                "state": "status",
                "text": label,
                "initiator": self.current_initiator() or "user",
                "delivery": delivery or DELIVERY_GUI,
            }
            if client_turn_id:
                payload["turn_id"] = client_turn_id
            msg = ChadMessage(MessageType.AI_THOUGHT, payload)
            await self._send(msg)
        except Exception as ex:
            logger.debug(f"[ UI STATE ] : Failed to broadcast status '{label}': {ex}")

    async def _broadcast_turn_mode(self, ui_mode: str, mode: str, source: str, confidence):
        try:
            msg = ChadMessage(MessageType.TURN_MODE, {
                "ui_mode": ui_mode,
                "mode": mode,
                "source": source,
                "confidence": confidence,
            })
            await self._send(msg)
        except Exception as ex:
            logger.error(f"[ UI STATE ] Failed to broadcast turn mode: {ex}")

    def _on_event_error(self, e):
        self._create_task(self._broadcast_error(e.error_message))

    def _on_event_capability_suggestion(self, e):
        # Not turn-scoped — the capability-gap analyzer runs on its own trigger
        # (CapabilityGapTracker), independent of any in-flight turn's bucket. Push
        # straight to the active client so the Suggestions panel/badge updates live.
        self._create_task(self._broadcast_capability_suggestion(e))

    async def _broadcast_capability_suggestion(self, e):
        try:
            payload = {
                "id": e.suggestion_id,
                "kind": e.kind,
                "title": e.title,
                "description": e.description,
                "rationale": e.rationale,
                "confidence": e.confidence,
                "cluster_size": e.cluster_size,
                "status": "pending",
            }
            msg = ChadMessage(MessageType.CAPABILITY_SUGGESTION, payload)
            await self._send(msg)
        except Exception as ex:
            logger.error(f"[ UI STATE ] Failed to broadcast capability suggestion: {ex}")

    def _on_event_token(self, e):
        bucket = self._bucket_from_event(e)
        if not self._is_streamable_turn(bucket):
            return
        initiator = self._initiator_by_bucket.get(bucket, "user")
        self._create_task(self._broadcast_response_fragment(
            e.text, initiator=initiator, bucket=bucket, client_turn_id=e.client_turn_id,
        ))

    def _on_event_usage(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        self._create_task(self._broadcast_token_usage(e))

    def _on_event_tool_start(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        self._create_task(self._broadcast_tool_start(e.tool_name, e.args, e.correlation_id, e.display_text, e.parent_id))

    def _on_event_tool_end(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        self._create_task(self._broadcast_tool_end(
            e.tool_name, e.result, e.success, e.error, e.correlation_id, e.display_text, e.parent_id,
            getattr(e, "awaiting_user", False),
        ))

    def _on_event_model_loading(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        self._create_task(self._broadcast({
            "type": "thinking_event",
            "event": {
                "type": "model_loading",
                "name": e.model_name,
                "provider": e.provider,
                "text": f"Loading {e.model_name}..."
            }
        }))

    def _on_event_execution_plan(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        self._create_task(self._broadcast_execution_plan(e))

    def _on_event_tools_menu(self, e):
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        delivery = getattr(e, "delivery", None)
        if delivery is not None and delivery not in (DELIVERY_GUI, DELIVERY_VOICE):
            return
        self._create_task(self._broadcast_tools_menu(e))

    def _on_event_wait_narrator(self, e):
        """GUI text chat only — never spoken on the voice lane."""
        if not self._is_chat_turn(self._bucket_from_event(e)):
            return
        if self._delivery_from_event(e) != DELIVERY_GUI:
            return
        self._create_task(self._broadcast_wait_narrator(e))

    # ------------------------------------------------------------------
    # BROADCAST WORKERS
    # ------------------------------------------------------------------

    async def _send(self, msg: ChadMessage) -> None:
        """Fan out one frame to every connected viewer.

        Every frame this class emits goes through here. Sending to the single
        ``active_websocket`` meant the phone stopped receiving its own turn's tokens
        and final answer the moment the desktop GUI reconnected and took the active
        slot — #709 only fanned out the tool/thinking frames raised from
        remote_factory, not the ones raised here (issue #711).
        """
        await self._limb.send_to_active_limb(msg)

    async def _persist_browser_turn(
        self, bucket: Tuple[str, str], response_text: str, turn_id: Optional[str],
        metrics: dict, initiator: str = "user",
    ):
        """Persist a completed browser/PWA turn server-side (issue #189).

        Desktop turns are persisted by the Limb (GUIChadLimb.handle_message); this
        is the Brain-side equivalent for clients that have no local repo. Writes the
        stashed user message first, then the assistant reply, and derives the title
        for a fresh "New Chat" — mirroring the Limb's ordering and metadata so phone
        chats are durable and feed the same downstream pipelines as desktop chats.

        Everything it writes comes from THIS turn's bucket, so a second turn starting
        on another client can't redirect the write (issue #711).
        """
        pending = self._persist_by_bucket.get(bucket)
        if not pending:
            return
        conversation_id = pending.conversation_id
        if not conversation_id:
            self._persist_by_bucket.pop(bucket, None)
            return
        history_repo = self._limb.history_repo
        try:
            # 1. User message
            if pending.user_content is not None:
                await history_repo.append_message(
                    conversation_id, "user", pending.user_content, metadata=pending.user_metadata
                )
                conv = await history_repo.get_conversation(conversation_id)
                if conv and conv.get("title") == "New Chat" and pending.user_text:
                    title = history_repo.derive_title(pending.user_text)
                    await history_repo.update_conversation_title(conversation_id, title)

            # 2. Assistant reply
            if response_text and response_text.strip():
                turn_coord = getattr(self._limb, "turn_coordinator", None)
                thoughts = getattr(turn_coord, "last_turn_thinking_events", None) if turn_coord else None
                metadata = {
                    "initiator": initiator or "user",
                    "turn_duration_ms": metrics.get("turn_duration_ms"),
                    "prompt_tokens": metrics.get("prompt_tokens"),
                    "completion_tokens": metrics.get("completion_tokens"),
                    "total_tokens": metrics.get("total_tokens"),
                    "reasoning_tokens": metrics.get("reasoning_tokens"),
                    "context_window": metrics.get("context_window"),
                    "ttft": metrics.get("ttft"),
                    "stop_reason": metrics.get("stop_reason"),
                    "speed": metrics.get("speed"),
                }
                if thoughts:
                    metadata["thoughts"] = thoughts
                    artifacts = [
                        e for e in thoughts
                        if e.get("type") == "tool_end" and e.get("success") and e.get("download_token")
                    ]
                    if artifacts:
                        metadata["artifacts"] = artifacts
                written = await history_repo.append_message(
                    conversation_id, "assistant", response_text, metadata=metadata, message_id=turn_id
                )
                # A failed write here is invisible from the client: the reply already
                # streamed, so the turn looks fine until a reload shows the question with
                # no answer. The backends return {} on failure rather than raising, so this
                # went unnoticed for a while — say it loudly.
                if not written:
                    logger.error(
                        "[ PERSIST ] Assistant reply for conversation %s was NOT stored "
                        "(backend rejected the write) — this turn will look half-empty on "
                        "reload.", conversation_id,
                    )
        except Exception as e:
            logger.error(f"[ PERSIST ] Failed to persist browser turn: {e}")
        finally:
            # Drop this turn's record so a later completion event can't write it twice.
            self._persist_by_bucket.pop(bucket, None)

    async def _broadcast(self, payload: dict):
        try:
            await self._limb.send_payload_to_all(payload)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed to broadcast payload: {e}")

    async def _broadcast_turn_complete_event(self, event, initiator: str = "user", bucket: Tuple[str, str] = ("", DELIVERY_GUI)):
        """Broadcast final ai_response for TurnCompleteEvent (turn_coordinator / LangGraph path).

        Always sends a non-fragment ai_response so clients know the turn is done,
        even when the planner strips all text (e.g. pure [[PLAN]] response → response="").
        Voice delivery sends a minimal payload (no GUI stream); GUI gets full metrics.
        System/autonomous turns (no conversation_id) are routed to AUTONOMOUS_NOTIFICATION
        so they never pollute the active chat session.
        """
        delivery = bucket[1]
        if not is_gui_delivery(delivery) and delivery != DELIVERY_VOICE:
            return
        response_text = getattr(event, "response", None) or ""
        client_turn_id = getattr(event, "client_turn_id", None)

        # Suppress heartbeat noise
        if response_text.strip().upper() == "HEARTBEAT_OK":
            return

        # Deliberately NOT gated on having a viewer. This used to bail early when no
        # socket was attached, which silently skipped the persistence at the bottom —
        # defeating the whole point of letting a disconnected browser's turn run to
        # completion (see LimbCoordinator.unregister_websocket). A phone backgrounded
        # mid-turn drops its socket, so that was the common case, not the rare one.
        # `_send` is a no-op without viewers, so the sends below are safe (issue #711).
        if self._is_turn_complete(bucket):
            logger.debug(f"[ UI STATE ] Suppressing duplicate TurnCompleteEvent ({bucket})")
            return

        self._mark_turn_complete(bucket)

        # Authoritative initiator for this bucket (tracked at TurnStarted).
        initiator = self._initiator_by_bucket.get(bucket, initiator)

        # Resolve the conversation_id for this bucket (set by USER_INPUT routing). An
        # autonomous turn runs under its own session_id bucket and never pins one.
        conversation_id = self._conversation_id_by_bucket.get(bucket)

        # Autonomous turns (cron/heartbeat, skill, gmail webhook) must NEVER appear as a
        # chat bubble — regardless of any stale conversation_id. Route to the notification
        # channel instead. Only USER/FOLLOWUP turns proceed into the chat.
        if is_gui_delivery(delivery) and is_autonomous_initiator(initiator):
            if response_text.strip():
                notif = ChadMessage(MessageType.AUTONOMOUS_NOTIFICATION, {"text": response_text})
                await self._send(notif)
                logger.info(f"[ UI STATE ] Autonomous turn routed to AUTONOMOUS_NOTIFICATION ({len(response_text)} chars)")
                # Web Push so the phone sees it even when the app is backgrounded.
                # Best-effort — never let a push failure abort notification delivery.
                try:
                    from src.remote.push_service import get_push_service
                    self._create_task(get_push_service().send(
                        title="Chad",
                        body=response_text[:120],
                        tag="autonomous",
                    ))
                except Exception as push_exc:
                    logger.warning("[ UI STATE ] autonomous push notify failed (non-fatal): %s", push_exc)
                self._create_task(self._write_autonomous_dashboard_entry(initiator, response_text))
            return

        try:
            if is_gui_delivery(delivery):
                await self._flush_thinking()
                turn_metrics = getattr(self._limb.turn_coordinator, "last_turn_metrics", {}) if self._limb.turn_coordinator else {}
                pending_approval = getattr(event, "pending_approval", None)

                # Interactive clarifying questions: push the card BEFORE the final
                # ai_response so the UI can attach it to the same bubble.
                if pending_approval and is_gui_delivery(delivery):
                    # Session isolation (issue #269): the approval card MUST carry the
                    # bucket's conversation_id so the Limb can attribute it to exactly
                    # one chat. Without it the card is session-less and leaks onto
                    # whatever chat the user is viewing when it lands. Copy so we never
                    # mutate the event's own dict.
                    approval_payload = dict(pending_approval)
                    if conversation_id:
                        approval_payload["conversation_id"] = conversation_id
                    approval_msg = ChadMessage(MessageType.TOOL_APPROVAL_REQUEST, approval_payload)
                    await self._send(approval_msg)
                    logger.info(
                        f"[ UI STATE ] Sent tool_approval_request for "
                        f"'{pending_approval.get('tool_name')}' (token={pending_approval.get('token')})"
                    )
                    # Web Push is a best-effort side channel — it must NEVER be able to
                    # abort the authoritative ai_response send below. get_push_service()
                    # lazily constructs the singleton (and can raise, e.g. VAPID keygen
                    # incompat), so guard the whole thing.
                    try:
                        from src.remote.push_service import get_push_service
                        tool_name = pending_approval.get("tool_name", "a tool")
                        self._create_task(get_push_service().send(
                            title="Chad has a question",
                            body=f"Answer clarifying questions for {tool_name}",
                            tag="approval",
                            require_interaction=True,
                        ))
                    except Exception as push_exc:
                        logger.warning("[ UI STATE ] approval push notify failed (non-fatal): %s", push_exc)

                payload = {"text": response_text, "initiator": initiator, "delivery": delivery}
                if conversation_id:
                    payload["conversation_id"] = conversation_id
                if client_turn_id:
                    payload["turn_id"] = client_turn_id
                if turn_metrics:
                    payload.update(turn_metrics)
                if getattr(event, "awaiting_approval", False):
                    payload["awaiting_approval"] = True
                    payload["stop_reason"] = "awaiting_approval"
                elif getattr(event, "awaiting_input", False):
                    payload["awaiting_input"] = True
                    payload["stop_reason"] = "awaiting_input"
                elif pending_approval:
                    payload["awaiting_input"] = True
                    payload["stop_reason"] = "awaiting_input"
            else:
                # Voice / silent: final line only — no thinking flush, no token metrics in chat
                payload = {
                    "text": response_text,
                    "delivery": delivery,
                    "initiator": initiator,
                }
                if client_turn_id:
                    payload["turn_id"] = client_turn_id

            msg = ChadMessage(MessageType.AI_RESPONSE, payload)
            await self._send(msg)
            logger.info(
                f"[ UI STATE ] Final ai_response ({delivery}) via TurnCompleteEvent "
                f"({len(response_text)} chars)"
            )

            if self._turn_client_type(bucket) == "browser" and is_gui_delivery(delivery):
                await self._persist_browser_turn(
                    bucket,
                    response_text,
                    client_turn_id,
                    getattr(self._limb.turn_coordinator, "last_turn_metrics", {}) if self._limb.turn_coordinator else {},
                    initiator=initiator,
                )

        except Exception as e:
            logger.error(f"[ UI STATE ] TurnCompleteEvent broadcast error: {e}")

    async def _broadcast_turn_completed(self, event, bucket: Tuple[str, str] = ("", DELIVERY_GUI)):
        """Broadcast significant turn completion events back to current active limb."""
        if not event or not event.turn_context:
            return

        if not is_gui_delivery(bucket[1]):
            return
        turn = event.turn_context
        response_text = event.model_response or turn.model_response

        if not response_text:
            return

        # OpenClaw Suppressions
        if response_text.strip().upper() == "HEARTBEAT_OK":
            return

        # Not gated on having a viewer — an unwatched turn must still complete and
        # persist. See the matching note in _broadcast_turn_complete_event (issue #711).
        if self._is_turn_complete(bucket):
            logger.debug("[ UI STATE ] Suppressing duplicate broadcast of TurnCompleted")
            return
        self._mark_turn_complete(bucket)
        try:
            # turn.initiator is authoritative here (carried on the turn context).
            initiator = initiator_to_str(turn.initiator)
            conversation_id = self._conversation_id_by_bucket.get(bucket)

            # Autonomous turns (cron/heartbeat, skill, gmail webhook) go to the
            # notification channel, never the active chat — even if a stale
            # conversation_id is still pinned from a prior user turn.
            if is_autonomous_initiator(initiator):
                notif = ChadMessage(MessageType.AUTONOMOUS_NOTIFICATION, {"text": response_text})
                await self._send(notif)
                logger.info(f"[ UI STATE ] Autonomous TurnCompleted routed to AUTONOMOUS_NOTIFICATION")
                self._create_task(self._write_autonomous_dashboard_entry(initiator, response_text))
                return

            await self._flush_thinking()
            # Retrieve last turn metrics from turn coordinator
            turn_metrics = getattr(self._limb.turn_coordinator, 'last_turn_metrics', {}) if self._limb.turn_coordinator else {}

            payload = {
                "text": response_text,
                "initiator": initiator,
                "delivery": DELIVERY_GUI,
            }
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if turn_metrics:
                payload.update(turn_metrics)

            msg = ChadMessage(MessageType.AI_RESPONSE, payload)
            await self._send(msg)

            if self._turn_client_type(bucket) == "browser":
                await self._persist_browser_turn(
                    bucket, response_text, getattr(turn, "turn_id", None), turn_metrics, initiator=initiator
                )
        except Exception as e:
            logger.error(f"[ UI STATE ] Broadcast turn_completed error: {e}")

    async def _write_autonomous_dashboard_entry(self, initiator: str, response_text: str) -> None:
        """Write a durable Dashboard feed entry for a completed autonomous turn.

        Called fire-and-forget (via create_task) after the AUTONOMOUS_NOTIFICATION is
        sent, so a Supabase latency spike never delays the live toast.  Entries expire
        after 48 h so the feed does not accumulate indefinitely.
        """
        try:
            from src.core.intelligence import dashboard_feed
            title = self._INITIATOR_TITLES.get(initiator.lower(), f"Autonomous run ({initiator})")
            body = response_text[:3000] if len(response_text) > 3000 else response_text
            await dashboard_feed.append_entry(
                kind="status",
                title=title,
                body=body,
                metadata={"initiator": initiator, "auto_run": True},
                expires_in_hours=48,
            )
        except Exception as e:
            logger.debug(f"[ UI STATE ] Dashboard entry write failed (non-fatal): {e}")

    async def _broadcast_token_usage(self, event):
        if not self._limb.has_viewers:
            return
        try:
            payload = {
                "prompt_tokens": event.prompt_tokens,
                "completion_tokens": event.completion_tokens,
                "total_tokens": event.total_tokens,
                "reasoning_tokens": event.reasoning_tokens,
                "context_window": event.context_window,
                "speed": getattr(event, "speed", None),
                "ttft": getattr(event, "ttft", None),
                "stop_reason": getattr(event, "stop_reason", None),
                "pre_model_breakdown": getattr(event, "pre_model_breakdown", None),
            }
            msg = ChadMessage(MessageType.TOKEN_USAGE, payload)
            await self._send(msg)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed to broadcast token usage: {e}")

    async def _broadcast_thinking_update(self, text, initiator=None, append=True, thinking_type="thinking",
                                         agent_name=None, parent_id=None):
        if not text:
            return

        # A change of subagent attribution must not be coalesced into the prior block,
        # or the UI would mis-attribute one agent's thoughts to another. Flush first.
        attribution_changed = (
            self._pending_thinking_broadcast
            and (agent_name != self._pending_thinking_agent_name
                 or parent_id != self._pending_thinking_parent_id)
        )
        if attribution_changed:
            await self._flush_thinking()

        if append and self._pending_thinking_broadcast:
            self._pending_thinking_broadcast += text
        else:
            self._pending_thinking_broadcast = text

        self._pending_thinking_initiator = initiator
        self._pending_thinking_type = thinking_type
        self._pending_thinking_append = append
        self._pending_thinking_agent_name = agent_name
        self._pending_thinking_parent_id = parent_id

        if self._limb.has_viewers:
            await self._flush_thinking()

    async def _flush_thinking(self):
        if self._pending_thinking_broadcast is None or not self._limb.has_viewers:
            return

        try:
            payload = {
                "state": self._pending_thinking_type,
                "text": self._pending_thinking_broadcast,
                "append": self._pending_thinking_append,
                "initiator": self._pending_thinking_initiator or "user"
            }
            if self._pending_thinking_agent_name:
                payload["agent_name"] = self._pending_thinking_agent_name
            if self._pending_thinking_parent_id:
                payload["parent_id"] = self._pending_thinking_parent_id
            msg = ChadMessage(MessageType.AI_THOUGHT, payload)

            await self._send(msg)
            self._last_thinking_broadcast_time = asyncio.get_event_loop().time()

            # Reset
            self._pending_thinking_broadcast = None
            self._pending_thinking_initiator = None
            self._pending_thinking_append = True
            self._pending_thinking_agent_name = None
            self._pending_thinking_parent_id = None
        except Exception as e:
            # Graceful exit if transport state mutated
            if any(x in str(e).lower() for x in ["close", "disconnect", "status"]):
                return
            logger.error(f"Error flushing thinking update: {e}")

    async def _broadcast_tool_start(self, tool_name, args, correlation_id=None, display_text=None, parent_id=None):
        if not self._limb.has_viewers:
            return
        try:
            msg = ChadMessage(MessageType.AI_THOUGHT, {
                "state": "tool_start",
                "name": tool_name,
                "tool_name": tool_name,
                "params": args,
                "correlation_id": correlation_id,
                "display_text": display_text,
                "parent_id": parent_id
            }, correlation_id=correlation_id)
            await self._send(msg)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed tool_start: {e}")

    async def _broadcast_tool_end(self, tool_name, result, success, error=None, correlation_id=None, display_text=None, parent_id=None, awaiting_user=False):
        if not self._limb.has_viewers:
            return
        try:
            raw = result if isinstance(result, dict) else {}
            # Prefer normalized wire fields when ToolCompleted carried wire_tool_result_fields.
            safe_result = raw.get("result", result) if isinstance(raw, dict) else result
            # When `result` is a short summary but sibling keys hold the real list/dict
            # payload (emails, files, …), append a capped JSON dump so live evals and
            # the GUI see the same evidence the model got (church_2h gmail grounded misses).
            if isinstance(raw, dict) and isinstance(safe_result, str) and len(safe_result) < 400:
                extras = {
                    k: v for k, v in raw.items()
                    if k not in (
                        "success", "error", "result", "hint",
                        "download_token", "filename",
                    )
                    and not str(k).startswith("_")
                    and v not in (None, "", [], {})
                }
                if extras:
                    import json as _json
                    try:
                        blob = _json.dumps(extras, default=str, ensure_ascii=False)
                    except Exception:
                        blob = str(extras)
                    if len(blob) > 4500:
                        blob = blob[:4500] + "...(truncated)"
                    safe_result = f"{safe_result}\n{blob}"
            if isinstance(safe_result, str) and len(safe_result) > 5000:
                safe_result = f"{safe_result[:100]}... (truncated)" if success else f"{safe_result[:2000]}... [TRUNCATED ERROR]"

            payload = {
                "state": "tool_end",
                "name": tool_name,
                "tool_name": tool_name,
                "result": safe_result,
                "success": success,
                "error": error,
                "correlation_id": correlation_id,
                "display_text": display_text,
                "parent_id": parent_id,
                "awaiting_user": awaiting_user or bool(raw.get("_pending_approval")),
            }
            # Full bodies for eval / grounding — do not drop content just because UI uses result.
            if isinstance(raw, dict):
                for key in ("content", "gather_brief", "brief", "steps", "gather_brief_chars", "tool_count"):
                    if key in raw and raw[key] is not None:
                        payload[key] = raw[key]
                if raw.get("download_token"):
                    payload["download_token"] = raw.get("download_token")
                if raw.get("filename"):
                    payload["filename"] = raw.get("filename")
                from src.tools.media.show_youtube_video import youtube_display_payload
                from src.tools.coding.artifact_display import artifact_display_payload
                from src.tools.maps.osm_tools import map_display_payload
                payload.update(youtube_display_payload(raw))
                payload.update(artifact_display_payload(tool_name, raw))
                payload.update(map_display_payload(raw))

            msg = ChadMessage(MessageType.AI_THOUGHT, payload, correlation_id=correlation_id)
            await self._send(msg)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed tool_end: {e}")

    async def _broadcast_execution_plan(self, event):
        if not self._limb.has_viewers:
            return
        try:
            from src.shared.ui_protocol import ACTIVITY_TYPE_PLAN, build_activity_plan_snapshot

            snapshot = build_activity_plan_snapshot(
                message_id=event.plan_id or event.turn_id or "plan",
                plan=event.plan,
                goal=event.goal,
                replace=getattr(event, "replace", True),
                gather_brief=getattr(event, "gather_brief", None),
            )
            msg = ChadMessage(MessageType.EXECUTION_PLAN, {
                **snapshot,
                "plan": event.plan,
                "plan_id": event.plan_id,
                "goal": event.goal,
                "gather_brief": getattr(event, "gather_brief", None),
                "activity_type": ACTIVITY_TYPE_PLAN,
            })
            await self._send(msg)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed execution_plan broadcast: {e}")

    async def _broadcast_tools_menu(self, event):
        """Forward resolved tool menu to the Limb as an ai_thought tools_menu frame."""
        if not self._limb.has_viewers:
            return
        try:
            tools = getattr(event, "tools", None) or []
            if not tools:
                return
            payload: Dict[str, Any] = {
                "state": "tools_menu",
                "tools": tools,
                "initiator": self.current_initiator() or "user",
            }
            if getattr(event, "shortlist_size", None) is not None:
                payload["shortlist_size"] = event.shortlist_size
            if getattr(event, "selection_path", None):
                payload["selection_path"] = event.selection_path
            msg = ChadMessage(MessageType.AI_THOUGHT, payload)
            await self._send(msg)
        except Exception as ex:
            logger.debug(f"[ UI STATE ] Failed tools_menu broadcast: {ex}")

    async def _broadcast_wait_narrator(self, event):
        """Forward wait-theater narrator lines to the Limb."""
        if not self._limb.has_viewers:
            return
        try:
            lines = getattr(event, "lines", None) or []
            if not lines:
                return
            payload: Dict[str, Any] = {
                "state": "wait_narrator",
                "lines": lines,
                "initiator": self.current_initiator() or "user",
            }
            msg = ChadMessage(MessageType.AI_THOUGHT, payload)
            await self._send(msg)
        except Exception as ex:
            logger.debug(f"[ UI STATE ] Failed wait_narrator broadcast: {ex}")

    async def _broadcast_response_fragment(
        self, text, initiator="user", bucket: Tuple[str, str] = ("", DELIVERY_GUI),
        client_turn_id: Optional[str] = None,
    ):
        if not self._limb.has_viewers:
            return
        try:
            payload: Dict[str, Any] = {
                "text": text,
                "fragment": True,
                "initiator": initiator,
                "delivery": bucket[1],
            }
            conversation_id = self._conversation_id_by_bucket.get(bucket)
            if conversation_id:
                payload["conversation_id"] = conversation_id
            if client_turn_id:
                payload["turn_id"] = client_turn_id
            msg = ChadMessage(MessageType.AI_RESPONSE, payload)
            await self._send(msg)
        except Exception as e:
            logger.error(f"[ UI STATE ] Failed fragment: {e}")

    async def _broadcast_error(self, message):
        try:
            msg = ChadMessage(MessageType.AI_RESPONSE, {"text": f"⚠️ System Error: {message}"})
            await self._send(msg)
        except Exception as e:
            if any(x in str(e).lower() for x in ["close", "disconnect"]):
                return
            logger.error(f"Error broadcasting error: {e}")
