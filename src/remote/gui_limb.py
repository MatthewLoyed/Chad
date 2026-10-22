"""
GUI Chad Limb — WebSocket path (PATH 1, primary)
=================================================

This is the persistence and event-relay layer for the GUI → Brain WebSocket path,
one of three ways a user message can reach the Brain.  See message_commands.py for
the full three-path architecture diagram.

Key invariant enforced here:
    send_input()  — stages the user message in self._pending_user_content, then
                    fires the message over WebSocket.  Does NOT write to Supabase yet.
    turn_complete — persists user message + assistant response AFTER the Brain replies,
                    so turn_coordinator.dispatch() never sees the live message in DB
                    history during the same turn (prevents LangGraph duplication).
"""

import asyncio
import json
import logging
import time
from typing import Optional, Any, Callable, List

from src.remote.limb import ChadLimb
from src.remote.protocol import ChadMessage, MessageType
from src.shared.logger import setup_logger
from src.core.tools.tool_timeouts import get_tool_timeout
from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

logger = setup_logger("GUIChadLimb")

# High-frequency token deltas (the answer + reasoning streams). These are the only
# events that arrive at model token-rate (~40/s); everything else (run lifecycle,
# tools, plan, speech finalize, usage) is structural and low-frequency.
_COALESCED_EVENT_TYPES = frozenset({"text_delta", "reasoning_delta"})
# Flush window for batching token deltas before the evaluate_js IPC hop. ~35ms ≈
# 28 batches/s, well inside the 30–100ms band the streaming-UI literature
# recommends and Microsoft's "batch messages" WebView2 IPC guidance.
_STREAM_FLUSH_INTERVAL_S = 0.035

