"""
Message Commands — REST Command Relay path
==========================================

This module is the persistence and dispatch layer for the **REST Command Relay**,
one of three ways a user message can reach the Brain:

┌─────────────────────────────────────────────────────────────────────────────┐
│  PATH 1 — GUI WebSocket (primary, everyday use)                             │
│  gui_limb.send_input() → WebSocket → limb_coordinator → turn_coordinator    │
│  Persistence: gui_limb.py turn_complete handler (AFTER the Brain responds)  │
│                                                                             │
│  PATH 2 — REST Command Relay  ← THIS FILE                                  │
│  HTTP POST /api/command  → CommandHandler → SendMessageCommand.execute()    │
│           → facade.send_message() → TextModeClient → turn_coordinator       │
│  Persistence: this file, AFTER turn completes (see execute() below)         │
│  Callers: Telegram bot, programmatic API clients, test_chat_compaction.py   │
│                                                                             │
│  PATH 3 — TextModeClient direct (integration tests, Skill sub-brains)       │
│  TextModeClient.send_message() → turn_coordinator                           │
│  Persistence: NONE — ephemeral by design; LangGraph checkpointer is the     │
│               only state store for these callers.                           │
└─────────────────────────────────────────────────────────────────────────────┘

⚠️  CARDINAL RULE — applies to ALL three paths:
    Never write the user message to Supabase BEFORE calling turn_coordinator.dispatch().
    The turn_coordinator loads DB history at the START of every dispatch.  If the
    current user message is already in the DB when dispatch runs, it will be loaded
    as prior history AND injected again as the live message → duplicate in LangGraph.
    Always defer persistence until AFTER the turn completes.
"""

from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
import logging
from src.application.commands.base_command import Command
from src.shared.logger import setup_logger

logger = setup_logger("MessageCommands")


