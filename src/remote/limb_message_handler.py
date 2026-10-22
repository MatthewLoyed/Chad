"""
Limb Message Handler
Processes fully-authenticated inbound WebSocket frames from a connected Limb:
tool RPC resolution, turn dispatch (USER_INPUT), fast-track commands, saved
routines, and live config updates. Owns the per-delivery turn task table and
the tool-response RPC futures; reads shared connection/turn state (active
websocket, turn_coordinator, tool_registry, pending browser-turn content)
through the owning LimbCoordinator.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from src.shared.logger import setup_logger
from src.remote.protocol import MessageType, ChadMessage
from src.shared.turn_delivery import DELIVERY_GUI, is_gui_delivery, resolve_delivery
from src.shared.domain_scope import normalize_domain

logger = setup_logger("LimbMessageHandler")


class LimbMessageHandler:
    def __init__(self, limb, event_bus, initialization_ready: asyncio.Event):
        self._limb = limb
        self._event_bus = event_bus
        self._initialization_ready = initialization_ready

        self.pending_responses: Dict[str, asyncio.Future] = {}
        self._active_tasks: Dict[str, asyncio.Task] = {}  # delivery -> turn task

    async def cancel_active_tasks(self) -> None:
        """Cancel all in-flight turn tasks — called when the client disconnects."""
        for delivery, task in list(self._active_tasks.items()):
            if task and not task.done():
                logger.warning(f"[ DISCONNECT ] : Cancelling {delivery} turn task...")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"[ DISCONNECT ] : {delivery} turn task cancelled.")
                except Exception as e:
                    logger.error(f"[ DISCONNECT ] : Error cancelling {delivery} task: {e}")
        self._active_tasks.clear()

    def handle_tool_response(self, msg: ChadMessage):
        """Resolve pending RPC futures for outbound tool requests."""
        cid = msg.id
        if cid in self.pending_responses:
            future = self.pending_responses.pop(cid)
            if not future.done():
                future.set_result(msg)
            logger.info(f"Tool response resolved for {cid}")
        else:
            logger.warning(f"Received tool response for unknown ID: {cid}")

    async def wait_for_tool_response(self, correlation_id: str, timeout: float = 60.0) -> Optional[ChadMessage]:
        """Await a response from the client RPC pipeline."""
        if self._limb.active_websocket is None:
            logger.warning(f"No limb connected, fast-failing tool RPC: {correlation_id}")
            return None

        future = asyncio.get_event_loop().create_future()
        self.pending_responses[correlation_id] = future

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Timed out waiting for tool response: {correlation_id}")
            return None
        finally:
            # Always clean up the pending future, regardless of timeout, cancellation, or success.
            # Without this, a CancelledError from task cancellation leaks the entry indefinitely.
            self.pending_responses.pop(correlation_id, None)

    async def handle_incoming_message(self, msg: ChadMessage, websocket):
        """Gateway entry point for fully authenticated inbound socket frames."""
        if msg.type == MessageType.TOOL_RESPONSE.value:
            self.handle_tool_response(msg)
        elif msg.type == MessageType.TOOL_APPROVAL_RESPONSE.value:
            self.handle_tool_approval_response(msg)
        else:
            await self.route_message(msg, websocket)

    def handle_tool_approval_response(self, msg: ChadMessage):
        """Record the user's decision for a pending requires_approval tool call.

        The actual resume happens via the normal USER_INPUT path the Limb sends
        right after this (a deterministic follow-up turn) — this just marks the
        approval_store entry resolved. The graph replays the saved atomic call
        directly before returning to the model (see src/infrastructure/tools/execution/executor_strategy.py).
        """
        from src.core.orchestration import approval_store
        token = msg.payload.get("token")
        decision = msg.payload.get("decision")
        if not token:
            logger.warning("[ APPROVAL ] tool_approval_response missing token")
            return
        ok = approval_store.resolve(token, decision)
        if not ok:
            logger.warning(f"[ APPROVAL ] Failed to resolve unknown/expired token {token}")

    def _handle_ui_turn_mode(self, msg: ChadMessage) -> None:
        """Warm auxiliary models when the user changes the chat mode dropdown."""
        ui_mode = (msg.payload.get("ui_mode") or msg.payload.get("mode") or "auto").strip().lower()
        try:
            from src.infrastructure.repositories.config_repository import ConfigRepository
            from src.shared.config_manager import ConfigManager
            ConfigRepository().update_preference("ui.default_turn_mode", ui_mode)
            ConfigManager._recursive_update(ConfigManager._config, {"ui": {"default_turn_mode": ui_mode}})
        except Exception:
            pass
        try:
            from src.core.intelligence.model_lifecycle import schedule_warm
            schedule_warm(turn_coordinator=self._limb.turn_coordinator, ui_mode=ui_mode)
            logger.info(f"[ COORDINATOR ] UI turn mode '{ui_mode}' — aux model preload scheduled.")
        except Exception as e:
            logger.debug(f"[ COORDINATOR ] UI turn mode preload failed (non-fatal): {e}")

    async def route_message(self, msg: ChadMessage, websocket):
        """Dispatch protocol commands to the appropriate system coordinator."""
        logger.info(f"Routing message: {msg.type}")

        if msg.type == MessageType.SET_UI_TURN_MODE.value:
            self._handle_ui_turn_mode(msg)
            return

        if msg.type == MessageType.USER_INPUT.value:
            await self._handle_user_input(msg, websocket)

        elif msg.type == MessageType.FAST_TRACK_INVOKE.value:
            await self._handle_fast_track_invoke(msg, websocket)

        elif msg.type == MessageType.WORKFLOW_RUN.value:
            await self._handle_workflow_run(msg, websocket)

        elif msg.type == MessageType.HEARTBEAT.value:
            await websocket.send(ChadMessage(MessageType.HEARTBEAT, {"status": "alive"}).to_json())

        elif msg.type == MessageType.SET_MODEL_CONFIG.value:
            self._handle_set_model_config(msg)

    async def _handle_user_input(self, msg: ChadMessage, websocket):
        # 1. Safety Check: Wait for System Ready
        if not self._initialization_ready.is_set():
            logger.warning("[ BRAIN ] : User input received while initializing. Waiting for boot...")
            try:
                await asyncio.wait_for(self._initialization_ready.wait(), timeout=45.0)
            except asyncio.TimeoutError:
                logger.error("[ BRAIN ] : Timeout waiting for system initialization!")
                return

        turn_coordinator = self._limb.turn_coordinator
        if not turn_coordinator:
            logger.error("[ BRAIN ] : Fatal - TurnCoordinator missing after boot initialization!")
            return

        text = msg.payload.get("text")
        content_blocks = msg.payload.get("content_blocks")
        image_base64 = msg.payload.get("image_base64")
        conversation_id = msg.payload.get("conversation_id")
        category = msg.payload.get("category")  # Restore Simple Ask Mode context
        # Real Limb clients never send this — defaults to "gui". The eval harness's live/workflow
        # lanes (evals/live.py) connect over this same WebSocket and set "eval" so the turn is
        # tagged priority="background" in the model-lifecycle coordinator (turn_coordinator.py)
        # instead of looking identical to (and free to evict) a real user's live conversation.
        platform = msg.payload.get("platform") or "gui"
        turn_id = msg.payload.get("turn_id")
        thinking_enabled = msg.payload.get("thinking_enabled")  # None | True | False
        delivery = resolve_delivery(category, msg.payload.get("delivery"))
        retry = bool(msg.payload.get("retry", False))
        keep_count = msg.payload.get("keep_count")
        # Domain-agent scope. Distinct from `category` above (turn lane / delivery mode) —
        # see src/shared/domain_scope.py. Only used to seed a brand-new conversation; for an
        # existing one the stored row wins (resolved below).
        requested_domain = normalize_domain(msg.payload.get("domain"))

        # --- RETRY TRUNCATION ---
        # When the user retries a prompt or regenerates a response, prune subsequent
        # messages so the retried turn executes with clean history.
        if retry and conversation_id:
            try:
                await self._limb.history_repo.truncate_conversation_after(conversation_id, keep_count)
                logger.info(f"[ RETRY ] Truncated conversation {conversation_id} keeping first {keep_count} messages")
            except Exception as e:
                logger.error(f"[ RETRY ] Failed to truncate conversation {conversation_id}: {e}")

        # --- BROWSER TURN PERSISTENCE (issue #189) ---
        # The desktop Limb persists its own turns; a browser/PWA client has no
        # local repo, so the Brain must. Stash the user message now and write it
        # (with the assistant reply) when the turn completes, so dispatch never
        # sees the live message in DB history during its own turn.
        #
        # Resolved from the socket this message ARRIVED ON, not from coordinator-wide
        # state. The desktop GUI reconnects periodically and used to overwrite a single
        # shared field, so a phone turn read "desktop", the Brain skipped persisting it,
        # and the desktop hadn't persisted it either — the turn silently vanished on
        # reload (issue #711).
        turn_client_type = self._limb.client_type_for(websocket)
        pending_user_content = None
        pending_user_text = None
        if turn_client_type == "browser" and is_gui_delivery(delivery):
            if not conversation_id:
                try:
                    from src.shared.config_manager import ConfigManager
                    conv = await self._limb.history_repo.create_conversation(
                        model=ConfigManager.get_active_model(), domain=requested_domain
                    )
                    conversation_id = conv.get("id")
                except Exception as e:
                    logger.error(f"[ PERSIST ] Failed to create conversation for browser turn: {e}")
            if conversation_id:
                pending_user_content = content_blocks if content_blocks else text
                pending_user_text = text

        # --- DOMAIN AGENT SCOPE ---
        # The stored conversation row is authoritative: a turn's memory scope must not be
        # settable by whatever the client happens to send, or a stale/spoofed payload could
        # pull another domain's private memories into this turn. The client value is only a
        # fallback for turns that have no conversation row at all (e.g. voice).
        turn_domain = requested_domain
        if conversation_id:
            try:
                turn_domain = await self._limb.history_repo.get_conversation_domain(conversation_id)
            except Exception as e:
                # Fail CLOSED to the general tier — an unreadable scope must never fall back
                # to the client's claim, which is exactly the spoof case above.
                logger.error(f"[ DOMAIN ] Failed to resolve domain for {conversation_id}: {e}")
                turn_domain = None

        # Set per-turn context for downstream adapter / graph
        from src.core.orchestration.context import (
            thinking_enabled_var,
            client_type_var,
            turn_delivery_var,
            turn_category_var,
            turn_id_var,
            session_id_var,
            domain_var,
        )
        thinking_enabled_var.set(thinking_enabled)
        # Lets tool routing know a browser turn can't be handed Limb-partitioned tools
        # (HybridToolExecutor) — the Brain runs them itself instead of proxying into a
        # client that has no local tool registry.
        client_type_var.set(turn_client_type)
        turn_delivery_var.set(delivery)
        turn_category_var.set(category)
        turn_id_var.set(turn_id)
        domain_var.set(turn_domain)

        # The bucket this turn's broadcast state lives under. Computed here so the
        # pin below matches the buckets that process_request's stream events will
        # resolve to (it pins the same session_id into session_id_var). (issue #208)
        turn_session_id = self._compute_session_id(delivery, conversation_id)
        session_id_var.set(turn_session_id)
        turn_bucket = (turn_session_id, delivery)

        # Pin the conversation scope for this bucket so all downstream broadcasts
        # (fragments, thinking, turn-complete) carry the right id.
        self._limb.ui_state.pin_conversation(turn_bucket, conversation_id)
        # Pin the initiating client + what this turn must persist onto the bucket, so
        # neither is readable as "whichever client is current" when the turn ends.
        pending_user_metadata = msg.payload.get("metadata")
        self._limb.ui_state.pin_turn_client(
            turn_bucket,
            turn_client_type,
            conversation_id=conversation_id,
            user_content=pending_user_content,
            user_text=pending_user_text,
            user_metadata=pending_user_metadata,
        )

        # --- AUTOMATIC FILE STAGING (Sandbox First Policy) ---
        staged_files = []
        sandbox_path = None
        if content_blocks:
            sandbox_path = (Path(os.getcwd()) / "sandbox").resolve()
            sandbox_path.mkdir(exist_ok=True)

            for block in content_blocks:
                if block.get('type') in ['document', 'image'] and block.get('name'):
                    source = block.get('source', {})
                    if source.get('type') == 'base64':
                        try:
                            import base64
                            file_data = base64.b64decode(source.get('data'))
                            file_name = os.path.basename(block['name'])
                            file_path = sandbox_path / file_name

                            with open(file_path, "wb") as f:
                                f.write(file_data)

                            staged_files.append(file_name)
                            logger.warning(f"[ BRAIN ] : Staged attachment to sandbox: {file_path}")
                        except Exception as e:
                            logger.error(f"Failed to stage file {block.get('name')}: {e}")

        if staged_files:
            staging_msg = (
                f"### [ SYSTEM : FILE STAGING ] ###\n"
                f"The following attachments have been automatically saved to your local `sandbox/` directory: {', '.join(staged_files)}\n"
                f"Current Sandbox Path: {sandbox_path}\n"
                f"Use `list_dir(DirectoryPath='sandbox')` to verify their existence and `archive_manager(action='unzip', source_path='sandbox/FILENAME')` to extract them.\n"
                f"DO NOT assume they are missing. They are physically on the disk now. If you need to watch a video, check the assignment instructions for links.\n"
                f"#################################\n\n"
            )
            text = (staging_msg + (text or "")) if text or staging_msg else text

        # 2. Dispatch Turn Request
        if not (text or content_blocks):
            logger.error("Received USER_INPUT but no text provided.")
            return

        logger.warning(
            f"[ BRAIN ] : Received user input (delivery={delivery}, category={category}, "
            f"domain={turn_domain or 'general'}): {text if text else '[Multimodal Only]'}"
        )

        async def process_text():
            try:
                from src.shared.context import RequestContext

                session_id = turn_session_id
                self._limb.ui_state.mark_turn_pending((session_id, delivery))

                ctx = RequestContext(
                    platform=platform,
                    session_id=session_id,
                    turn_id=turn_id,
                    category=category,
                    delivery=delivery,
                    domain=turn_domain,
                    retry=retry,
                    keep_count=keep_count,
                )

                # 3-Tier Alignment: Route through ConversationOrchestrator to respect session locking & lifecycle events
                if hasattr(turn_coordinator, 'session_manager') and turn_coordinator.session_manager:
                    sm = turn_coordinator.session_manager
                    if hasattr(sm, 'conversation_orchestrator') and sm.conversation_orchestrator:
                        model_response = await sm.conversation_orchestrator.process_request(
                            prompt=text,
                            context=ctx,
                            content_blocks=content_blocks,
                            image_base64=image_base64,
                            category=category
                        )
                    else:
                        logger.warning("[ COORDINATOR ] : Orchestrator missing, using direct dispatch fallback.")
                        model_response = await turn_coordinator.dispatch(
                            text,
                            ctx,
                            content_blocks=content_blocks,
                            image_base64=image_base64,
                            category=category
                        )
                else:
                    logger.warning("[ COORDINATOR ] : Session manager missing, using direct dispatch fallback.")
                    model_response = await turn_coordinator.dispatch(
                        text,
                        ctx,
                        content_blocks=content_blocks,
                        image_base64=image_base64,
                        category=category
                    )

                if model_response and model_response.startswith("⚠️"):
                    logger.warning(f"[ SERVER ] Orchestrator returned error: {model_response}")
                    error_msg = ChadMessage(
                        MessageType.SYSTEM_ERROR,
                        {"error": model_response, "delivery": delivery, "turn_id": turn_id},
                    )
                    await websocket.send(error_msg.to_json())

            except asyncio.CancelledError:
                logger.warning(f"[ TURN ] : {delivery} turn task cancelled.")
                try:
                    if hasattr(turn_coordinator, 'session_manager') and turn_coordinator.session_manager:
                        sm = turn_coordinator.session_manager
                        if hasattr(sm, 'conversation_orchestrator') and sm.conversation_orchestrator:
                            await asyncio.shield(sm.conversation_orchestrator.cancel_turn(reason="user_requested"))
                except Exception as cleanup_err:
                    logger.error(f"Failed to cancel turn on orchestrator during task cancellation: {cleanup_err}")
                raise
            except Exception as e:
                logger.error(f"Text processing error: {e}", exc_info=True)
                error_msg = ChadMessage(
                    MessageType.SYSTEM_ERROR,
                    {"error": str(e), "delivery": delivery, "turn_id": turn_id},
                )
                await websocket.send(error_msg.to_json())

        # Cancel only the in-flight turn for this delivery channel
        existing = self._active_tasks.get(delivery)
        if existing and not existing.done():
            logger.warning(f"[ BRAIN ] : Interrupting in-flight {delivery} turn...")
            existing.cancel()
            try:
                await existing
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error interrupting {delivery} task: {e}")

        self._active_tasks[delivery] = asyncio.create_task(process_text())

    @staticmethod
    def _compute_session_id(delivery: str, conversation_id: Optional[str]) -> str:
        """The session key a GUI/voice turn dispatches under — must match the value
        ConversationOrchestrator.process_request pins into session_id_var, so the bucket
        pinned at USER_INPUT lines up with the buckets its stream events resolve to."""
        from src.core.intelligence.session_key_manager import SessionKeyManager
        from src.remote.turn_initiator import VOICE_SESSION_ID
        from src.shared.turn_delivery import DELIVERY_VOICE
        if delivery == DELIVERY_VOICE:
            return VOICE_SESSION_ID
        if conversation_id:
            return f"main:default:gui:{conversation_id}"
        return SessionKeyManager.DEFAULT_SESSION_KEY

    def _handle_set_model_config(self, msg: ChadMessage) -> None:
        logger.info(f"[ BRAIN ] : Received model config update: {json.dumps(msg.payload, indent=2)}")
        try:
            from copy import deepcopy
            from src.shared.config_manager import ConfigManager
            from src.infrastructure.repositories.config_repository import ConfigRepository

            patch = deepcopy(msg.payload)
            local = patch.get("local_models", {})
            # Older clients only send active_model. Synchronize the preferred
            # supervisor alias on the server as well as in current GUI clients.
            if local.get("active_model"):
                local["supervisor"] = deepcopy(local["active_model"])

            # 2. Persist config repo
            repo = ConfigRepository()
            prefs = repo.load('preferences', use_cache=False)
            ConfigManager._recursive_update(prefs, patch)
            if not repo.save('preferences', prefs):
                raise RuntimeError("Could not persist model configuration")
            ConfigManager._recursive_update(ConfigManager._config, patch)

            # 3. Publish ModelChanged event to hot-reload model coordinator / router
            from src.application.events import ModelChanged
            active = local.get("supervisor") or local.get("active_model", "")
            if isinstance(active, dict):
                new_model_name = active.get("name", "")
            else:
                new_model_name = str(active or "")
            self._event_bus.publish(ModelChanged(old_model=None, new_model=new_model_name))
            logger.info(f"[ BRAIN ] : ModelChanged event published for hot-reload. New model: {new_model_name}")
        except Exception as e:
            logger.error(f"[ BRAIN ] : Failed to apply config update: {e}", exc_info=True)

    async def _send_error(self, websocket, message: str) -> None:
        msg = ChadMessage(MessageType.AI_RESPONSE, {"text": f"⚠️ System Error: {message}"})
        await websocket.send(msg.to_json())

    async def _handle_fast_track_invoke(self, msg: ChadMessage, websocket):
        """Resolve an allowlisted '/<alias>' command and call execute_tool() directly,
        bypassing the planner/LLM entirely. Reuses the existing tool_start/tool_end event
        broadcast and the plain ai_response shape, so no new client-side rendering is needed."""
        from src.application.events import ToolStarted, ToolCompleted

        alias = msg.payload.get("alias")
        tool_registry = self._limb.tool_registry
        if not tool_registry:
            await self._send_error(websocket, "Fast-track unavailable: tool registry not bound.")
            return

        command = tool_registry.resolve_fast_track(alias)
        if not command:
            logger.warning(f"[ FAST TRACK ] Unknown or disallowed alias: {alias}")
            await self._send_error(websocket, f"Unknown fast-track command: /{alias}")
            return

        tool_name = command["tool"]
        params = command["params"]
        label = command["label"]
        correlation_id = msg.id

        self._event_bus.publish(ToolStarted(
            tool_name=tool_name, args=params, correlation_id=correlation_id, display_text=label,
        ))

        try:
            result = await tool_registry.execute_tool(tool_name, params)
            success = True
            error = None
        except Exception as e:
            logger.error(f"[ FAST TRACK ] '{alias}' failed: {e}", exc_info=True)
            result = None
            success = False
            error = str(e)

        self._event_bus.publish(ToolCompleted(
            tool_name=tool_name, result=result, success=success, error=error,
            correlation_id=correlation_id, display_text=label,
        ))

        summary = error if not success else self._summarize_fast_track_result(label, result)
        response_msg = ChadMessage(MessageType.AI_RESPONSE, {
            "text": summary,
            "initiator": "user",
            "delivery": DELIVERY_GUI,
        })
        await websocket.send(response_msg.to_json())

    @staticmethod
    def _summarize_fast_track_result(label: str, result: Any) -> str:
        if isinstance(result, dict):
            if result.get("result"):
                return str(result["result"])
            if result.get("error"):
                return str(result["error"])
        if result:
            return str(result)
        return f"{label} done."

    async def _handle_workflow_run(self, msg: ChadMessage, websocket):
        """Run a saved routine: each step's tool is executed in order via
        execute_tool(), skipping the LLM/planner entirely."""
        workflow_id = msg.payload.get("workflow_id")
        tool_registry = self._limb.tool_registry
        if not tool_registry:
            await self._send_error(websocket, "Routines unavailable: tool registry not bound.")
            return

        from src.infrastructure.repositories.workflow_repository import WorkflowRepository
        from src.core.routines.workflow_executor import execute_workflow_steps

        repo = WorkflowRepository()
        workflow = await repo.get_workflow(workflow_id)
        if not workflow:
            await self._send_error(websocket, f"Unknown routine: {workflow_id}")
            return

        name = workflow.get("name", "Routine")
        steps = workflow.get("steps") or []
        results, _ok = await execute_workflow_steps(
            tool_registry,
            steps,
            event_bus=self._event_bus,
            correlation_prefix=msg.id,
        )

        summary = f"Routine '{name}' finished:\n" + "\n".join(results) if results else f"Routine '{name}' has no steps."
        response_msg = ChadMessage(MessageType.AI_RESPONSE, {
            "text": summary,
            "initiator": "user",
            "delivery": DELIVERY_GUI,
        })
        await websocket.send(response_msg.to_json())
