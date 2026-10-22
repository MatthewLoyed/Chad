from typing import Optional, List, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class SessionFacadeMixin:
    """Mixin for session and execution-related facade methods."""

    def is_session_active(self) -> bool:
        """
        Check if a conversation session is active.

        Returns:
            True if session is active, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'session_manager'):
            return False

        session_manager = self._assistant.session_manager
        if not session_manager:
            return False

        try:
            return session_manager.is_listening
        except Exception as e:
            logger.error(f"Failed to check session status: {e}")
            return False

    def get_session_state(self) -> str:
        """
        Get current session state.

        Returns:
            State string ('idle', 'listening', 'thinking', 'speaking', etc.)
        """
        if not self._assistant or not hasattr(self._assistant, 'session_manager'):
            return 'idle'

        session_manager = self._assistant.session_manager
        if not session_manager or not hasattr(session_manager, 'current_state'):
            return 'idle'

        try:
            return session_manager.current_state
        except Exception as e:
            logger.error(f"Failed to get session state: {e}")
            return 'idle'

    async def send_message(
        self,
        text: str,
        image_base64: Optional[str] = None,
        content_blocks: Optional[List[Dict[str, Any]]] = None,
        confirm_continuation=None,
        thinking_level: str = "medium",
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a text message to the assistant (cloud or local mode).

        Routing priority:
        1. If a local model is active → LocalTextModeClient
        2. If cloud mode + limb connected → cloud Brain via WebSocket
        3. Otherwise → local Gemini TextModeClient

        Args:
            text: User message text
            image_base64: Optional base64-encoded image
            content_blocks: Optional list of multimodal content blocks
            confirm_continuation: Optional callback for tool limit confirmation
            thinking_level: Thinking level ('minimal', 'medium', 'high')
            conversation_id: Optional UUID for persistent history sync

        Returns:
            Dictionary with 'success', 'response', optional 'error'
        """
        if not self._assistant:
            return {"success": False, "error": "Assistant not initialized"}

        # MESSAGE ROUTING (UNIFIED CLIENT)
        from src.shared.config_manager import ConfigManager
        
        # 1. CLOUD MODE: Route to Cloud Brain via WebSocket Limb
        # This takes precedence because "Cloud mode" is an infrastructure-level setting.
        if self._is_uplink and self._uplink:
            try:
                logger.info(f"Routing multimodal message to cloud: {text[:50] if text else 'None'}...")
                # Pass all multimodal data through the limb to the Brain
                sent = await self._uplink.send_input(
                    text, 
                    image_base64=image_base64,
                    content_blocks=content_blocks,
                    conversation_id=conversation_id,
                    category=category
                )
                if sent:
                    return {"success": True, "response": ""}  # Response comes via WebSocket
                else:
                    return {"success": False, "error": "Not connected to Brain. Check your local connection."}
            except Exception as e:
                logger.error(f"Cloud message send failed: {e}")
                return {"success": False, "error": str(e)}

        # 2. LOCAL MODEL OVERRIDE: If local model is active, handle locally via TextModeClient
        # This fulfills the priority: Local compute ALWAYS wins if enabled by the user in LOCAL mode.
        elif ConfigManager.is_local_model_active() and self._text_mode_client:
            # Drop through to Step 2 (Local Adapter Mode)
            pass

        # 2. LOCAL ADAPTER MODE (Gemini or Local AI)
        text_client = self._text_mode_client
        if text_client:
            try:
                # Reset interrupt flag
                if hasattr(self._assistant, 'session_manager') and self._assistant.session_manager:
                    self._assistant.session_manager.reset_interrupt()

                # LOGGING for Multimodal Debug
                if content_blocks:
                    total_chars = sum(len(b.get('text', '')) for b in content_blocks if b.get('type') == 'text')
                    media_count = sum(1 for b in content_blocks if b.get('type') in ['image', 'document'])
                    debug_msg = f"[ FACADE ] Received {len(content_blocks)} blocks ({total_chars} text chars, {media_count} media)"
                    logger.info(debug_msg)
                    print(debug_msg) # FORCE TERMINAL
                else:
                    debug_msg = f"[ FACADE ] Received standard text message: {len(text) if text else 0} chars"
                    logger.info(debug_msg)
                    print(debug_msg) # FORCE TERMINAL

                # Send message
                result = await text_client.send_message(
                    text,
                    image_base64=image_base64,
                    content_blocks=content_blocks,
                    confirm_continuation=confirm_continuation,
                    thinking_level=thinking_level,
                    conversation_id=conversation_id,
                    metadata=metadata,
                    category=category
                )

                return result
            except Exception as e:
                logger.error(f"Text mode send failed: {e}", exc_info=True)
                return {"success": False, "error": str(e)}

        # Fallback: No valid mode
        return {
            "success": False,
            "error": "No valid message handler (text_mode_client or limb missing)"
        }

    async def interrupt(self) -> bool:
        """
        Interrupt the active task (text or voice).

        Returns:
            True if successful
        """
        if not self._assistant:
            return False

        logger.warning("[ FACADE ] Interrupting active tasks...")

        # 1. Stop Text Client
        if self._text_mode_client:
            self._text_mode_client.stop()

        # 2. Stop Conversation / Session
        if hasattr(self._assistant, 'session_manager') and self._assistant.session_manager:
            await self._assistant.session_manager.stop_active_turn()
            
        # 3. Publish stop event to EventBus
        bus = self.get_event_bus()
        if bus:
            from src.application.events import TaskStoppedByUser
            bus.publish(TaskStoppedByUser())
            logger.info("[ FACADE ] Published TaskStoppedByUser event.")
            return True
            
        return False

    def get_event_bus(self):
        """
        Get the EventBus instance for subscribing to events.

        Returns:
            EventBus instance or None
        """
        if not self._assistant or not hasattr(self._assistant, 'event_bus'):
            return None

        return self._assistant.event_bus

    async def classify_intent(self, text: str) -> str:
        """
        Classify user intent for adaptive thinking level.

        Args:
            text: User message text

        Returns:
            Intent bucket string ('SYSTEM', 'ACADEMIC', 'OFFICE', etc.)
        """
        if not self._assistant:
            return "GENERAL"

        # Try to get router_agent from assistant or session manager
        router_agent = getattr(self._assistant, 'router_agent', None)
        if not router_agent and hasattr(self._assistant, 'session_manager'):
            router_agent = getattr(self._assistant.session_manager, 'router_agent', None)

        if not router_agent:
            return "GENERAL"

        try:
            # Classification returns an Enum or string
            intent = await router_agent.classify_intent(text)
            if hasattr(intent, 'value'):
                return intent.value
            return str(intent)
        except Exception as e:
            logger.warning(f"Intent classification failed: {e}")
            return "GENERAL"

    async def force_consolidation(self, conversation_id: str = "default") -> bool:
        """
        Manually trigger conversation memory consolidation.
        
        Args:
            conversation_id: Session ID to consolidate
            
        Returns:
            bool: True if compaction succeeded, False otherwise
        """
        if self._is_uplink and self._uplink:
            logger.warning("[ FACADE ] force_consolidation is not supported in remote Limb mode.")
            return False
            
        if self._text_mode_client:
            return await self._text_mode_client.perform_manual_consolidation(conversation_id)
            
        return False
