"""
Remote Limb Coordinator
Bridges the brain's event bus to connected WebSocket clients.

A lightweight dispatch facade: owns the connection identity (active websocket,
client type) and the components bound post-boot (turn_coordinator,
tool_registry), and delegates the real work to two collaborators —
LimbMessageHandler (inbound message routing / turn dispatch / RPC) and
UIStateSyncer (EventBus -> outgoing socket payload bridging).
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional, Dict, Tuple

from src.remote.protocol import ChadMessage
from src.remote.limb_message_handler import LimbMessageHandler
from src.remote.ui_state_syncer import UIStateSyncer
from src.shared.logger import setup_logger

logger = setup_logger("LimbCoordinator")


@dataclass
class ConnectedClient:
    """One authenticated viewer, and everything true about *that* connection.

    Per-connection facts belong here, never as a field on the coordinator. A single
    shared ``_client_type`` was last-writer-wins across every client: the desktop GUI
    reconnects periodically, so it routinely landed last and a turn driven from the
    phone read ``desktop`` — which meant the Brain skipped persisting it and the
    desktop hadn't persisted it either, since the turn wasn't its own (issue #711).
    """

    websocket: Any
    client_type: str = "desktop"


class LimbCoordinator:
    def __init__(self, event_bus, initialization_ready_event: asyncio.Event):
        self._event_bus = event_bus
        self._initialization_ready = initialization_ready_event

        # Connection State
        # EVERY authenticated viewer, keyed by socket — not just the most recent one.
        # `_active_websocket` is a single slot, so with the desktop GUI and a phone both
        # connected, whichever registered last won and the other went silent. Turn output
        # fans out to all of these (see send_to_active_limb); clients already scope
        # rendering by conversation_id, so extra viewers are harmless.
        self._active_websocket = None
        self._clients: Dict[Any, ConnectedClient] = {}

        # Brain-side turn persistence (issue #189). The desktop Limb persists its
        # own turns (GUIChadLimb.handle_message); browser/PWA clients have no local
        # ChatHistoryRepository, so the Brain persists their turns here. The per-turn
        # user content and the initiating client's type are captured on the TURN
        # (UIStateSyncer, keyed by bucket) at USER_INPUT and written AFTER the turn
        # completes — same ordering the Limb uses, so dispatch never sees the live
        # message in DB history during its own turn (prevents LangGraph dual-load
        # duplication). They are deliberately NOT fields here: coordinator-wide
        # per-turn state is overwritten by whatever client speaks next (issue #711).
        from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
        self.history_repo = ChatHistoryRepository()

        # Components injected post-boot by RemoteFactory
        self.turn_coordinator = None
        self.tool_registry = None

        # Collaborators
        self.messages = LimbMessageHandler(self, event_bus, initialization_ready_event)
        self.ui_state = UIStateSyncer(self, event_bus)

    # ------------------------------------------------------------------
    # Shared state accessors — read by LimbMessageHandler / UIStateSyncer
    # ------------------------------------------------------------------

    @property
    def active_websocket(self):
        return self._active_websocket

    @property
    def client_type(self) -> str:
        """Type of the MOST RECENTLY registered connection.

        Kept for connect-time concerns (model preload) that legitimately act on
        "whoever just connected". NEVER use it to decide anything about a turn —
        an unrelated client connecting mid-flight would change the answer. Turn
        code reads the type captured on the turn's own bucket (issue #711).
        """
        return self.client_type_for(self._active_websocket)

    def client_type_for(self, websocket) -> str:
        """The client type of one specific connection."""
        entry = self._clients.get(websocket)
        return entry.client_type if entry else "desktop"

    @property
    def has_viewers(self) -> bool:
        """True while at least one client could still receive a broadcast.

        Not the same as ``active_websocket is not None``: unregistering the active
        socket clears that slot while other viewers remain connected.
        """
        return bool(self._clients) or self._active_websocket is not None

    @property
    def active_tasks(self) -> Dict[str, asyncio.Task]:
        return self.messages._active_tasks

    @property
    def pending_responses(self) -> Dict[str, asyncio.Future]:
        return self.messages.pending_responses

    # Backward-compatible aliases for whitebox tests / tooling that reach into
    # the per-bucket broadcast state directly.
    @property
    def _completed_by_bucket(self):
        return self.ui_state._completed_by_bucket

    @property
    def _conversation_id_by_bucket(self):
        return self.ui_state._conversation_id_by_bucket

    @property
    def _initiator_by_bucket(self):
        return self.ui_state._initiator_by_bucket

    def _delivery_from_event(self, event) -> str:
        return self.ui_state._delivery_from_event(event)

    def _is_chat_turn(self, bucket) -> bool:
        return self.ui_state._is_chat_turn(bucket)

    def _is_streamable_turn(self, bucket: Tuple[str, str]) -> bool:
        return self.ui_state._is_streamable_turn(bucket)

    def _is_turn_complete(self, bucket) -> bool:
        return self.ui_state._is_turn_complete(bucket)

    def _mark_turn_complete(self, bucket) -> None:
        self.ui_state._mark_turn_complete(bucket)

    # ------------------------------------------------------------------
    # Component wiring
    # ------------------------------------------------------------------

    def bind_components(self, turn_coordinator, tool_registry=None):
        """Inject core dependencies after factory construction completes."""
        self.turn_coordinator = turn_coordinator
        self.tool_registry = tool_registry
        logger.info("[ COORDINATOR ] : Core components bound.")

    def setup_event_subscriptions(self):
        """Register internal event handlers to the system event bus."""
        self.ui_state.setup_event_subscriptions()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def register_active_websocket(
        self,
        websocket,
        client_type: str = "desktop",
        preload_ui_mode: Optional[str] = None,
    ):
        """Link a newly authenticated websocket as the active interface.

        client_type distinguishes the desktop Limb (which persists its own turns)
        from a browser/PWA client (whose turns the Brain must persist — issue #189)
        and from the eval harness (``eval`` — preload agent/plan, never auto).
        """
        self._active_websocket = websocket
        self._clients[websocket] = ConnectedClient(
            websocket=websocket, client_type=client_type or "desktop"
        )
        self._connect_preload_ui_mode = preload_ui_mode
        logger.warning(
            f"[ COORDINATOR ] : Active WebSocket registered (client_type={client_type or 'desktop'}, "
            f"viewers={len(self._clients)})."
        )
        self._maybe_preload_on_connect()

    def _maybe_preload_on_connect(self) -> None:
        """Load main first, then aux models for the connect UI mode."""
        try:
            from src.core.intelligence.model_lifecycle import (
                resolve_connect_preload_ui_mode,
                schedule_warm,
            )
            # `client_type` (most-recently-registered) is the right read here: this
            # fires from register_active_websocket, about the client that just connected.
            ui_mode = resolve_connect_preload_ui_mode(
                self.client_type,
                getattr(self, "_connect_preload_ui_mode", None),
            )
            schedule_warm(turn_coordinator=self.turn_coordinator, ui_mode=ui_mode)
            logger.info(
                f"[ COORDINATOR ] Model preload scheduled "
                f"(ui_mode={ui_mode}, client_type={self.client_type})."
            )
        except Exception as e:
            logger.debug(f"[ COORDINATOR ] Preload on connect setup failed (non-fatal): {e}")


    async def unregister_websocket(self, websocket):
        """Handle clean-up steps when a websocket disconnects.

        A dropped socket is NOT automatically a reason to abandon the work. The turn runs
        entirely on this machine; the socket is just the viewport onto it. Two cases where
        cancelling was actively destructive:

        - **A phone.** iOS drops the WebSocket every time the PWA is backgrounded. Cancelling
          there meant glancing at another app killed the turn mid-flight — and because browser
          turns are persisted as a unit when they COMPLETE (issue #189, limb_message_handler),
          a cancelled turn writes nothing at all. The user came back to a conversation
          containing only whatever had finished before they looked away, which reads exactly
          like "my session got wiped".
        - **A socket that isn't even the active one.** This ran unconditionally, outside the
          identity check below, so a stale socket's late close event cancelled the turn
          belonging to the connection that had already replaced it. Reconnects routinely
          produce that ordering: the new socket registers, then the old one's close arrives.

        Desktop keeps the old behavior. Closing the desktop window is a deliberate "I'm done"
        in a way that backgrounding a phone is not, and the desktop Limb persists its own
        turns as they stream, so a cancelled turn there isn't silently lost.
        """
        # Drop it as a viewer regardless of whether it held the "active" slot — a socket
        # that's gone must not keep receiving turn output. Read its type BEFORE dropping
        # it; the disconnect policy below depends on which kind of client just left.
        entry = self._clients.pop(websocket, None)

        if self._active_websocket != websocket:
            # Stale/duplicate socket — whatever is running belongs to a live connection.
            logger.info("[ COORDINATOR ] : Ignoring unregister for a non-active WebSocket.")
            return

        client_type = entry.client_type if entry else "desktop"
        self._active_websocket = None
        logger.warning("[ COORDINATOR ] : Active WebSocket cleared.")

        if client_type == "browser":
            logger.warning(
                "[ COORDINATOR ] : Browser/PWA client disconnected — letting the in-flight "
                "turn finish so it persists and is there on reconnect."
            )
            return

        await self.messages.cancel_active_tasks()

    async def send_to_active_limb(self, msg: ChadMessage):
        """Ship a raw message to every connected viewer.

        Named "active limb" for historical reasons: this used to target the single
        ``_active_websocket``, which meant only the most recently connected client saw
        anything. With the desktop GUI and a phone both up, whichever registered last won —
        so a turn started on the phone rendered on the desktop, because the desktop happened
        to reconnect after the phone did. There is no ordering that makes a single slot
        correct when two viewers are legitimately open.

        Fans out to all of them instead. Clients already scope rendering by
        ``conversation_id``, so a second viewer receiving the stream is harmless; it just
        shows the same turn in both places.

        A send failure only drops that one viewer — a dead phone socket must not stop the
        desktop from receiving the rest of the turn.
        """
        payload = msg.to_json()

        async def _send_one(ws):
            # Ensure we support both framework implementations
            if hasattr(ws, "send"):
                await ws.send(payload)
            elif hasattr(ws, "send_text"):
                await ws.send_text(payload)

        await self._fan_out(_send_one)

    async def send_payload_to_all(self, payload: dict) -> None:
        """Fan out a raw dict payload (non-ChadMessage frames) to every viewer."""

        async def _send_one(ws):
            if hasattr(ws, "send_json"):
                await ws.send_json(payload)
            else:
                await ws.send(json.dumps(payload))

        await self._fan_out(_send_one)

    async def _fan_out(self, send_one) -> None:
        """Deliver to every connected viewer, dropping the ones that fail.

        A send failure only drops that one viewer — a dead phone socket must not stop
        the desktop from receiving the rest of the turn.
        """
        targets = list(self._clients) or (
            [self._active_websocket] if self._active_websocket else []
        )
        if not targets:
            logger.debug("[ COORDINATOR ] : Skipping broadcast (no connected viewers)")
            return

        dead = []
        for ws in targets:
            try:
                await send_one(ws)
            except Exception as e:
                logger.error(f"[ COORDINATOR ] : Failed to send to a viewer, dropping it: {e}")
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    async def broadcast_error(self, message: str) -> None:
        """Callback used by LimbMessageHandler for fast-track/routine RPC failures."""
        await self.ui_state._broadcast_error(message)

    def current_initiator(self) -> str:
        """Initiator of the turn whose code is currently running (resolved from
        contextvars). Used by the direct send_to_active_limb callbacks in
        remote_factory, which fire inside the turn's task context but outside the
        event-bus handlers that carry the bucket explicitly."""
        return self.ui_state.current_initiator()

    # ------------------------------------------------------------------
    # Inbound message routing — delegated to LimbMessageHandler
    # ------------------------------------------------------------------

    def handle_tool_response(self, msg: ChadMessage):
        self.messages.handle_tool_response(msg)

    async def wait_for_tool_response(self, correlation_id: str, timeout: float = 60.0) -> Optional[ChadMessage]:
        return await self.messages.wait_for_tool_response(correlation_id, timeout)

    async def handle_incoming_message(self, msg: ChadMessage, websocket):
        """Gateway entry point for fully authenticated inbound socket frames."""
        await self.messages.handle_incoming_message(msg, websocket)

    def handle_tool_approval_response(self, msg: ChadMessage):
        self.messages.handle_tool_approval_response(msg)

    async def route_message(self, msg: ChadMessage, websocket):
        """Dispatch protocol commands to the appropriate system coordinator."""
        await self.messages.route_message(msg, websocket)
