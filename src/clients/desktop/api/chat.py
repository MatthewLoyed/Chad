import asyncio
import time as time_module
from src.shared.logger import setup_logger
from src.application.commands.chat_history_commands import (
    GetConversationsCommand,
    GetMessagesCommand,
    DeleteConversationCommand,
    CreateConversationCommand,
    SearchChatsCommand,
    UpdateConversationTitleCommand,
    PinConversationCommand,
    SetConversationProjectCommand,
    ListWorkspaceProjectsCommand,
    CreateWorkspaceProjectCommand,
    RenameWorkspaceProjectCommand,
)

logger = setup_logger("WebViewAPI")


class ChatAPIMixin:
    """Mixin for chat-related WebView API methods."""

    def get_conversations(self, limit=50, offset=0):
        """Fetch persistent conversations with pagination."""
        from src.shared.benchmark import timer
        logger.warning(f"[ BRIDGE ] get_conversations received request")
        with timer(f"API Bridge: get_conversations"):
            t0 = time_module.time()
            res = self._execute_command(
                GetConversationsCommand(limit=int(limit), offset=int(offset)),
                timeout=15.0,
                default_fallback=[],
                raise_on_error=True
            )
            elapsed = time_module.time() - t0
            logger.warning(f"[ SUCCESS ] BRIDGE get_conversations completed in {elapsed:.4f}s with {len(res)} results.")
            
            # Capture TOTAL STARTUP TIME for user benchmarking on the VERY FIRST execution only
            if not hasattr(self, '_first_history_sync_done'):
                self._first_history_sync_done = True
                try:
                    from src.shared.benchmark import mark_critical
                    mark_critical("TOTAL GUI STARTUP COMPLETED (From process spawn to data displayed)")
                except ImportError:
                    pass
            return res

    def get_chat_messages(self, conversation_id):
        """Fetch messages for a specific conversation.

        A turn still streaming isn't persisted to the DB until it completes, so a
        cold load (e.g. after a GUI/webview reload) mid-turn would show nothing for
        it. Merge an ephemeral in-progress snapshot from the live Limb turn state so
        the user's question and the partial answer survive a reload — no DB write,
        so the fragile LangGraph history channel (which double-loads an early-
        persisted user message) is untouched.
        """
        from src.shared.benchmark import timer
        with timer(f"API Bridge: get_chat_messages"):
            messages = self._execute_command(
                GetMessagesCommand(conversation_id=conversation_id),
                timeout=15.0,
                default_fallback=[],
                raise_on_error=True
            )
            return self._merge_in_progress_snapshot(conversation_id, messages)

    def _merge_in_progress_snapshot(self, conversation_id, messages):
        """Append synthetic in-progress user/assistant messages for a turn that is
        still streaming in ``conversation_id``. Best-effort; never raises."""
        try:
            window = self.main_window_ref()
            app = getattr(window, "app_reference", None) if window else None
            limb = getattr(app, "uplink_limb", None) if app else None
            if limb is None:
                return messages
            if getattr(limb, "pending_conversation_id", None) != conversation_id:
                return messages  # no in-flight turn for this chat

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            turn_id = getattr(limb, "current_turn_id", None) or f"inprogress-{conversation_id}"
            merged = list(messages or [])
            existing_ids = {m.get("id") for m in merged if isinstance(m, dict)}

            # 1. The user message that triggered the turn (not yet persisted).
            pending_user = getattr(limb, "_pending_user_content", None)
            pending_user_text = getattr(limb, "_pending_user_text", None)
            user_snap_id = f"inprogress-user-{turn_id}"
            if pending_user is not None and user_snap_id not in existing_ids:
                merged.append({
                    "id": user_snap_id,
                    "role": "user",
                    "content": pending_user if isinstance(pending_user, list) else (pending_user_text or str(pending_user)),
                    "created_at": now_iso,
                    "metadata": {"in_progress": True},
                })

            # 2. The partial assistant answer + thoughts streamed so far.
            speech = getattr(limb, "_speech_buffer", "") or ""
            thoughts = getattr(limb, "_turn_thinking_events", None) or []
            if (speech or thoughts) and turn_id not in existing_ids:
                merged.append({
                    "id": turn_id,
                    "role": "assistant",
                    "content": speech,
                    "created_at": now_iso,
                    "metadata": {"in_progress": True, "thoughts": list(thoughts)},
                })
            return merged
        except Exception as e:
            logger.warning(f"[ BRIDGE ] in-progress snapshot merge skipped: {e}")
            return messages

    def delete_chat(self, conversation_id):
        """Delete a conversation."""
        return self._execute_command(
            DeleteConversationCommand(conversation_id=conversation_id),
            timeout=15.0,
            default_fallback=False,
            raise_on_error=True
        )

    def rename_chat(self, conversation_id, title):
        """Rename a conversation."""
        return self._execute_command(
            UpdateConversationTitleCommand(conversation_id=conversation_id, title=title),
            timeout=15.0,
            default_fallback=False,
            raise_on_error=True
        )

    def pin_chat(self, conversation_id, is_pinned=True):
        """Pin or unpin a conversation in the sidebar."""
        return self._execute_command(
            PinConversationCommand(conversation_id=conversation_id, is_pinned=bool(is_pinned)),
            timeout=15.0,
            default_fallback=False,
            raise_on_error=True
        )

    def move_chat_to_project(self, conversation_id, project_path=None):
        """Associate a conversation with a workspace project path (None clears)."""
        path = project_path if project_path else None
        return self._execute_command(
            SetConversationProjectCommand(conversation_id=conversation_id, project_path=path),
            timeout=15.0,
            default_fallback=False,
            raise_on_error=True
        )

    def get_workspace_projects(self):
        """List workspace projects available for the move-to-project menu."""
        return self._execute_command(
            ListWorkspaceProjectsCommand(),
            timeout=15.0,
            default_fallback=[],
            raise_on_error=True
        )

    def create_workspace_project(self, name):
        """Create (or reuse) a workspace project folder for the Projects nav."""
        return self._execute_command(
            CreateWorkspaceProjectCommand(name=name),
            timeout=15.0,
            default_fallback=None,
            raise_on_error=True
        )

    def rename_workspace_project(self, old_path, new_name):
        """Rename a workspace project folder and retarget any chats pointed at it."""
        return self._execute_command(
            RenameWorkspaceProjectCommand(old_path=old_path, new_name=new_name),
            timeout=15.0,
            default_fallback=None,
            raise_on_error=True
        )

    def create_new_chat(self, model=None, title="New Chat", domain=None):
        """Explicitly create a new conversation."""
        return self._execute_command(
            CreateConversationCommand(model=model, title=title, domain=domain),
            timeout=15.0,
            default_fallback=None,
            raise_on_error=True
        )

    def search_chats(self, query):
        """Search for messages or conversations."""
        return self._execute_command(
            SearchChatsCommand(query=query),
            timeout=15.0,
            default_fallback={"messages": [], "conversations": []},
            raise_on_error=True
        )

    def send_text_command(self, text, image_base64=None, content_blocks=None, conversation_id=None, metadata=None, category=None, thinking_enabled=None, domain=None, retry=None, keep_count=None):
        """
        Send text command to assistant.
        Routes directly over WebSockets using the Distributed Limb pipeline.
        """
        window = self.main_window_ref()
        has_content = (text and text.strip()) or image_base64 or content_blocks
        if not window or not has_content:
            return False

        try:
            app = window.app_reference
            if not app:
                return False

            # BOOT GUARD: Queue message if system not initialized
            if not app.is_initialized:
                logger.info("[SYSTEM]: Queuing message for post-boot processing...")
                if not hasattr(app, '_pending_messages'):
                    app._pending_messages = []
                app._pending_messages.append({
                    "text": text,
                    "image_base64": image_base64,
                    "content_blocks": content_blocks,
                    "conversation_id": conversation_id,
                    "metadata": metadata,
                    "category": category,
                    "thinking_enabled": thinking_enabled,
                    "domain": domain,
                    "retry": retry,
                    "keep_count": keep_count,
                })
                return True

            # Dispatch strictly through established WebSocket Uplink Pipeline
            if hasattr(app, 'uplink_limb') and app.uplink_limb:
                async def _dispatch():
                    try:
                        await app.uplink_limb.send_input(
                            text=text,
                            image_base64=image_base64,
                            content_blocks=content_blocks,
                            conversation_id=conversation_id,
                            category=category,
                            thinking_enabled=thinking_enabled,
                            domain=domain,
                            retry=retry,
                            keep_count=keep_count,
                            metadata=metadata,
                        )
                    except Exception as send_err:
                        logger.error(f"[ BRIDGE ] : Failed to transmit input across uplink: {send_err}")
                
                if app.loop and not app.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(_dispatch(), app.loop)
                    return True
            else:
                logger.error("[ BRIDGE ] : Attempted to send message but uplink_limb is unavailable.")
                
            return False

        except Exception as e:
            logger.error(f"[ BRIDGE ] : Error dispatching text command: {e}", exc_info=True)
            return False

    def set_ui_turn_mode(self, ui_mode):
        """Sync chat-input mode dropdown to the Brain (model preload + preferences)."""
        window = self.main_window_ref()
        if not window:
            return False
        try:
            app = window.app_reference
            if not app or not hasattr(app, 'uplink_limb') or not app.uplink_limb:
                return False

            async def _dispatch():
                try:
                    await app.uplink_limb.send_ui_turn_mode(str(ui_mode or "auto"))
                except Exception as send_err:
                    logger.error(f"[ BRIDGE ] : Failed to transmit UI turn mode: {send_err}")

            if app.loop and not app.loop.is_closed():
                asyncio.run_coroutine_threadsafe(_dispatch(), app.loop)
                return True
            return False
        except Exception as e:
            logger.error(f"[ BRIDGE ] : Error dispatching UI turn mode: {e}", exc_info=True)
            return False

    def invoke_fast_track(self, alias):
        """
        Invoke an allowlisted '/<alias>' fast-track command. Skips the LLM/planner
        entirely on the Brain side — see LimbMessageHandler._handle_fast_track_invoke.
        """
        window = self.main_window_ref()
        if not window or not alias:
            return False

        try:
            app = window.app_reference
            if not app or not app.is_initialized:
                return False

            if hasattr(app, 'uplink_limb') and app.uplink_limb:
                async def _dispatch():
                    try:
                        await app.uplink_limb.send_fast_track_invoke(alias)
                    except Exception as send_err:
                        logger.error(f"[ BRIDGE ] : Failed to transmit fast-track invoke: {send_err}")

                if app.loop and not app.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(_dispatch(), app.loop)
                    return True

            return False
        except Exception as e:
            logger.error(f"[ BRIDGE ] : Error dispatching fast-track invoke: {e}", exc_info=True)
            return False