class GUIChadLimb(ChadLimb):
    """
    A specialized Chad Limb that integrates with the pywebview GUI.
    """
    def __init__(self, main_window, server_url: Optional[str] = None, tool_registry: Optional[Any] = None):
        super().__init__(server_url, tool_registry=tool_registry)
        self.window = main_window
        self.status_callback = self._on_connection_status_change
        self.history_repo = ChatHistoryRepository()
        self.pending_conversation_id: Optional[str] = None
        # True only while a turn THIS Limb started is in flight. The Brain fans every
        # turn out to every connected viewer (#709), so the desktop now also receives
        # turns driven from the phone — and it must not persist those. The Brain
        # already writes them (issue #189), and `pending_conversation_id` is the
        # desktop's *currently open* chat, not the phone turn's, so persisting here
        # duplicated the reply into an unrelated conversation (issue #711).
        self._turn_is_mine: bool = False
        self.current_turn_id: Optional[str] = None
        self._turn_thinking_events = []
        self._speech_buffer = ""
        self._last_event_type = None
        # Voice-turn reply correlation: turn_id -> Future[str]. Lets voice mode
        # await the Brain's reply for a specific turn (so it can speak it).
        self._voice_turn_futures: dict[str, "asyncio.Future[str]"] = {}
        # Voice-turn streaming (epic #291 Phase 2): turn_id -> (SentenceAccumulator,
        # on_sentence callback). Populated only when the caller wants sentences
        # spoken as they stream in, rather than waiting for the final reply.
        self._voice_turn_streams: "dict[str, tuple[Any, Callable[[str], Any]]]" = {}
        # Debounce for spoken status narration (epic #293 Phase 3): turn_id -> monotonic
        # time last spoken. A status label (e.g. "Selecting tools…") is only voiced if
        # nothing else has been said for this turn recently, so it doesn't talk over the
        # model's own narration or a previous status.
        self._voice_status_last_spoken: "dict[str, float]" = {}
        self._VOICE_STATUS_DEBOUNCE_S = 1.5
        # Typed stream emitter (streaming refactor). The Brain→Limb→React render now
        # runs entirely on this typed path: it emits the StreamEvent union
        # (src/remote/stream_events.py) to window.applyStreamEvent, the React reducer
        # (streamReducer) folds it into streamTurn, and ChatStream renders from it.
        # See docs/features/STREAMING_REFACTOR.md.
        self._stream_emitter = None  # type: Optional[Any]
        # Coalescing buffer for the typed stream: token deltas are batched into
        # ~35ms windows before crossing the evaluate_js IPC boundary (see
        # _push_stream_events). Without this the UI re-renders + pays an IPC hop
        # per token and falls progressively behind a fast local model.
        self._stream_buffer: List[dict] = []
        self._stream_flush_handle = None  # type: Optional[asyncio.TimerHandle]

    def _se(self, fn: Callable[[Any], List[dict]]) -> None:
        """Feed the typed stream emitter and push the result, exception-safely.

        ``fn`` receives the active StreamEventEmitter and returns its event list.
        A no-op when no turn is active; any failure here is swallowed so a transient
        emit error can never abort turn handling.
        """
        if self._stream_emitter is None:
            return
        try:
            events = fn(self._stream_emitter)
        except Exception as e:
            logger.debug(f"[ LIMB ] stream-event emit skipped: {e}")
            return
        if events:
            self._push_stream_events(events)

    def _push_stream_events(self, events: List[dict]) -> None:
        """Buffer stream events and batch token deltas across the IPC boundary.

        WebView2's perf guidance is to batch host→web messages rather than fire one
        evaluate_js call per token; React likewise should not re-render per token. So
        runs of high-frequency token deltas (text/reasoning) coalesce into a single
        ~35ms batch, while structural events (run lifecycle, tools, plan, speech
        finalize, usage) flush immediately to keep latency-sensitive UI transitions
        snappy. Events carry a monotonic ``seq``, so a batch preserves order; flushing
        the whole buffer on any structural event keeps deltas ahead of it ordered too.
        """
        self._stream_buffer.extend(events)
        if all(e.get("type") in _COALESCED_EVENT_TYPES for e in events):
            self._schedule_stream_flush()
        else:
            self._flush_stream_buffer()

    def _schedule_stream_flush(self) -> None:
        """Arm a one-shot flush timer if one isn't already pending."""
        if self._stream_flush_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (not expected on the WS path) — flush synchronously.
            self._flush_stream_buffer()
            return
        self._stream_flush_handle = loop.call_later(
            _STREAM_FLUSH_INTERVAL_S, self._flush_stream_buffer
        )

    def _flush_stream_buffer(self) -> None:
        """Drain the buffered stream events to the webview in one IPC call."""
        if self._stream_flush_handle is not None:
            self._stream_flush_handle.cancel()
            self._stream_flush_handle = None
        if not self._stream_buffer:
            return
        batch = self._stream_buffer
        self._stream_buffer = []
        try:
            self.window.push_stream_event(batch)
        except Exception as e:
            logger.debug(f"[ LIMB ] stream-event flush skipped: {e}")

    def _is_voice_delivery(self, msg: ChadMessage) -> bool:
        """True when this wire message belongs to the voice lane, not the GUI chat."""
        payload = msg.payload or {}
        delivery = payload.get("delivery")
        if delivery == "voice":
            return True
        if payload.get("category") == "voice":
            return True
        turn_id = payload.get("turn_id")
        if turn_id and turn_id in self._voice_turn_futures:
            return True
        return False

    def _is_telegram_delivery(self, msg: ChadMessage) -> bool:
        """True when this wire message belongs to Telegram, not the desktop GUI chat."""
        payload = msg.payload or {}
        if payload.get("delivery") == "telegram":
            return True
        if payload.get("category") in ("contact", "telegram_agent"):
            return True
        session_id = str(payload.get("session_id") or payload.get("conversation_id") or "")
        if ":telegram:" in session_id:
            return True
        return False
        
    # Number of consecutive failed reconnect attempts tolerated before the UI
    # escalates from a neutral "reconnecting" state to an alarmed "error" state.
    # INITIAL_RECONNECT_DELAY doubles each failure (1s, 2s, 4s, 8s, ...), so a
    # threshold of 4s means the first two failures stay calm and only the third+
    # turns red — most transient blips (server still booting, brief network hiccup)
    # never reach the scary state at all.
    _RECONNECT_ESCALATION_THRESHOLD_S = 4.0

    def _on_connection_status_change(self, status: str, metadata: dict):
        """Update GUI based on connection status."""
        if status == "connecting":
            self.window.update_status(state="initializing", text="Connecting to Chad...")
            self.window.push_log(f"Connecting to Brain at {metadata.get('url')}...", "INFO")
        elif status == "authenticating":
            self.window.update_status(state="initializing", text="Connecting to Chad...")
        elif status == "reconnecting":
            error = metadata.get('error', 'Connection lost')
            retry_in = metadata.get('next_retry', 0)
            if retry_in <= self._RECONNECT_ESCALATION_THRESHOLD_S:
                self.window.update_status(state="reconnecting", text="Reconnecting to Chad...")
            else:
                self.window.update_status(state="error", text="Can't reach Chad — still retrying")
            self.window.push_log(f"Brain connection failed: {error}. Retrying in {int(retry_in)}s...", "WARNING")
        elif status == "error":
            error = metadata.get('error', 'Unknown error')
            self.window.update_status(state="error", text="Something went wrong with the connection")
            self.window.push_log(f"Internal Limb Error: {error}", "ERROR")

    async def handle_message(self, msg: ChadMessage):
        """Handle messages from the Brain and update the GUI."""
        if self._is_telegram_delivery(msg):
            return
        
        if msg.type == MessageType.AUTH.value:
            if msg.payload.get("status") == "success":
                logger.debug("Authenticated with Brain.")
                self.window.update_status(state="ready", text="Connected")
                self.window.push_log("Connected to Chad.", "INFO")
                fast_track_commands = msg.payload.get("fast_track_commands", [])
                if hasattr(self.window, "push_fast_track_commands"):
                    self.window.push_fast_track_commands(fast_track_commands)
                planner_enabled = bool(msg.payload.get("planner_enabled", False))
                if hasattr(self.window, "push_planner_enabled"):
                    self.window.push_planner_enabled(planner_enabled)
            else:
                error_msg = msg.payload.get("error", "Unknown auth error")
                logger.error(f"Auth failed: {error_msg}")
                self.window.update_status(state="error", text="Couldn't sign in to Chad")
                self.window.push_log(f"Brain Auth Failed: {error_msg}", "ERROR")

        elif msg.type in [MessageType.ERROR.value, MessageType.SYSTEM_ERROR.value]:
            error_msg = msg.payload.get("error", "Unknown server error")
            logger.error(f"Brain Error: {error_msg}")
            
            status_text = "Brain Error"
            if "LM Studio is not running" in error_msg or "Cannot reach LM Studio" in error_msg:
                status_text = "LM Studio Offline"
                
            self.window.update_status(state="error", text=status_text)
            self.window.push_log(f"Brain Error: {error_msg}", "ERROR")
            
            target = getattr(self.window, 'webview_window', self.window)
            if hasattr(target, 'evaluate_js'):
                display_msg = error_msg
                if not display_msg.startswith("⚠️"):
                    display_msg = f"⚠️ {display_msg}"
                safe_text = json.dumps(display_msg)
                target.evaluate_js(f"window.addAIMessageToChat({safe_text})")

            # Mirror the failure onto the typed stream (parallel path), then end the turn.
            self._se(lambda em, m=error_msg: em.run_error(m))
            self._stream_emitter = None

            # Unblock any voice-turn waiter so voice mode re-arms instead of hanging.
            self._fail_pending_voice_turns()

        elif msg.type == MessageType.TOOL_REQUEST.value:
            tool_name = msg.payload.get("tool")
            params = msg.payload.get("params", {})
            timeout = get_tool_timeout(tool_name)
            try:
                result = await asyncio.wait_for(
                    self.registry.execute_tool(tool_name, params),
                    timeout=timeout
                )
                response = ChadMessage(MessageType.TOOL_RESPONSE, result, correlation_id=msg.id)
                if self.websocket:
                    await self.websocket.send(response.to_json())
            except asyncio.TimeoutError:
                logger.error(f"Tool '{tool_name}' execution timed out after {timeout}s")
                error_resp = ChadMessage(
                    MessageType.TOOL_RESPONSE,
                    {"success": False, "error": f"Tool execution timed out after {timeout}s"},
                    correlation_id=msg.id
                )
                if self.websocket:
                    await self.websocket.send(error_resp.to_json())
            except Exception as e:
                logger.error(f"Error executing local tool: {e}")
                error_resp = ChadMessage(MessageType.TOOL_RESPONSE, {"success": False, "error": str(e)}, correlation_id=msg.id)
                if self.websocket:
                    await self.websocket.send(error_resp.to_json())

        elif msg.type == MessageType.AI_THOUGHT.value:
            if self._is_voice_delivery(msg):
                if msg.payload.get("state") == "status":
                    turn_id = msg.payload.get("turn_id")
                    label = msg.payload.get("text") or ""
                    await self._maybe_speak_voice_status(turn_id, label)
                return
            import time
            # INTERLEAVING LOGIC: If we were just speaking, flush the speech buffer to thinking stream
            if self._last_event_type == 'speech' and self._speech_buffer:
                speech_text = self._speech_buffer
                ts = int(time.time() * 1000)
                self.window.push_thinking_event({'type': 'speech', 'text': speech_text, 'append': False, 'timestamp': ts})
                self._turn_thinking_events.append({'type': 'speech', 'text': speech_text, 'append': False, 'timestamp': ts})
                self._se(lambda em, t=speech_text: em.speech_finalize(t))
                self._speech_buffer = ""

            self._last_event_type = 'thinking'

            state = msg.payload.get("state")
            text = msg.payload.get("text")
            initiator = msg.payload.get("initiator", "user") or "user"
            is_autonomous = initiator in ["system", "cron", "heartbeat"]
            
            if state == "status":
                if not is_autonomous:
                    self.window.update_status(state="thinking", text=text)
                target = getattr(self.window, 'webview_window', self.window)
                if hasattr(target, 'evaluate_js'):
                    safe_text = json.dumps(text)
                    target.evaluate_js(f"window.handleLiveStatus?.({safe_text})")
                return

            if state == 'browser_progress':
                try:
                    progress = json.loads(text)
                    self._se(lambda em, p=progress: em.browser_progress(p))
                except (ValueError, TypeError):
                    pass
                return

            if state == "tools_menu":
                if is_autonomous:
                    return
                tools = msg.payload.get("tools") or []
                if not tools:
                    return
                shortlist_size = msg.payload.get("shortlist_size")
                selection_path = msg.payload.get("selection_path")
                ts = int(time.time() * 1000)
                self._turn_thinking_events.append({
                    "type": "tool_menu",
                    "tools": list(tools),
                    "shortlist_size": shortlist_size,
                    "selection_path": selection_path,
                    "timestamp": ts,
                })
                self._se(lambda em, t=tools, s=shortlist_size, p=selection_path: em.tools_snapshot(
                    t, shortlist_size=s, selection_path=p,
                ))
                return

            if state == "wait_narrator":
                if is_autonomous:
                    return
                lines = msg.payload.get("lines") or []
                if not lines:
                    return
                target = getattr(self.window, 'webview_window', self.window)
                if hasattr(target, 'evaluate_js'):
                    safe_lines = json.dumps(lines)
                    target.evaluate_js(f"window.handleWaitNarrator?.({safe_lines})")
                return

            if state in ["thinking", "thought"]:
                append = msg.payload.get("append", True)
                ts = int(time.time() * 1000)
                # logger.debug(f"[ LIMB ] Received thinking update: {snippet}")
                self.window.set_thinking_text(text, append=append)
                self.window.push_thinking_event({'type': 'thought', 'text': text, 'append': append, 'timestamp': ts})
                self._turn_thinking_events.append({'type': 'thought', 'text': text, 'append': append, 'timestamp': ts})
                self._se(lambda em, t=text, a=msg.payload.get("agent_name"), p=msg.payload.get("parent_id"):
                         em.reasoning_delta(t, agent_name=a, parent_id=p))
                if not is_autonomous:
                    self.window.update_status(state="thinking")

            elif state in ["reasoning", "thought_reasoning"]:
                append = msg.payload.get("append", True)
                ts = int(time.time() * 1000)
                # logger.debug(f"[ LIMB ] Received reasoning update: {snippet}")
                self.window.push_thinking_event({'type': 'thought', 'text': text, 'is_reasoning': True, 'append': append, 'timestamp': ts})
                self._turn_thinking_events.append({'type': 'thought', 'text': text, 'is_reasoning': True, 'append': append, 'timestamp': ts})
                self.window.set_thinking_text(text, append=append)
                self._se(lambda em, t=text, a=msg.payload.get("agent_name"), p=msg.payload.get("parent_id"):
                         em.reasoning_delta(t, agent_name=a, parent_id=p))

            elif state == "tool_start":
                tool_name = msg.payload.get("name")
                params = msg.payload.get("params", {})
                action = msg.payload.get("action")
                await self._notify_ui_tool_start(tool_name, params, action, msg.id, is_autonomous=is_autonomous)
                ts = int(time.time() * 1000)
                parent_id = msg.payload.get("parent_id")
                self._turn_thinking_events.append({
                    'type': 'tool_start',
                    'correlation_id': msg.id,
                    'name': tool_name,
                    'params': params,
                    'display_text': action or f"Executing {tool_name}...",
                    'parent_id': parent_id,
                    'timestamp': ts
                })
                self._se(lambda em, cid=msg.id, n=tool_name, p=params, a=action, pid=parent_id: em.tool_start(
                    cid, n or "unknown", args=p, display_text=a or f"Executing {n}...", parent_id=pid))

            elif state == "tool_end":
                tool_name = msg.payload.get("name")
                success = msg.payload.get("success", True)
                result = msg.payload.get("result")
                await self._notify_ui_tool_end(tool_name, {"success": success, "result": result}, msg.id, is_autonomous=is_autonomous, payload=msg.payload)
                ts = int(time.time() * 1000)
                tool_end_event = {
                    'type': 'tool_end',
                    'correlation_id': msg.id,
                    'name': tool_name,
                    'success': success,
                    'result': result,
                    'timestamp': ts,
                }
                res_dict = result if isinstance(result, dict) else {}
                for key in (
                    'download_token', 'filename', 'youtube_video_id', 'youtube_title',
                    'auto_preview', 'awaiting_user', 'map_view',
                ):
                    val = msg.payload.get(key)
                    if val is None:
                        val = res_dict.get(key)
                    if val is not None:
                        tool_end_event[key] = val
                self._turn_thinking_events.append(tool_end_event)
                self._se(lambda em, cid=msg.id, ok=success, r=result, p=msg.payload: em.tool_end(
                    cid,
                    ok=bool(ok),
                    result=r,
                    download_token=p.get('download_token'),
                    filename=p.get('filename'),
                    youtube_video_id=p.get('youtube_video_id'),
                    youtube_title=p.get('youtube_title'),
                    auto_preview=p.get('auto_preview'),
                    awaiting_user=bool(p.get('awaiting_user')),
                    map_view=p.get('map_view'),
                ))

        # 3. Execution Plan
        elif msg.type == MessageType.EXECUTION_PLAN.value:
            if self._is_voice_delivery(msg):
                return
            import time as _time
            ts = int(_time.time() * 1000)
            event = dict(msg.payload)
            event['type'] = 'activity_snapshot'
            event.setdefault('activity_type', 'PLAN')
            event['timestamp'] = ts
            self.window.push_thinking_event(event)
            self._turn_thinking_events.append(event)
            _content = msg.payload.get('content') or {}
            plan_steps = event.get('plan') or _content.get('steps') or []
            plan_goal = event.get('goal') or _content.get('goal')
            self._se(lambda em, s=plan_steps, g=plan_goal: em.plan_snapshot(s or [], goal=g))

        # Generic human-in-the-loop tool approval request (accept/decline or MCQ).
        elif msg.type == MessageType.TOOL_APPROVAL_REQUEST.value:
            if self._is_voice_delivery(msg):
                return
            approval_payload = dict(msg.payload)
            approval_conv_id = approval_payload.get("conversation_id")
            # Session isolation (issue #269): an approval/clarifying-question card
            # belongs to exactly one chat. Drop it if the Brain tagged it for a
            # conversation other than the in-flight one — the same guard AI_RESPONSE
            # uses below — so a stray card never leaks onto the chat now on screen.
            if (
                approval_conv_id
                and self.pending_conversation_id
                and approval_conv_id != self.pending_conversation_id
            ):
                logger.warning(
                    f"[ LIMB ] Session mismatch — discarding tool_approval_request for "
                    f"{approval_conv_id} (active: {self.pending_conversation_id})"
                )
                return
            # The React store guard reads camelCase `conversationId`; carry it so the
            # card is attributed to this chat and cannot stamp onto a different one.
            if approval_conv_id:
                approval_payload["conversationId"] = approval_conv_id
            self._pending_tool_approval = approval_payload
            self.window.push_tool_approval_request(approval_payload)

        # 4. Autonomous Notifications (Gmail, Cron, Heartbeat turns)
        elif msg.type == MessageType.AUTONOMOUS_NOTIFICATION.value:
            text = msg.payload.get("text")
            logger.info(f"GUI Limb: Received autonomous notification: {text[:50]}...")
            
            # Non-blocking TTS (Limb handles playback for headless Brain)
            from src.shared.config_manager import ConfigManager
            if ConfigManager.get("tts.announce_autonomous", True):
                from src.core.tts.tts_player import get_tts_player
                asyncio.create_task(get_tts_player().speak(text))
            else:
                logger.info("GUI Limb: Autonomous action announcements muted by user preference.")
            
            # Push to special thinking lane (Frontend will render as toast)
            self.window.push_thinking_event({'type': 'autonomous_notification', 'text': text})
            
            # Record in debug log & history
            self.window.push_log(f"Autonomous: {text}", "INFO")
            if self.pending_conversation_id:
                try: 
                    await self.history_repo.append_message(
                        self.pending_conversation_id, 
                        "assistant", 
                        text, 
                        metadata={"initiator": "autonomous", "hidden": True}
                    )
                except: pass

        elif msg.type == MessageType.CAPABILITY_SUGGESTION.value:
            if hasattr(self.window, "push_capability_suggestion"):
                self.window.push_capability_suggestion(dict(msg.payload))

        elif msg.type == MessageType.TURN_MODE.value:
            if self._is_voice_delivery(msg):
                return
            if hasattr(self.window, "push_turn_mode"):
                self.window.push_turn_mode(dict(msg.payload))

        # 4. Live token usage update (fires mid-stream and at turn end from Brain)
        elif msg.type == MessageType.TOKEN_USAGE.value:
            if self._is_voice_delivery(msg):
                return
            self.window.push_token_usage(msg.payload)
            self._se(lambda em, u=dict(msg.payload): em.token_usage(u))

        # 5. Standard Direct AI Response
        elif msg.type == MessageType.AI_RESPONSE.value:
            import time
            text = msg.payload.get("text", "")
            is_fragment = msg.payload.get("fragment", False)
            turn_id = msg.payload.get("turn_id") or getattr(self, "current_turn_id", None)

            if self._is_voice_delivery(msg):
                if is_fragment:
                    await self._feed_voice_turn_stream(turn_id, text)
                    return
                await self._flush_voice_turn_stream(turn_id)
                self._resolve_voice_turn(turn_id, self._sanitize_text(text))
                self._voice_turn_streams.pop(turn_id, None)
                return

            initiator = msg.payload.get("initiator", "user") or "user"
            conv_id = msg.payload.get("conversation_id")

            # Autonomous turns (anything not user/followup) that slipped through as
            # AI_RESPONSE (safety net — limb_coordinator already routes these to
            # AUTONOMOUS_NOTIFICATION upstream).
            is_system_turn = initiator not in ("user", "followup") and not conv_id
            if is_system_turn and not is_fragment:
                clean = self._sanitize_text(text)
                if clean and clean.upper() != "HEARTBEAT_OK":
                    self.window.push_thinking_event({'type': 'autonomous_notification', 'text': clean})
                self._stream_emitter = None
                return

            # Approval continuation has a new turn ID in the same conversation.
            # A late final frame must not close its emitter or erase its thoughts.
            if (turn_id and getattr(self, "current_turn_id", None)
                    and turn_id != self.current_turn_id):
                return

            # Session mismatch guard: discard responses that belong to a different
            # conversation so a late reply never lands in the user's current chat.
            if conv_id and self.pending_conversation_id and conv_id != self.pending_conversation_id:
                logger.warning(
                    f"[ LIMB ] Session mismatch — discarding response for {conv_id} "
                    f"(active: {self.pending_conversation_id})"
                )
                return

            if is_fragment:
                # Handle as streaming token fragment, matching local EventBridge pattern
                if text:
                    self._last_event_type = 'speech'
                    self._speech_buffer += text
                    # NB: the legacy per-token push_caption() IPC call was removed —
                    # window.handleCaptionUpdate isn't installed in the production
                    # runtime (only the testkit frame recorder), so it was a no-op
                    # that still paid a full evaluate_js round-trip per token. The
                    # answer now streams solely through the typed text_delta path.
                    self._se(lambda em, t=text: em.text_delta(t))
                return

            # --- FINAL TURN COMPLETION ---

            # Final flush of remaining speech buffer into the thinking stream
            ts = int(time.time() * 1000)
            if self._speech_buffer:
                clean_speech = self._sanitize_text(self._speech_buffer)
                
                # Suppress pushing raw JSON plans as speech
                is_json_plan = False
                stripped = clean_speech.strip()
                if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
                    try:
                        obj = json.loads(stripped)
                        if (isinstance(obj, dict) and "steps" in obj) or isinstance(obj, list):
                            is_json_plan = True
                    except Exception:
                        pass
                
                if not is_json_plan:
                    self.window.push_thinking_event({'type': 'speech', 'text': clean_speech, 'append': False, 'timestamp': ts})
                    self._turn_thinking_events.append({'type': 'speech', 'text': clean_speech, 'append': False, 'timestamp': ts})
                    self._se(lambda em, t=clean_speech: em.speech_finalize(t))
                self._speech_buffer = ""

            # Calculate turn duration
            turn_duration_ms = None
            if hasattr(self, '_turn_start_time') and self._turn_start_time:
                turn_duration_ms = int((time.time() - self._turn_start_time) * 1000)
                self._turn_start_time = None

            # Append turn_complete event
            self._turn_thinking_events.append({
                'type': 'turn_complete',
                'response': self._sanitize_text(text),
                'timestamp': ts
            })
            self._se(lambda em, r=self._sanitize_text(text), d=turn_duration_ms: em.run_finished(r, turn_duration_ms=d))

            # Calculate thinking duration from collected thinking events
            thinking_duration_ms = None
            first_thought = next((e for e in self._turn_thinking_events if e.get("type") in ("thought", "thinking", "reasoning")), None)
            if first_thought and first_thought.get("timestamp"):
                end_evt = next((e for e in self._turn_thinking_events if e.get("type") == "speech"), None)
                end_ts = end_evt.get("timestamp") if (end_evt and end_evt.get("timestamp")) else ts
                if end_ts and end_ts > first_thought["timestamp"]:
                    thinking_duration_ms = int(end_ts - first_thought["timestamp"])

            # Persist completed turn to history — user message first, then assistant.
            # Both are saved here (after the Brain responds) so that the turn_coordinator
            # never sees the current user message in DB history during its own dispatch,
            # which prevents the dual-load duplication in the LangGraph message channel.
            turn_id = msg.payload.get("turn_id") or getattr(self, "current_turn_id", None)
            metadata = {}
            if self.pending_conversation_id and (text.strip() or self._turn_thinking_events):
                # Built for EVERY turn we render — it feeds the answer bubble below, so
                # it must not depend on whether we're the one writing to the DB.
                consolidated_thoughts = self._consolidate_and_sanitize_thoughts(self._turn_thinking_events)
                metadata = {
                    "thoughts": consolidated_thoughts,
                    "initiator": initiator,
                    "turn_duration_ms": msg.payload.get("turn_duration_ms") or turn_duration_ms or int(msg.payload.get("duration", 0) * 1000),
                    "thinking_duration_ms": thinking_duration_ms,
                    "prompt_tokens": msg.payload.get("prompt_tokens"),
                    "completion_tokens": msg.payload.get("completion_tokens"),
                    "total_tokens": msg.payload.get("total_tokens"),
                    "reasoning_tokens": msg.payload.get("reasoning_tokens"),
                    "context_window": msg.payload.get("context_window"),
                    "ttft": msg.payload.get("ttft"),
                    "stop_reason": msg.payload.get("stop_reason"),
                    "speed": msg.payload.get("speed")
                }
                artifacts = [
                    e for e in consolidated_thoughts
                    if e.get("type") == "tool_end" and e.get("success") and e.get("download_token")
                ]
                if artifacts:
                    metadata["artifacts"] = artifacts
                if getattr(self, "_pending_tool_approval", None):
                    metadata["actions"] = {
                        "type": "tool_approval",
                        "token": self._pending_tool_approval.get("token"),
                        "decisionType": self._pending_tool_approval.get("decision_type") or "accept_decline",
                        "prompt": self._pending_tool_approval.get("prompt"),
                        "title": self._pending_tool_approval.get("title"),
                        "questions": self._pending_tool_approval.get("questions"),
                        "options": self._pending_tool_approval.get("options"),
                    }

            # Write ONLY turns this Limb started. The Brain fans every turn out to every
            # viewer (#709), so a turn driven from the phone also arrives here — and the
            # Brain already persists those (#189). Writing them again duplicated the user
            # message and filed the reply under whatever chat this window had open, since
            # pending_conversation_id is OUR open chat, not the phone's (issue #711).
            if self._turn_is_mine and metadata:
                try:
                    # 1. Persist the user message that triggered this turn
                    pending_user_content = getattr(self, "_pending_user_content", None)
                    pending_user_text = getattr(self, "_pending_user_text", None)
                    pending_user_metadata = getattr(self, "_pending_user_metadata", None)
                    if pending_user_content is not None:
                        await self.history_repo.append_message(
                            self.pending_conversation_id, "user", pending_user_content, metadata=pending_user_metadata
                        )
                        self._pending_user_content = None
                        self._pending_user_metadata = None

                        # Dynamic title generation for "New Chat" (moved here from send_input)
                        conv = await self.history_repo.get_conversation(self.pending_conversation_id)
                        if conv and conv.get("title") == "New Chat" and pending_user_text:
                            derived_title = self.history_repo.derive_title(pending_user_text)
                            await self.history_repo.update_conversation_title(self.pending_conversation_id, derived_title)
                            conv["title"] = derived_title
                            self.window.update_conversation_in_store(conv)

                    # 2. Persist the assistant response
                    await self.history_repo.append_message(self.pending_conversation_id, "assistant", text, metadata=metadata, message_id=turn_id)
                except Exception as e:
                    logger.error(f"Failed to persist turn: {e}")

            # Push final answer bubble to replace raw DOM stream
            conv_data = self.pending_conversation_id
            if self.pending_conversation_id:
                try:
                    conv = await self.history_repo.get_conversation(self.pending_conversation_id)
                    if conv:
                        conv_data = conv
                except Exception as e:
                    logger.error(f"Failed to fetch conversation for UI response: {e}")

            self.window.push_ai_response(self._sanitize_text(text), conversation_id=conv_data, message_id=turn_id, metadata=metadata)

            # Update token ticker with final counts from the completed turn
            if metadata.get("total_tokens"):
                self.window.push_token_usage({
                    "prompt_tokens": metadata.get("prompt_tokens"),
                    "completion_tokens": metadata.get("completion_tokens"),
                    "total_tokens": metadata.get("total_tokens"),
                })

            # Resolve any stray voice future (should not happen if delivery tag is correct)
            self._resolve_voice_turn(turn_id, self._sanitize_text(text))

            # Reset turn state
            self._turn_thinking_events = []
            self._speech_buffer = ""
            self._last_event_type = None
            self._stream_emitter = None
            self._turn_is_mine = False
            self._pending_tool_approval = None
            self._pending_user_metadata = None
            self.window.update_status(state="ready")

    async def send_input(self, text: str, image_base64: Optional[str] = None, content_blocks: Optional[list] = None, conversation_id: Optional[str] = None, category: Optional[str] = None, turn_id: Optional[str] = None, thinking_enabled: Optional[bool] = None, delivery: Optional[str] = None, domain: Optional[str] = None, retry: Optional[bool] = None, keep_count: Optional[int] = None, metadata: Optional[dict] = None) -> bool:
        """Override to track conversation_id, persist user inputs, and prevent global fallback."""
        from src.shared.turn_delivery import resolve_delivery, DELIVERY_VOICE

        resolved_delivery = resolve_delivery(category, delivery)
        is_voice = resolved_delivery == DELIVERY_VOICE

        if not turn_id:
            import uuid
            turn_id = str(uuid.uuid4())

        if not is_voice:
            self.current_turn_id = turn_id

        # Prevent LangGraph global thread collision by force-allocating a unique session ID if none exists
        if not is_voice and not conversation_id:
            conversation_id = await self._ensure_conversation_id()
            if not conversation_id:
                logger.error("[ LIMB ] : No conversation_id and failed to allocate one.")
                return False

        if not is_voice:
            self.pending_conversation_id = conversation_id
            self._turn_thinking_events = []
            self._speech_buffer = ""
            self._last_event_type = None
            import time
            self._turn_start_time = time.time()

            # Start a fresh typed-stream turn (drives the React render).
            from src.remote.stream_events import StreamEventEmitter
            self._stream_emitter = StreamEventEmitter(turn_id)
            self._se(lambda em: em.run_started(turn_id))

            # Stage user message for persistence AFTER the turn completes.
            self._pending_user_content = content_blocks if content_blocks else text
            self._pending_user_text = text
            self._pending_user_metadata = metadata
            # Claim this turn: only turns we sent are ours to persist.
            self._turn_is_mine = True

        return await super().send_input(
            text,
            image_base64,
            content_blocks,
            conversation_id if not is_voice else None,
            category=category,
            turn_id=turn_id,
            thinking_enabled=thinking_enabled,
            delivery=resolved_delivery,
            domain=domain,
            retry=retry,
            keep_count=keep_count,
            metadata=metadata,
        )

    async def _ensure_conversation_id(self) -> Optional[str]:
        """Return the active conversation id, creating one if needed (e.g. voice turns)."""
        if self.pending_conversation_id:
            return self.pending_conversation_id
        try:
            from src.shared.config_manager import ConfigManager
            model_name = ConfigManager.get_active_model()
            conv = await self.history_repo.create_conversation(model=model_name)
            conversation_id = conv.get("id")
            if conversation_id:
                logger.debug(f"[ LIMB ] : Created conversation for turn: {conversation_id}")
            return conversation_id
        except Exception as e:
            logger.error(f"[ LIMB ] : Failed to create conversation: {e}")
            return None

    async def run_voice_turn(
        self,
        text: str,
        on_sentence: Optional[Callable[[str], Any]] = None,
        timeout: float = 120.0,
    ) -> str:
        """
        Send a voice-transcribed message to the Brain and await the spoken reply.

        Used by the Limb's voice mode: unlike send_input (fire-and-forget), this
        correlates the asynchronous AI_RESPONSE back to the caller so voice mode
        can TTS the reply. Returns "" if not connected, on error, or on timeout.

        If ``on_sentence`` is given (epic #291 Phase 2), streaming AI_RESPONSE
        fragments are buffered and split into sentences as they arrive, calling
        ``on_sentence`` once per completed sentence (including any trailing
        partial sentence once the turn finishes) so voice mode can start
        speaking before the full reply is back. Without it, fragments are
        dropped and only the final text is returned, matching Phase 1 behavior.
        """
        if not self.websocket:
            logger.warning("[ VOICE ] : Not connected to Brain; dropping voice turn.")
            return ""

        import uuid
        turn_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[str]" = loop.create_future()
        self._voice_turn_futures[turn_id] = future
        if on_sentence is not None:
            from src.core.tts.sentence_accumulator import SentenceAccumulator

            self._voice_turn_streams[turn_id] = (SentenceAccumulator(), on_sentence)

        sent = await self.send_input(
            text,
            category="voice",
            turn_id=turn_id,
            thinking_enabled=False,
            delivery="voice",
        )
        if not sent:
            self._voice_turn_futures.pop(turn_id, None)
            self._voice_turn_streams.pop(turn_id, None)
            return ""

        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[ VOICE ] : Brain reply timed out after {timeout}s.")
            return ""
        finally:
            self._voice_turn_futures.pop(turn_id, None)
            self._voice_turn_streams.pop(turn_id, None)

    async def _feed_voice_turn_stream(self, turn_id: Optional[str], delta: str) -> None:
        """Feed a streaming AI_RESPONSE fragment into the turn's sentence buffer."""
        if not turn_id or not delta:
            return
        entry = self._voice_turn_streams.get(turn_id)
        if not entry:
            return
        accumulator, on_sentence = entry
        for sentence in accumulator.feed(delta):
            self._voice_status_last_spoken[turn_id] = time.monotonic()
            await self._maybe_await(on_sentence(sentence))

    async def _flush_voice_turn_stream(self, turn_id: Optional[str]) -> None:
        """Speak any trailing partial sentence left in the buffer at turn end."""
        if not turn_id:
            return
        entry = self._voice_turn_streams.get(turn_id)
        if not entry:
            return
        accumulator, on_sentence = entry
        remainder = accumulator.flush()
        if remainder:
            self._voice_status_last_spoken[turn_id] = time.monotonic()
            await self._maybe_await(on_sentence(remainder))

    async def _maybe_speak_voice_status(self, turn_id: Optional[str], label: str) -> None:
        """Speak a status-phase label as narration during a long voice wait.

        Restricted to escalate / long-wait style labels. Early GUI processing
        strings ("Let me think…", "Processing prompt…") were being TTS'd by Orpheus
        before tier-1 "Opening youtube." acks — false canned filler. Quick-lane
        turns no longer emit those early statuses from the Brain; this gate is
        belt-and-suspenders if any still arrive.
        """
        if not turn_id or not label:
            return
        if not self._voice_status_label_ok(label):
            return
        entry = self._voice_turn_streams.get(turn_id)
        if not entry:
            return
        last = self._voice_status_last_spoken.get(turn_id, 0.0)
        now = time.monotonic()
        if now - last < self._VOICE_STATUS_DEBOUNCE_S:
            return
        self._voice_status_last_spoken[turn_id] = now
        _, on_sentence = entry
        await self._maybe_await(on_sentence(label.rstrip("…").strip()))

    @staticmethod
    def _voice_status_label_ok(label: str) -> bool:
        """Never TTS harness status labels on voice — they sound like canned fillers.

        PROCESSING/FORMULATING strings ("Let me think…", "On it…", "Working on it…")
        were being spoken by Orpheus instead of the dynamic filler / real reply.
        Escalate waits already get a spoken ack from the voice model; mid-wait
        progress uses dynamic filler, not GUI status text.
        """
        return False


    @staticmethod
    async def _maybe_await(result: Any) -> None:
        """Await ``result`` if it is awaitable; otherwise return immediately."""
        import inspect

        if inspect.isawaitable(result):
            await result

    def _resolve_voice_turn(self, turn_id: Optional[str], text: str) -> None:
        """Resolve a pending voice-turn future with the final reply text."""
        if not turn_id:
            return
        future = self._voice_turn_futures.get(turn_id)
        if future and not future.done():
            future.set_result(text or "")

    def _fail_pending_voice_turns(self) -> None:
        """Unblock all pending voice-turn waiters (e.g. on Brain error) with ''."""
        for future in list(self._voice_turn_futures.values()):
            if not future.done():
                future.set_result("")

    async def send_model_update(self, model_config: dict) -> bool:
        """
        Notify the Brain about a model configuration change.
        """
        if not self.websocket:
            return False
            
        msg = ChadMessage(MessageType.SET_MODEL_CONFIG, model_config)
        try:
            await self.websocket.send(msg.to_json())
            return True
        except Exception as e:
            logger.error(f"Failed to send model update: {e}")
            return False

    async def _notify_ui_tool_start(self, tool_name, params, action_msg, tool_id, is_autonomous=False):
        """Helper to trigger MainWindow's tool start sequence."""
        display_text = action_msg or f"Executing {tool_name}..."
        event_data = {
            'type': 'tool_start',
            'correlation_id': tool_id,
            'name': tool_name,
            'params': params,
            'display_text': display_text
        }
        self.window.push_thinking_event(event_data)
        if not is_autonomous:
            self.window.update_status(text=f"EXECUTING: {display_text}", state="thinking")

    async def _notify_ui_tool_end(self, tool_name, result, tool_id, is_autonomous=False, payload=None):
        """Helper to trigger MainWindow's tool end sequence."""
        success = result.get('success', False)
        event_data = {
            'type': 'tool_end',
            'correlation_id': tool_id,
            'name': tool_name,
            'success': success,
            'result': result.get('result') if success else (result.get('error') or result.get('result') or "Unknown error")
        }
        if payload:
            for key in (
                'download_token', 'filename', 'youtube_video_id', 'youtube_title',
                'auto_preview', 'map_view',
            ):
                if payload.get(key) is not None:
                    event_data[key] = payload.get(key)
        self.window.push_thinking_event(event_data)
        if not is_autonomous:
            self.window.update_status(state="ready")

    async def interactive_loop(self):
        """No-op for GUI Limb."""
        pass

    def abort_active_turn(self, loop=None) -> None:
        """User cancelled — flush buffered events, commit partial bubble, clear state.

        Mirrors the normal ``AI_RESPONSE`` turn-complete handler so cancelled turns
        produce a frozen bubble with all accumulated thoughts/tools/text, instead of
        silently disappearing. The key insight: treat cancellation as a turn end.

        Order of operations (matches the normal turn_complete path):
          1. Flush ``_stream_buffer`` + emit ``run_finished(stop_reason='user_cancelled')``
             so the frontend reducer transitions lifecycle→finished and the stream widget
             freezes in place.
          2. Call ``push_ai_response`` with whatever text was accumulated — this triggers
             ``window.addAIMessageToChat`` → ``store.addChatMessage``, exactly as normal.
          3. Persist user message + partial assistant response to history DB
             (fire-and-forget so the UI is never blocked).
          4. Clear all live turn state.
        """
        import asyncio as _asyncio

        # Snapshot before clearing — order is critical.
        snapshot_conv_id      = self.pending_conversation_id
        snapshot_user_content = getattr(self, '_pending_user_content', None)
        snapshot_user_text    = getattr(self, '_pending_user_text', None)
        snapshot_thinking     = list(self._turn_thinking_events)
        snapshot_speech       = self._speech_buffer  # partial answer text

        # --- 1. Flush the stream buffer and emit run_cancelled ---------------
        # Combine any events still sitting in the flush-timer buffer with the
        # cancellation signal so the frontend sees the complete partial turn.
        if self._stream_emitter is not None:
            try:
                cancel_events = self._stream_emitter.run_cancelled()
                all_events = list(self._stream_buffer) + cancel_events
                self._stream_buffer = []
                if all_events:
                    self.window.push_stream_event(all_events)
            except Exception as e:
                logger.debug(f"[ LIMB ] abort: stream flush failed: {e}")
        else:
            self._stream_buffer = []

        # --- 2. Clear the flush timer ----------------------------------------
        if self._stream_flush_handle is not None:
            self._stream_flush_handle.cancel()
            self._stream_flush_handle = None

        # --- 3. Commit the partial bubble via the normal push_ai_response path --
        # ``push_ai_response`` calls ``window.addAIMessageToChat`` which calls
        # ``store.addChatMessage``.  addChatMessage reads ``streamTurn`` (now at
        # lifecycle=finished) to extract thoughts, then clears it — the same path
        # every normal turn takes.
        partial_text = self._sanitize_text(snapshot_speech)
        if snapshot_conv_id:
            conv_data = snapshot_conv_id
            self.window.push_ai_response(
                partial_text,
                conversation_id=conv_data,
                metadata={"stop_reason": "user_cancelled"},
            )

        # --- 4. Persist to history DB (fire-and-forget) ----------------------
        if snapshot_conv_id and snapshot_user_content is not None:
            async def _persist():
                try:
                    await self.history_repo.append_message(
                        snapshot_conv_id, "user", snapshot_user_content
                    )
                    if snapshot_user_text:
                        try:
                            conv = await self.history_repo.get_conversation(snapshot_conv_id)
                            if conv and conv.get("title") == "New Chat":
                                title = self.history_repo.derive_title(snapshot_user_text)
                                await self.history_repo.update_conversation_title(snapshot_conv_id, title)
                                conv["title"] = title
                                self.window.update_conversation_in_store(conv)
                        except Exception as e:
                            logger.debug(f"[ LIMB ] abort: title update failed: {e}")
                    cleaned = self._consolidate_and_sanitize_thoughts(snapshot_thinking)
                    await self.history_repo.append_message(
                        snapshot_conv_id, "assistant", partial_text,
                        metadata={"thoughts": cleaned, "stop_reason": "user_cancelled"},
                    )
                except Exception as e:
                    logger.error(f"[ LIMB ] Failed to persist aborted turn: {e}", exc_info=True)

            if loop is not None:
                _asyncio.run_coroutine_threadsafe(_persist(), loop)
            else:
                try:
                    _asyncio.get_running_loop().create_task(_persist())
                except RuntimeError:
                    logger.warning("[ LIMB ] abort_active_turn: no event loop — DB write skipped")

        # --- 5. Clear all live state -----------------------------------------
        # pending_conversation_id is cleared AFTER snapshotting above.  Any
        # late-arriving AI_RESPONSE from the Brain hits the
        # ``if self.pending_conversation_id`` guard in the normal turn_complete
        # handler and is silently discarded — preventing a duplicate bubble.
        self._stream_emitter = None
        self._turn_thinking_events = []
        self._speech_buffer = ""
        self._last_event_type = None
        self._turn_start_time = None
        self._pending_user_content = None
        self._pending_user_text = None
        self.pending_conversation_id = None
        # Give up the claim too. Clearing pending_conversation_id already blocks the
        # write, but leaving this set would mean a later turn from another client is
        # one stray assignment away from being persisted here (issue #711).
        self._turn_is_mine = False

    def _sanitize_text(self, text: str) -> str:
        from src.shared.plan_markers import sanitize_response_text
        return sanitize_response_text(text)

    def _consolidate_and_sanitize_thoughts(self, events: list) -> list:
        """Merge consecutive speech/thought deltas, then strip orchestration markers.

        Thought/reasoning tokens stream as many tiny append chunks whose leading
        space lives ON the delta (e.g. "The", " user", " wants"). Sanitizing each
        chunk with strip() before joining eats those spaces and produces
        "Theuserwants" in persisted history. Speech already coalesced first —
        thoughts must do the same.
        """
        if not events:
            return []

        THOUGHT_TYPES = frozenset({"thought", "thinking", "reasoning"})
        consolidated = []
        current_speech = []
        current_thought = []
        thought_meta = None  # type: Optional[dict]
        import time

        def _flush_speech(next_event: dict | None = None) -> None:
            nonlocal current_speech
            if not current_speech:
                return
            full_text = "".join(current_speech)
            clean_full = self._sanitize_text(full_text)
            if clean_full:
                consolidated.append({
                    "type": "speech",
                    "text": clean_full,
                    "timestamp": (next_event or {}).get("timestamp", int(time.time() * 1000)),
                })
            current_speech = []

        def _flush_thought(next_event: dict | None = None) -> None:
            nonlocal current_thought, thought_meta
            if not current_thought or thought_meta is None:
                current_thought = []
                thought_meta = None
                return
            full_text = "".join(current_thought)
            clean_full = self._sanitize_text(full_text)
            if clean_full:
                start_ts = thought_meta.get("timestamp")
                last_ts = thought_meta.get("last_timestamp")
                end_ts = (next_event or {}).get("timestamp") or last_ts
                duration_ms = None
                if start_ts and end_ts and end_ts > start_ts:
                    duration_ms = int(end_ts - start_ts)
                event = {
                    "type": thought_meta.get("type") or "thought",
                    "text": clean_full,
                    "timestamp": start_ts or int(time.time() * 1000),
                }
                if duration_ms is not None and duration_ms > 0:
                    event["durationMs"] = duration_ms
                if thought_meta.get("is_reasoning"):
                    event["is_reasoning"] = True
                if thought_meta.get("agent_name") is not None:
                    event["agent_name"] = thought_meta["agent_name"]
                if thought_meta.get("parent_id") is not None:
                    event["parent_id"] = thought_meta["parent_id"]
                consolidated.append(event)
            current_thought = []
            thought_meta = None

        for event in events:
            etype = event.get("type")
            if etype == "speech":
                _flush_thought(event)
                current_speech.append(event.get("text", ""))
            elif etype in THOUGHT_TYPES:
                _flush_speech(event)
                # Break the merge when attribution changes (matches ThinkingWidget).
                if (
                    thought_meta is not None
                    and (
                        thought_meta.get("agent_name") != event.get("agent_name")
                        or thought_meta.get("parent_id") != event.get("parent_id")
                        or thought_meta.get("type") != etype
                    )
                ):
                    _flush_thought(event)
                if thought_meta is None:
                    thought_meta = {
                        "type": etype,
                        "timestamp": event.get("timestamp"),
                        "last_timestamp": event.get("timestamp"),
                        "is_reasoning": event.get("is_reasoning"),
                        "agent_name": event.get("agent_name"),
                        "parent_id": event.get("parent_id"),
                    }
                else:
                    if event.get("timestamp"):
                        thought_meta["last_timestamp"] = event.get("timestamp")
                current_thought.append(event.get("text", ""))
            else:
                _flush_speech(event)
                _flush_thought(event)
                consolidated.append(event)

        _flush_speech()
        _flush_thought()
        return consolidated
