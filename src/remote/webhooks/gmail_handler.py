import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Optional
from src.shared.logger import setup_logger
from src.shared.context import RequestContext
from src.tools.workspace.manager import GoogleWorkspaceManager
from src.infrastructure.repositories.telegram_chat_repository import TelegramChatRepository
logger = setup_logger("GmailWebhookHandler")

class GmailWebhookHandler:
    """
    Handles Gmail Pub/Sub webhooks sequentially to avoid race conditions.
    Isolates Gmail-specific orchestration logic from the main server.
    """

    def __init__(self, session_manager):
        """
        Initialize the handler.
        
        Args:
            session_manager: The active session manager to access orchestrator
        """
        self._session_manager = session_manager
        self._queue = asyncio.Queue()
        self._worker_task = None
        self._workspace_manager = GoogleWorkspaceManager()
        self._chat_repo = TelegramChatRepository()

    def start(self):
        """Start the background worker."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("[ GMAIL ] Handler worker started.")

    async def stop(self):
        """Stop the background worker."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            logger.info("[ GMAIL ] Handler worker stopped.")

    def handle_webhook(self, event_metadata: Dict[str, Any]):
        """
        Public entry point for new Gmail webhooks.
        Pushes to the sequential queue and returns immediately.
        """
        from src.core.monitoring.daemon_status import daemon_enabled
        if not daemon_enabled("gmail_webhook"):
            return
        history_id = event_metadata.get("historyId")
        email_address = event_metadata.get("emailAddress")

        if not history_id:
            logger.warning("[ GMAIL ] Webhook missing historyId. Skipping.")
            return

        self._queue.put_nowait({
            "history_id": history_id,
            "email_address": email_address,
            "timestamp": datetime.now()
        })
        logger.debug(f"[ GMAIL ] Queued notification {history_id}. Queue size: {self._queue.qsize()}")

    @staticmethod
    def _is_actionable_email(email: Dict[str, Any]) -> bool:
        """Return True if an email looks like personal/urgent correspondence rather than automated clutter."""
        labels = set(email.get("labels") or [])
        # Definite clutter labels in Gmail
        if labels & {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}:
            return False

        from_email = (email.get("from_email") or "").lower()
        subject = (email.get("subject") or "").lower()

        # Automated sender indicators
        automated_senders = (
            "no-reply@", "noreply@", "donotreply@", "notifications@", "notification@",
            "mailer-daemon@", "updates@", "support@", "billing@", "order@", "marketing@",
            "newsletter@", "news@", "info@", "alert@", "service@", "system@"
        )
        if any(snd in from_email for snd in automated_senders):
            return False

        # Automated subject indicators
        automated_subjects = (
            "order confirmation", "your order", "shipped", "tracking update",
            "receipt for", "invoice", "statement available", "password reset",
            "security alert", "sign-in attempt", "subscription confirmed",
            "weekly digest", "newsletter", "special offer", "refill status", "refill delay"
        )
        if any(sub in subject for sub in automated_subjects):
            return False

        return True

    async def _worker_loop(self):
        """The core sequential worker loop."""
        while True:
            try:
                data = await self._queue.get()
                from src.core.monitoring.daemon_status import daemon_enabled
                if not daemon_enabled("gmail_webhook"):
                    self._queue.task_done()
                    continue
                history_id = str(data["history_id"])
                
                # 1. Idempotency Check
                # Protect against duplicate notifications (Gmail often sends several for one event)
                last_id = self._workspace_manager.get_last_history_id()
                if last_id and int(history_id) <= int(last_id):
                    logger.debug(f"[ GMAIL ] Skipping redundant notification {history_id} (database already at {last_id})")
                    self._queue.task_done()
                    continue

                logger.info(f"[ GMAIL ] Worker processing event {history_id} (last seen in DB: {last_id})")
                
                # 2. Preparation
                if not self._session_manager or not hasattr(self._session_manager, 'conversation_orchestrator'):
                    logger.warning("[ GMAIL ] Orchestrator not ready. Retrying in 5s.")
                    await asyncio.sleep(5)
                    # Re-queue the item
                    self._queue.put_nowait(data)
                    self._queue.task_done()
                    continue

                orchestrator = self._session_manager.conversation_orchestrator
                
                # 3. Context & Routing
                # We target the default Telegram chat so Chad's response is routed back correctly.
                chat_id = self._chat_repo.get_default_chat_id()
                if chat_id:
                    session_key = self._chat_repo.get_session_key(chat_id)
                    ctx = RequestContext(
                        platform="gmail", 
                        session_id=session_key
                    )
                else:
                    ctx = RequestContext(
                        platform="gmail", 
                        session_id="autonomous:gmail"
                    )

                # 4. Deterministic Asymmetric Pre-Filter (2026 Agentic Architecture)
                # Inspect the history payload directly before waking the LLM when live workspace manager is present.
                actionable_emails = []
                if hasattr(self._workspace_manager, "list_history") and hasattr(self._workspace_manager, "get_gmail_service"):
                    from src.tools.workspace.history_tool import GetGmailHistoryTool
                    history_tool = GetGmailHistoryTool(workspace_manager=self._workspace_manager)
                    res = await history_tool.execute(history_id=history_id)

                    if res.get("success"):
                        emails = res.get("emails", []) if isinstance(res, dict) else []
                        if not emails:
                            logger.info(f"[ GMAIL ] Pre-filter: 0 new messages in history {history_id}. Suppressing LLM turn.")
                            if hasattr(self._workspace_manager, "save_last_history_id"):
                                await asyncio.to_thread(self._workspace_manager.save_last_history_id, history_id)
                            self._queue.task_done()
                            continue

                        actionable_emails = [em for em in emails if self._is_actionable_email(em)]
                        if not actionable_emails:
                            logger.info(
                                f"[ GMAIL ] Pre-filter: Filtered {len(emails)} non-actionable email(s) "
                                f"(promos/spam/automated notifications). Suppressing LLM turn."
                            )
                            if hasattr(self._workspace_manager, "save_last_history_id"):
                                await asyncio.to_thread(self._workspace_manager.save_last_history_id, history_id)
                            self._queue.task_done()
                            continue

                # 5. Format Actionable Details & Load Prompt from PromptRegistry
                if actionable_emails:
                    email_lines = []
                    for em in actionable_emails:
                        email_lines.append(
                            f"From: {em.get('from', 'Unknown')}\n"
                            f"Subject: {em.get('subject', 'No Subject')}\n"
                            f"Snippet: {em.get('snippet', '')}"
                        )
                    email_details = "\n\n".join(email_lines)
                else:
                    email_details = f"Inspect recent inbox updates for history id {history_id}."

                try:
                    from src.infrastructure.repositories.prompt_registry import PromptRegistry
                    template = PromptRegistry.get_instance().get_prompt("event_gmail_sync")
                    user_input = template.replace("{history_id}", history_id).replace("{email_details}", email_details)
                except Exception as e:
                    logger.debug(f"[ GMAIL ] PromptRegistry lookup failed for event_gmail_sync: {e}")
                    user_input = (
                        f"SYSTEM NOTIFICATION: A new Gmail sync event occurred for history id {history_id}.\n\n"
                        f"## Email Details:\n{email_details}\n\n"
                        "If this email is from a real human and requires Matthew's attention, summarize it casually in 1–2 short lines.\n"
                        "If it does not require his attention, call `stay_silent`."
                    )

                # 6. Execution (Awaited to ensure sequential processing)
                from src.core.orchestration.conversation_orchestrator import TurnInitiator
                await orchestrator.process_request(
                    prompt=user_input,
                    context=ctx,
                    initiator=TurnInitiator.WEBHOOK,
                    category='autonomous'
                )
                
                self._queue.task_done()
                logger.debug(f"[ GMAIL ] Worker successfully completed event {history_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ GMAIL ] Worker error during event processing: {e}", exc_info=True)
                # Ensure we don't stall the queue on error
                try:
                    self._queue.task_done()
                except ValueError:
                    pass
                await asyncio.sleep(2) # Backoff
