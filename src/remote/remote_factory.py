"""
Remote System Factory
Encapsulates the construction, dependency injection, and async initialization 
for the Headless Brain server.
"""

import os
import logging
import asyncio
from pathlib import Path
from typing import Optional, Any

from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.infrastructure.tools import ToolRegistry
from src.remote.protocol import ChadMessage, MessageType
from src.shared.startup_timing import mark as _timing_mark, checkpoint as _timing_checkpoint

# Mock audio classes for headless environment
from src.core.audio.audio_handler import MockAudio

logger = setup_logger("RemoteFactory")

class RemoteFactory:
    """Handles dependency graph construction for the Remote Assistant instance."""

    @staticmethod
    async def initialize_remote_system(
        event_bus, 
        limb_coordinator, 
        initialization_ready_event: asyncio.Event
    ) -> dict:
        """
        Delayed initialization of the assistant core components.
        
        Builds the shared registries, constructs the hybrid tool executor,
        links them through SessionManager, and spins up autonomous services.
        """
        logger.warning("[ BOOT LOGIC ] : Beginning remote component initialization lifecycle...")

        # --- 1. Deferred Core Imports (Prevent startup cycle bottlenecks) ---
        from src.core.orchestration.session_manager import SessionManager
        from src.core.monitoring.heartbeat_service import HeartbeatService
        from src.core.scheduling.cron_scheduler import CronScheduler
        from src.infrastructure.repositories.task_repository import TaskRepository
        from src.core.orchestration.turn_coordinator import TurnCoordinator
        from src.core.tools.remote_executor import RemoteToolExecutor
        from src.core.tools.tool_executor import LocalToolExecutor
        from src.core.tools.hybrid_executor import HybridToolExecutor
        from src.remote.telegram.telegram_response_router import TelegramResponseRouter
        from src.remote.webhooks.gmail_handler import GmailWebhookHandler
        from src.application.system_factory import SystemFactory

        # --- 2. Build Unified Engine via SystemFactory ---
        with _timing_checkpoint("config_manager_load"):
            config = ConfigManager.load_config()
        _timing_mark("config_loaded")
        logger.warning("[ BOOT ] : Constructing core system container via SystemFactory...")

        # Synchronous core engine build (tool discovery, memory engine, model router, adapter)
        with _timing_checkpoint("build_core_engine_total"):
            container = SystemFactory.build_core_engine(config, event_bus=event_bus)
        _timing_mark("core_engine_built")

        # Asynchronous infrastructure warm (MCPs/Skills/embeddings/LLM selector)
        with _timing_checkpoint("warmup_async_infrastructure_total"):
            await SystemFactory.warmup_async_infrastructure(container, config)
        _timing_mark("async_infrastructure_warmed")

        # --- 3. Set up Remote & Hybrid Tool Executors ---
        
        async def on_tool_start(name, params, action, tool_id):
            msg = ChadMessage(
                MessageType.AI_THOUGHT, 
                {
                    "state": "tool_start", 
                    "name": name, 
                    "params": params, 
                    "display_text": action,
                    "initiator": limb_coordinator.current_initiator()
                }, 
                correlation_id=tool_id
            )
            await limb_coordinator.send_to_active_limb(msg)

        async def on_turn_end(name, result, tool_id):
            success = result.get('success', True) if isinstance(result, dict) else True
            payload = {
                "state": "tool_end", 
                "name": name,
                "success": success,
                "result": result.get('result') if isinstance(result, dict) else result,
                "download_token": result.get('download_token') if isinstance(result, dict) else None,
                "filename": result.get('filename') if isinstance(result, dict) else None,
                "initiator": limb_coordinator.current_initiator()
            }
            if isinstance(result, dict):
                from src.tools.media.show_youtube_video import youtube_display_payload
                from src.tools.coding.artifact_display import artifact_display_payload
                from src.tools.maps.osm_tools import map_display_payload
                payload.update(youtube_display_payload(result))
                payload.update(artifact_display_payload(name, result))
                payload.update(map_display_payload(result))
            msg = ChadMessage(MessageType.AI_THOUGHT, payload, correlation_id=tool_id)
            await limb_coordinator.send_to_active_limb(msg)

        # Remote Side (executed on current active connection)
        remote_executor = RemoteToolExecutor(
            send_message_callback=limb_coordinator.send_to_active_limb,
            wait_for_response_callback=limb_coordinator.wait_for_tool_response,
            on_tool_start=on_tool_start,
            on_tool_end=on_turn_end
        )

        # Local Side (running within backend cloud instance)
        brain_local_executor = LocalToolExecutor(
            tool_registry=container.tool_registry,
            on_tool_start=on_tool_start,
            on_tool_end=on_turn_end
        )
        
        hybrid_executor = HybridToolExecutor(
            local_executor=brain_local_executor,
            remote_executor=remote_executor
        )

        # --- 4. Compose Main Session Manager ---
        
        async def on_thinking(text, type="thinking", append=True):
            # Route direct thinking events to limb
            initiator = limb_coordinator.current_initiator()
            msg = ChadMessage(MessageType.AI_THOUGHT, {
                "state": type, 
                "text": text, 
                "append": append, 
                "initiator": initiator or "user"
            })
            await limb_coordinator.send_to_active_limb(msg)

        mock_audio = MockAudio()

        # IMPORTANT: SessionManager accepts pre-constructed system components
        session_manager = SessionManager(
            config=config,
            model_router=container.model_router,
            tool_registry=container.tool_registry,
            memory_engine=container.memory_engine,
            audio_handler=mock_audio,
            tool_executor=hybrid_executor,
            event_bus=event_bus
        )
        
        # Ensure SessionManager linked in container for background agent injection
        container.session_manager = session_manager

        # Link legacy callbacks
        session_manager.on_thinking = on_thinking
        session_manager.on_reasoning = on_thinking # Reuse thinking bridge

        # Start manager subsystems
        await session_manager.start_modules()

        # --- 5. Start Autonomous Background Services ---
        heartbeat_service = None
        cron_scheduler = None
        task_repo = None

        # Keep configured workers available while the master switch is off;
        # their live gates pause work and allow resuming without a restart.
        cron_enabled = ConfigManager.get("daemons.cron", True)
        heartbeat_enabled = ConfigManager.get("daemons.heartbeat", True)
        gmail_enabled = ConfigManager.get("daemons.gmail_webhook", True)

        if hasattr(session_manager, 'conversation_orchestrator') and (cron_enabled or heartbeat_enabled):
            orch = session_manager.conversation_orchestrator
            
            if heartbeat_enabled:
                hb_mins = int(ConfigManager.get("daemons.heartbeat_interval_minutes", 30) or 30)
                heartbeat_service = HeartbeatService(orchestrator=orch)
                await heartbeat_service.start(interval_minutes=hb_mins)
                logger.warning(f"[ DAEMONS ] : Heartbeat service initialized ({hb_mins} min interval).")
            
            if cron_enabled:
                task_repo = TaskRepository(str(Path("data/agent_tasks.db")))
                cron_scheduler = CronScheduler(
                    orchestrator=orch,
                    task_repo=task_repo,
                    event_bus=event_bus
                )
                asyncio.create_task(cron_scheduler.start())
                logger.warning("[ DAEMONS ] : Cron scheduler initialized and active.")

        # --- 6. Final Assembly of Peripheral Handlers ---
        
        # Omnichannel Telegram support
        telegram_router = TelegramResponseRouter(event_bus)
        
        # Sequential Gmail pipeline
        gmail_handler = None
        if gmail_enabled:
            gmail_handler = GmailWebhookHandler(session_manager)
            gmail_handler.start()
            logger.warning("[ DAEMONS ] : Gmail webhook handler initialized and active.")

        from src.core.intelligence.reflection_trigger import register_reflection_triggers
        register_reflection_triggers(event_bus)

        # Wire external webhooks (Gmail/Telegram -> System calls)
        await RemoteFactory._register_webhook_handlers(
            gmail_handler, session_manager, telegram_router
        )

        # Re-aim Telegram Bot API at this Brain when a public base URL is configured
        # (telegram.public_base_url / TELEGRAM_PUBLIC_BASE_URL). Fire-and-forget so
        # boot is not blocked on Telegram's API.
        try:
            from src.remote.telegram.webhook_register import maybe_auto_register_telegram_webhook

            asyncio.create_task(maybe_auto_register_telegram_webhook())
        except Exception as e:
            logger.warning(f"[ TELEGRAM ] Auto-register task failed to schedule: {e}")
        
        # Gmail Renew Logic
        from src.core.monitoring.daemon_status import daemons_master_enabled
        if os.getenv("GMAIL_PUB_SUB_TOPIC") and cron_scheduler and daemons_master_enabled():
            # Push trigger
            asyncio.create_task(RemoteFactory.refresh_gmail_watch())
            
            # Push registration to cron to prevent expiration
            try:
                tasks = await cron_scheduler.list_tasks()
                if not any(t.name == "Refresh Gmail Watch" for t in tasks):
                    await cron_scheduler.add_task(
                        name="Refresh Gmail Watch",
                        description="Renew Gmail Pub/Sub watch automatically every 6 days.",
                        schedule="0 0 */6 * *",
                        metadata={"is_agentic": False, "system_task": True}
                    )
            except Exception as e:
                logger.error(f"Failed to setup recurring Gmail watch task: {e}")

        # Compose finalized coordinating gateway
        turn_coordinator = TurnCoordinator(
            session_manager=session_manager,
            default_adapter=container.adapter,
            consolidation_manager=container.consolidation_manager
        )

        # Construct a lightweight compatible Headless Facade & Command Handler for Application Relay
        class HeadlessAssistantWrapper:
            def __init__(self, audio, memory, config, session, model_router, tool_registry=None, event_bus=None):
                self.audio_handler = audio
                self.memory = memory
                self.config = config
                self.session_manager = session
                self.model_router = model_router
                self.tool_registry = tool_registry
                # Exposed so AssistantFacade.get_event_bus() can resolve the bus.
                # Without this, facade.interrupt() (the Stop button's HTTP command)
                # could not publish TaskStoppedByUser — the event LimbCoordinator
                # listens for to cancel the in-flight turn and abort the LM Studio
                # stream — so Stop did nothing and the model kept generating.
                self.event_bus = event_bus

        from src.application.facades.assistant_facade import AssistantFacade
        from src.application.commands import CommandHandler
        
        assistant_wrapper = HeadlessAssistantWrapper(
            audio=mock_audio,
            memory=container.memory_engine,
            config=config,
            session=session_manager,
            model_router=container.model_router,
            tool_registry=container.tool_registry,
            event_bus=event_bus,
        )

        headless_facade = AssistantFacade(
            assistant=assistant_wrapper,
            is_uplink=False,
            text_mode_client=container.text_mode_client
        )
        # Exposed for automation_commands.py (Autonomous Actions settings panel) —
        # these aren't part of the facade's normal mixin surface since they're
        # daemons, not assistant state.
        headless_facade.heartbeat_service = heartbeat_service
        headless_facade.cron_scheduler = cron_scheduler

        headless_handler = CommandHandler(headless_facade)

        # Signal total system ready
        initialization_ready_event.set()
        logger.warning("[ BOOT ] : Headless Brain components are fully constructed & online.")

        # Package results
        return {
            "session_manager": session_manager,
            "turn_coordinator": turn_coordinator,
            "command_handler": headless_handler,
            "heartbeat_service": heartbeat_service,
            "cron_scheduler": cron_scheduler,
            "task_repository": task_repo,
            "registry": container.tool_registry,
            "mcp_registry": container.mcp_registry,
            "remote_executor": remote_executor,
            "hybrid_executor": hybrid_executor,
            "telegram_router": telegram_router,
            "gmail_handler": gmail_handler
        }

    @staticmethod
    async def _register_webhook_handlers(gmail_handler, session_manager, telegram_router=None):
        """Register active handlers to global webhook router singleton."""
        from src.remote.webhooks.webhook_manager import get_webhook_manager
        from src.core.orchestration.conversation_orchestrator import TurnInitiator
        from src.remote.telegram.access import UNAUTHORIZED_REPLY, authorize_inbound
        
        webhook_manager = get_webhook_manager()

        async def handle_gmail_webhook(event):
            if gmail_handler:
                gmail_handler.handle_webhook(event.metadata)
            else:
                logger.warning("[ GMAIL ] Webhook handler not active.")

        async def handle_telegram_webhook(event):
            payload = event.payload
            if "message" not in payload:
                return

            msg = payload["message"]
            text = msg.get("text", "") or ""
            chat_id = msg.get("chat", {}).get("id")
            user = msg.get("from", {}) or {}
            user_id = user.get("id")

            if chat_id is None:
                logger.warning("[ TELEGRAM ] Webhook missing chat.id — ignoring")
                return

            if not authorize_inbound(user_id, chat_id):
                logger.warning(
                    f"[ TELEGRAM ] Rejected unauthorized sender user_id={user_id} chat_id={chat_id}"
                )
                if telegram_router is not None:
                    await telegram_router.send_unauthorized_notice(chat_id, UNAUTHORIZED_REPLY)
                return

            from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
            from src.remote.telegram.control import is_telegram_stop_command

            repo = TelegramChatRepository()

            session_key = repo.create_or_update_chat(
                chat_id=chat_id,
                user_id=user_id,
                username=user.get("username", ""),
                first_name=user.get("first_name", ""),
                last_name=user.get("last_name", ""),
            )

            # Control lane (OpenClaw /stop): must NOT go through process_request /
            # the per-session lock, or stop queues behind the turn it should kill.
            if is_telegram_stop_command(text):
                stopped = False
                if hasattr(session_manager, "conversation_orchestrator"):
                    orch = session_manager.conversation_orchestrator
                    try:
                        stopped = await orch.cancel_turn_for_session(
                            session_key, reason="user_requested"
                        )
                    except Exception as e:
                        logger.warning(f"[ TELEGRAM ] /stop cancel failed: {e}")
                if telegram_router is not None:
                    if stopped:
                        await telegram_router.notify_stopped(session_key, chat_id)
                    else:
                        await telegram_router.send_text(
                            chat_id, "Nothing running to stop."
                        )
                logger.warning(
                    f"[ TELEGRAM ] /stop for session {session_key} "
                    f"(stopped={stopped})"
                )
                return

            # Typing while the supervisor runs.
            has_photo = bool(msg.get("photo")) or (
                isinstance(msg.get("document"), dict)
                and str((msg.get("document") or {}).get("mime_type") or "").startswith("image/")
            )
            if telegram_router is not None and (text.strip() or has_photo):
                await telegram_router.send_typing(chat_id)

            if hasattr(session_manager, "conversation_orchestrator"):
                orch = session_manager.conversation_orchestrator
                from src.shared.context import RequestContext
                from src.remote.telegram.inbound import extract_inbound_content
                from src.remote.telegram.telegram_client import get_telegram_client

                content_blocks = None
                prompt_text = text
                try:
                    client = get_telegram_client()

                    def _download(file_id: str) -> bytes:
                        return client.download_file(file_id)

                    prompt_text, content_blocks, _has_media = extract_inbound_content(
                        msg, download_file_bytes=_download
                    )
                except Exception as e:
                    logger.warning(f"[ TELEGRAM ] Inbound media parse failed: {e}")
                    content_blocks = None

                # Ignore empty non-photo messages (stickers/empty edits, etc.).
                if not (prompt_text or "").strip() and not content_blocks:
                    logger.info(
                        f"[ TELEGRAM ] Ignoring empty message chat_id={chat_id} "
                        f"(no text/caption/photo)"
                    )
                    return

                ctx = RequestContext(
                    platform="telegram",
                    session_id=session_key,
                    user_id=str(user_id) if user_id else None,
                    delivery="telegram",
                )

                # Organic feedback calibration: tune category affinity weights if user replies with reaction/sentiment
                if prompt_text:
                    try:
                        from src.core.autonomy.curiosity_service import CuriosityService
                        CuriosityService().record_feedback(prompt_text)
                    except Exception as fb_err:
                        logger.debug(f"[ TELEGRAM ] Curiosity feedback recording skipped: {fb_err}")

                # Fast chat mode by default with escalate_to_agent on Bonsai 27B.
                asyncio.create_task(
                    orch.process_request(
                        prompt_text,
                        context=ctx,
                        initiator=TurnInitiator.USER,
                        category="contact",
                        content_blocks=content_blocks,
                    )
                )
        
        async def handle_github_webhook(event):
            # Skeleton hook currently logged, expand here later
            logger.info(f"[ GITHUB ] Webhook Received: {event.event_type}")

        webhook_manager.register_handler("gmail", handle_gmail_webhook)
        webhook_manager.register_handler("telegram", handle_telegram_webhook)
        webhook_manager.register_handler("github", handle_github_webhook)

    @staticmethod
    async def refresh_gmail_watch():
        """Explicit execution trigger for Google PubSub renewal."""
        topic = os.getenv("GMAIL_PUB_SUB_TOPIC")
        if not topic:
            return

        logger.info(f"[ GMAIL ] Starting watch refresh process for {topic}")
        try:
            from src.tools.workspace.manager import GoogleWorkspaceManager
            mgr = GoogleWorkspaceManager()
            
            success, err = mgr.authenticate()
            if success:
                loop = asyncio.get_event_loop()
                # Move blocking request off loop
                res = await loop.run_in_executor(None, mgr.watch_gmail, topic)
                if res:
                    logger.warning(f"[ GMAIL ] Renewed! Expiry: {res.get('expiration')}")
                    hid = res.get('historyId')
                    if hid:
                        mgr.save_last_history_id(str(hid))
        except Exception as e:
            logger.error(f"[ GMAIL ] Watch auto-renew cycle failed: {e}")