@dataclass
class SendMessageCommand(Command):
    """
    Command to send a text message to the assistant.

    This is the primary command for user text input.
    Handles routing to cloud or local mode automatically.
    """

    text: str
    image_base64: Optional[str] = None
    content_blocks: Optional[list] = None
    thinking_level: str = "medium"
    confirm_continuation: Optional[Callable] = None
    conversation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    category: Optional[str] = "autonomous"

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate message parameters"""
        has_content = (self.text and self.text.strip()) or self.image_base64 or self.content_blocks
        if not has_content:
            return (False, "Message content cannot be empty")

        if self.thinking_level not in ["minimal", "medium", "high"]:
            return (False, f"Invalid thinking level: {self.thinking_level}")

        return (True, None)

    async def execute(self, facade) -> Dict[str, Any]:
        """
        Execute message send via facade with persistence.

        Args:
            facade: AssistantFacade instance

        Returns:
            Dictionary with 'success', 'response', 'conversation_id', optional 'error'
        """
        logger.info(f"Sending message: {self.text[:50]}...")
        from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
        repo = ChatHistoryRepository()
        
        is_new_chat = not self.conversation_id
        current_conv_id = self.conversation_id

        # 1. Create conversation if needed
        if is_new_chat:
            from src.shared.config_manager import ConfigManager
            model_name = ConfigManager.get_active_model()
            conv = await repo.create_conversation(model=model_name)
            current_conv_id = conv.get("id")
            logger.info(f"Created new conversation: {current_conv_id}")

        # Capture original user content now (before dispatch alters nothing, but keeps it co-located)
        user_content = self.content_blocks if self.content_blocks else self.text

        # Check for manual compaction command
        if self.text and self.text.strip().lower() == "/compact":
            logger.info("[ COMMAND ] /compact intercepted. Executing manual compaction...")
            await repo.append_message(current_conv_id, "user", user_content)
            success = await facade.force_consolidation(current_conv_id)
            if success:
                response_text = "[System: Memory compaction successfully executed.]"
                await repo.append_message(current_conv_id, "assistant", response_text)
                return {
                    "success": True,
                    "response": response_text,
                    "conversation_id": current_conv_id
                }
            else:
                response_text = "[System: Memory compaction failed or not required.]"
                await repo.append_message(current_conv_id, "assistant", response_text)
                return {
                    "success": False,
                    "response": response_text,
                    "error": "Memory compaction failed or not required.",
                    "conversation_id": current_conv_id
                }

        try:
            # 3. Execute LLM turn
            import time
            turn_start_time = time.time()
            result = await facade.send_message(
                text=self.text,
                image_base64=self.image_base64,
                content_blocks=self.content_blocks,
                confirm_continuation=self.confirm_continuation,
                thinking_level=self.thinking_level,
                conversation_id=current_conv_id,
                metadata=self.metadata,
                category=self.category
            )

            if result.get("success"):
                logger.info("Message sent successfully")
                response_text = result.get("response", "")
                
                # Capture thoughts and turn duration for persistence
                turn_duration_ms = int((time.time() - turn_start_time) * 1000)
                thinking_events = result.get("thinking_events", [])
                turn_metrics = result.get("turn_metrics", {})
                
                metadata = {}
                if thinking_events:
                    metadata["thoughts"] = thinking_events
                metadata["turn_duration_ms"] = turn_duration_ms
                if turn_metrics:
                    metadata.update(turn_metrics)

                # 4. Persist turn — user message first (original content, no stripping),
                #    then assistant. Saving after a successful turn means the DB history
                #    never includes the current message during dispatch, which prevents the
                #    dual-load duplication that occurs when the checkpointer seeds from DB.
                await repo.append_message(current_conv_id, "user", user_content)
                if response_text:
                    await repo.append_message(current_conv_id, "assistant", response_text, metadata=metadata)
                
                # 5. Handle title generation for new chats
                if is_new_chat:
                    new_title = repo.derive_title(self.text)
                    await repo.update_conversation_title(current_conv_id, new_title)
                    logger.info(f"Generated title for {current_conv_id}: {new_title}")
            else:
                logger.warning(f"Message send failed: {result.get('error')}")

            # Always return conversation_id and the full conversation object for UI sync
            result["conversation_id"] = current_conv_id
            
            # Fetch latest conversation state (to get updated title/updated_at)
            try:
                convs = await repo.get_conversations()
                active_conv = next((c for c in convs if c["id"] == current_conv_id), None)
                if active_conv:
                    result["conversation"] = active_conv
            except Exception as e:
                logger.error(f"Failed to fetch updated conversation for result: {e}")

            return result

        except Exception as e:
            logger.error(f"Message send exception: {e}", exc_info=True)
            return {"success": False, "error": str(e), "conversation_id": current_conv_id}

    def __str__(self) -> str:
        """String representation for logging"""
        preview = self.text[:30] + "..." if len(self.text) > 30 else self.text
        has_blocks = " (with blocks)" if self.content_blocks else ""
        return f"SendMessageCommand(text='{preview}', thinking={self.thinking_level}){has_blocks}"


@dataclass
class ClassifyIntentCommand(Command):
    """
    Command to classify user intent for adaptive thinking.

    Used to determine the appropriate thinking level based on message content.
    """

    text: str

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate intent classification parameters"""
        if not self.text or not self.text.strip():
            return (False, "Text cannot be empty for intent classification")

        return (True, None)

    async def execute(self, facade) -> Dict[str, Any]:
        """
        Execute intent classification via facade.

        Args:
            facade: AssistantFacade instance

        Returns:
            Dictionary with 'intent' key
        """
        try:
            intent = await facade.classify_intent(self.text)
            return {"success": True, "intent": intent}

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            return {"success": False, "intent": "GENERAL", "error": str(e)}

    def __str__(self) -> str:
        """String representation for logging"""
        preview = self.text[:30] + "..." if len(self.text) > 30 else self.text
        return f"ClassifyIntentCommand(text='{preview}')"


@dataclass
class StopActiveTaskCommand(Command):
    """
    Command to stop/interrupt the current active task.

    Used to cancel ongoing tool execution or conversation turn.
    """

    async def execute(self, facade) -> Dict[str, Any]:
        """
        Execute task interruption.

        Args:
            facade: AssistantFacade instance

        Returns:
            Dictionary with 'success' key
        """
        success = await facade.interrupt()
        return {"success": success}

    def __str__(self) -> str:
        """String representation for logging"""
        return "StopActiveTaskCommand()"
