"""
Gemini Live API Client - Network Layer
Implementation of BaseModel for Gemini's native audio streaming API.

.. deprecated::
    DEPRECATED — Gemini Live (cloud native-audio "live voice mode") is no longer used.
    Chad runs fully local (LM Studio): text turns route to the local model
    (`local_models.use_local: true`) and hands-free use goes through the discrete
    local voice pipeline (faster-whisper STT -> text turn -> Kokoro TTS; see
    docs/features/VOICE_MODE.md). This module is retained for reference only and is
    NOT wired into any active path — the model router never routes here while a local
    model is active, and `session.default_voice_mode` is off. Do not build new
    functionality on it. Instantiating it logs a deprecation warning.

This is the Network Layer of the 3-layer architecture:
- Network Layer: models/gemini_live.py (WebSocket management) <- YOU ARE HERE
- Logic Layer: core/response_parser.py (response parsing)
- Data Layer: core/history_utils.py (history conversion)

Responsibilities:
- WebSocket connection lifecycle
- Audio chunk streaming
- Session management
- Tool response sending
"""

import logging
import os
import sys
import asyncio
import json
import time
import re
from typing import Dict, Any, AsyncGenerator, Optional, List, TYPE_CHECKING
from google.genai import types

if TYPE_CHECKING:
    from google import genai
    from google.genai import types

from models.base_model import BaseModel
from src.shared.exceptions import ModelError
from src.shared.logger import setup_logger
from src.shared.terminal_debug import log_tool_call, log_thinking, log_reasoning
from src.shared.debug_helper import debug_print
from src.core.utils.response_parser import ResponseParser
from src.core.utils.history_utils import reconstruct_history_safe

# Try to import websockets for ConnectionClosedError handling
try:
    import websockets.exceptions
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


logger = setup_logger("GeminiLive")
logger.setLevel(logging.WARNING)


class GeminiLive(BaseModel):
    """
    Gemini Live API implementation with native audio streaming.
    Uses the multimodal Live API for low-latency voice interaction.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Gemini Live client.
        
        Args:
            config: Model configuration including model name, temperature, etc.
        """
        super().__init__(config)

        # DEPRECATED path — see module docstring. Loud warning so an accidental route
        # here (e.g. someone flipping local_models.use_local off) is immediately visible.
        logger.warning(
            "[ DEPRECATED ] GeminiLive instantiated — cloud live voice mode is no longer "
            "used. Chad runs local (LM Studio) + discrete local voice pipeline. This "
            "adapter is retained for reference only.")

        from src.shared.config_manager import ConfigManager
        self._api_key = ConfigManager.get_secret('GEMINI_API_KEY')
        if not self._api_key:
            raise ModelError("GEMINI_API_KEY not found in secrets or environment")
        
        # Lazy-initialized client (defers google.genai import)
        self._client = None
        self.session = None
        self.current_tools = []
        self.is_connected = False  # Protocol readiness flag - True only after handshake completes
        
        # Audio format configuration
        # Input: 16,000 Hz (required by Gemini Live API)
        # Output: 24,000 Hz (Gemini Live API outputs at this rate)
        self.audio_config = {
            'input_sample_rate': 16000,
            'output_sample_rate': 24000,
            'channels': 1,
            'encoding': 'pcm'  # 16-bit PCM, little-endian
        }
        
        # Track if AI is expecting a follow-up (asked a question)
        self.expecting_followup = False
        
        # Tool processing flag - set True ONLY when a literal function_call is found in response
        # Cleared when tool call is yielded. No longer uses keyword detection.
        self._is_processing_tool = False
        
        # ========== PERSISTENT SESSION STATE ==========
        # Audio Gate: Controls whether audio is flowing to the WebSocket
        self.is_streaming = False  # True = audio flows, False = audio paused
        
        # Persistent session management
        self._persistent_session = None  # The long-lived WebSocket session
        self._persistent_context = None  # The connection context manager
        self._session_config = None  # Stored config for session refresh
        self._tools_list = None  # Stored tools for session refresh
        
        # Session refresh timer (10 minutes = 600 seconds)
        self._session_refresh_interval = 600.0
        self._last_session_activity = time.time()
        self._refresh_task = None  # Background task for session refresh
        
        # New: Response queue for persistent background receiving
        self._response_queue = asyncio.Queue()
        self._receive_task = None
        
        # Agentic pause: When True, the background receive loop waits instead of receiving
        self._agentic_pause = False
        
        # ========== PHYSICS-FIRST ARCHITECTURE ==========
        # Generation complete flag: Set when turn_complete is received from network.
        # This is a READ-ONLY flag for SessionManager. GeminiLive does NOT act on it.
        self._generation_complete = False
    
    @property
    def client(self):
        """Lazy-load the genai.Client on first access."""
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client
    
    @property
    def is_generation_complete(self) -> bool:
        """Return True if the model has finished generating for this turn."""
        return self._generation_complete
    
    def reset_generation_flag(self) -> None:
        """Reset the generation complete flag for a new turn."""
        self._generation_complete = False
    
    
    @property
    def is_processing_tool(self) -> bool:
        """Return True if the model is currently processing a tool call."""
        return self._is_processing_tool
    
    def get_capabilities(self) -> Dict[str, bool]:
        """Return model capabilities."""
        return {
            'audio_input': True,
            'audio_output': True,
            'vision': True,
            'function_calling': True,
            'streaming': True
        }
    
    def _build_cache_optimized_instruction(
        self, 
        base_instruction: str, 
        tool_schemas: List[Dict[str, Any]],
        memory_block: Optional[str] = None
    ) -> str:
        """
        Build a system instruction optimized for Gemini's implicit context caching.
        
        CACHE OPTIMIZATION STRATEGY:
        1. STABLE PREFIX (cached at 90% discount):
           - Tool definitions (JSON, deterministic)
           - Core personality (from config, rarely changes)
        2. DYNAMIC SUFFIX (full price, but small):
           - User profile / memory facts (changes occasionally)
        
        This ordering ensures the first 2048+ tokens remain stable across requests,
        maximizing cache hit rate and reducing costs by up to 90%.
        
        Args:
            base_instruction: Core system instruction from config
            tool_schemas: List of tool schemas (for documentation in prompt)
            memory_block: Optional memory facts block
            
        Returns:
            Cache-optimized system instruction string
        """
        parts = []
        
        # SECTION 1: STABLE PREFIX - Tool Schema Summary (cached)
        # Instead of embedding full JSON, we create a condensed tool summary
        # that helps the model understand available tools while keeping prefix stable
        if tool_schemas:
            tool_names = [s.get('name', 'unknown') for s in tool_schemas]
            tools_summary = f"""### AVAILABLE TOOLS ({len(tool_schemas)} tools)
{', '.join(tool_names)}

Note: Use tools silently. Execute first, speak after results."""
            parts.append(tools_summary)
        
        # SECTION 2: CORE PERSONALITY (stable, from config)
        if base_instruction:
            parts.append(base_instruction)
        
        # SECTION 3: DYNAMIC SUFFIX - Memory (changes per session)
        if memory_block:
            parts.append(memory_block)
        
        return "\n\n".join(parts)
    
    async def stream_audio(
        self,
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream audio to Gemini and receive responses.
        
        Uses persistent session if available (no cold start!).
        Falls back to ephemeral session if persistent isn't ready.
        
        Args:
            audio_input: Async generator yielding audio chunks
            tool_schemas: List of available tool schemas
            
        Yields:
            Response dictionaries (audio, tool_call, turn_complete, error)
        """
        # ========== PERSISTENT SESSION: USE IF AVAILABLE ==========
        if self._persistent_session and self.is_connected:
            debug_print("DEBUG: Using PERSISTENT session (no cold start!)")
            
            # Drain the queue of any stale responses before starting a new turn
            while not self._response_queue.empty():
                self._response_queue.get_nowait()
                
            async for response in self._stream_with_persistent_queue(audio_input, tool_schemas):
                yield response
            return
        
        # ========== EPHEMERAL SESSION: FALLBACK ==========
        debug_print("DEBUG: Using EPHEMERAL session (cold start)")
        try:
            from google.genai import types
            self.current_tools = tool_schemas
            
            # Convert tool schemas to function declarations
            function_declarations = []
            if tool_schemas:
                for schema in tool_schemas:
                    try:
                        func_decl = types.FunctionDeclaration(
                            name=schema['name'],
                            description=schema['description'],
                            parameters=schema['parameters']
                        )
                        function_declarations.append(func_decl)
                    except Exception as e:
                        logger.error(f"Failed to create function declaration for '{schema.get('name')}': {e}")
            
            # Build tools list with Google Search and function declarations
            tools_list = []
            # Add Google Search tool for grounding
            try:
                tools_list.append(types.Tool(google_search=types.GoogleSearch()))
            except (TypeError, AttributeError) as e:
                logger.warning(f"GoogleSearch type not available ({e}), continuing without Google Search grounding")
            # Add function declarations if any
            if function_declarations:
                tools_list.append(types.Tool(function_declarations=function_declarations))
            
            # Create generation config - STRICTLY AUDIO ONLY to avoid mixed modality conflict
            generation_config = types.GenerationConfig(
                temperature=self.temperature,
                response_modalities=["AUDIO"]  # Audio only - must be list, not string
            )
            
            # Verify response_modalities is correct format
            if not isinstance(generation_config.response_modalities, list) or generation_config.response_modalities != ["AUDIO"]:
                logger.error(f"Invalid response_modalities: {generation_config.response_modalities}, expected ['AUDIO']")
                raise ModelError(f"Invalid response_modalities format: must be ['AUDIO']")
            
            # Create speech_config with voice config to force native audio generation
            voice_name = self.config.get('voice_name', 'Kore')
            speech_config = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            )
            config = types.LiveConnectConfig(
                generation_config=generation_config,
                system_instruction=self.config.get('system_instruction', ''),
                speech_config=speech_config,  # CRITICAL: Force native audio generation
                output_audio_transcription=types.AudioTranscriptionConfig(),
                input_audio_transcription=types.AudioTranscriptionConfig()
            )
            
            # Add tools if we have any
            if tools_list:
                config.tools = tools_list
            
            logger.debug(f"LiveConnectConfig: response_modalities={config.generation_config.response_modalities}, model={self.model_name}, voice_name={voice_name}")
            
            # Start Live session with timeout for slow connections
            async def _establish_connection():
                connect_context = self.client.aio.live.connect(
                    model=self.model_name,
                    config=config
                )
                return connect_context
            
            connect_context = await _establish_connection()
            
            # Wrap the actual connection with timeout
            try:
                session = await asyncio.wait_for(connect_context.__aenter__(), timeout=30.0)
            except asyncio.TimeoutError:
                raise ModelError("Connection timeout: WebSocket handshake took longer than 30 seconds. Check your network connection.")
            
            # Store context for cleanup in finally block
            self._connect_context = connect_context
            
            try:
                self.session = session
                
                # Wait a brief moment to ensure handshake is processed
                await asyncio.sleep(0.1)
                
                # Create task for sending audio (runs in background)
                send_task = asyncio.create_task(
                    self._send_audio(session, audio_input)
                )
                
                # Wait for audio pipe to establish before injecting history
                await asyncio.sleep(0.5)
                
                # Mark connection as ready
                self.is_connected = True
                logger.debug("Protocol handshake complete - connection ready for commands")
                
                # Inject conversation history if available
                memory_manager = self.config.get('memory_manager')
                try:
                    if memory_manager and memory_manager.has_history():
                        context_turns = self.config.get('context_turns', 20)
                        history_turns = memory_manager.get_history_turns(max_turns=context_turns)
                        
                        if history_turns:
                            try:
                                clean_history = reconstruct_history_safe(history_turns)
                                
                                if clean_history:
                                    # Verify first turn has role="user"
                                    if clean_history[0].role == "model":
                                        logger.warning("First turn has role='model' - violates User-First rule. Skipping history injection.")
                                        clean_history = None
                                    
                                    if clean_history:
                                        logger.info(f"Injecting {len(clean_history)} turns of history...")
                                        client_content = types.LiveClientContent(turns=clean_history)
                                        await session.send_client_content(client_content)
                                        logger.info(f"Memory restored successfully ({memory_manager.get_history_count()} total turns, {len(clean_history)} turns in context)")
                            except Exception as e:
                                logger.warning(f"Failed to restore memory: {e}")
                                logger.info("Starting session with empty context to prevent crash.")
                except Exception as e:
                    logger.warning(f"Failed to inject conversation history: {e}", exc_info=True)
                    logger.info("Continuing session without history injection.")
                
                # Process responses as they come in
                try:
                    tool_call_seen = False
                    
                    # Log thinking at turn start (Removed to fix Ghost Thinking - SessionManager handles this)
                    # log_thinking(logger=logger)
                    
                    async for response in self._receive_responses(session):
                        response_type = response.get('type')
                        
                        # Trigger thinking loop if model is processing
                        if response_type in ['thinking', 'reasoning', 'tool_call', 'tool_call_batch']:
                            sound_manager = self.config.get('sound_manager')
                            if sound_manager:
                                try:
                                    # sound_manager.start_thinking_loop()
                                    pass
                                except Exception as e:
                                    logger.warning(f"Failed to start thinking loop: {e}")
                        
                        yield response
                        
                        # Track if we've seen a tool call
                        if response_type == 'tool_call':
                            tool_call_seen = True
                            logger.debug("Tool call detected - will keep socket alive")
                        
                        # Only break on turn_complete if NO tool_call was seen
                        if response_type == 'turn_complete':
                            has_tool = response.get('has_tool', False)
                            
                            if not tool_call_seen and not has_tool:
                                logger.debug("turn_complete received with no tool calls - ending turn")
                                break
                            else:
                                logger.debug("turn_complete received but tool_call was seen - keeping socket alive")
                            
                except Exception as e:
                    logger.error(f"Error in response stream: {e}", exc_info=True)
                    yield {'type': 'error', 'message': str(e)}
                
                finally:
                    send_task.cancel()
                    try:
                        await send_task
                    except asyncio.CancelledError:
                        pass
                    
            except Exception as e:
                logger.error(f"Error in session block: {e}", exc_info=True)
                raise
                    
        except Exception as e:
            logger.error(f"Audio streaming failed: {e}", exc_info=True)
            yield {'type': 'error', 'message': f"Streaming failed: {str(e)}"}
        
        finally:
            # Ensure thinking loop is stopped on session end/error
            sound_manager = self.config.get('sound_manager')
            if sound_manager:
                try:
                    # sound_manager.stop_thinking_loop()
                    pass
                except Exception as e:
                    logger.debug(f"Error stopping thinking loop in cleanup: {e}")
            
            # Manually exit the connection context manager
            if hasattr(self, '_connect_context') and self._connect_context:
                try:
                    await self._connect_context.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting connection context: {e}")
                finally:
                    self._connect_context = None
            
            self.session = None
            self.is_connected = False
    
    async def send_turn_complete(self) -> None:
        """Manual turn completion signal for Gemini Live."""
        if self.session and self.is_connected:
            try:
                # Use input=None for a clean turn_complete without empty parts
                # This avoids 'invalid argument' (1007) errors on some Gemini versions
                await self.session.send(input=None, end_of_turn=True)
                logger.debug("Manual turn_complete signal sent to Gemini")
            except Exception as e:
                logger.warning(f"Failed to send manual turn_complete: {e}")

    def is_session_active(self) -> bool:
        """
        Check if the session is fully active and ready to accept commands.
        
        Returns:
            True if session exists and handshake is complete, False otherwise
        """
        return self.session is not None and self.is_connected
    
    async def force_close(self) -> None:
        """
        Force-close the current session immediately.
        Used when we need to break out of a blocking receive loop.
        """
        debug_print("DEBUG: GeminiLive force_close() called")
        self.is_connected = False
        self.is_streaming = False
        
        if self.session:
            try:
                # Try to close the session gracefully
                if hasattr(self.session, 'close'):
                    await self.session.close()
            except Exception as e:
                debug_print(f"DEBUG: Error closing session: {e}")
            finally:
                self.session = None
        
        # Also close the connection context if it exists
        if hasattr(self, '_connect_context') and self._connect_context:
            try:
                await self._connect_context.__aexit__(None, None, None)
            except Exception as e:
                debug_print(f"DEBUG: Error exiting connection context: {e}")
            finally:
                self._connect_context = None
    
    # ========== PERSISTENT SESSION MANAGEMENT ==========
    
    async def start_persistent_session(self, tool_schemas: List[Dict[str, Any]]) -> bool:
        """
        Start a persistent WebSocket session on application startup.
        This eliminates the 3-second cold start latency by keeping the connection open.
        
        Args:
            tool_schemas: List of available tool schemas
            
        Returns:
            True if session started successfully, False otherwise
        """
        debug_print("DEBUG: Starting persistent Gemini session...")
        
        try:
            from google.genai import types
            self.current_tools = tool_schemas
            
            # Convert tool schemas to function declarations
            function_declarations = []
            if tool_schemas:
                for schema in tool_schemas:
                    try:
                        func_decl = types.FunctionDeclaration(
                            name=schema['name'],
                            description=schema['description'],
                            parameters=schema['parameters']
                        )
                        function_declarations.append(func_decl)
                    except Exception as e:
                        logger.error(f"Failed to create function declaration for '{schema.get('name')}': {e}")
            
            # Build tools list
            self._tools_list = []
            try:
                self._tools_list.append(types.Tool(google_search=types.GoogleSearch()))
            except (TypeError, AttributeError) as e:
                logger.warning(f"GoogleSearch not available: {e}")
            if function_declarations:
                self._tools_list.append(types.Tool(function_declarations=function_declarations))
            
            # Stage 1: Build cache-optimized system instruction
            # ORDER MATTERS: Tools + Core Personality (stable) -> Memory (dynamic)
            base_instruction = self.config.get('system_instruction', '')
            memory_block = None
            memory_manager = self.config.get('memory_manager')
            if memory_manager:
                memory_block = memory_manager.get_memory_block()
            
            # Use cache-optimized prompt ordering for 90% cost reduction
            system_instruction = self._build_cache_optimized_instruction(
                base_instruction=base_instruction,
                tool_schemas=tool_schemas,
                memory_block=memory_block
            )
            logger.info("[ CACHE ] System instruction built with cache-optimized ordering.")

            # Create generation config
            generation_config = types.GenerationConfig(
                temperature=self.temperature,
                response_modalities=["AUDIO"]
            )
            
            # Create speech config
            voice_name = self.config.get('voice_name', 'Kore')
            speech_config = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            )
            
            # Store config for session refresh
            self._session_config = types.LiveConnectConfig(
                generation_config=generation_config,
                system_instruction=system_instruction,
                speech_config=speech_config,
                output_audio_transcription=types.AudioTranscriptionConfig(),
                input_audio_transcription=types.AudioTranscriptionConfig()
            )
            if self._tools_list:
                self._session_config.tools = self._tools_list
            
            # Establish the persistent connection
            connect_context = self.client.aio.live.connect(
                model=self.model_name,
                config=self._session_config
            )
            
            # Open the connection with timeout
            try:
                session = await asyncio.wait_for(connect_context.__aenter__(), timeout=30.0)
            except asyncio.TimeoutError:
                debug_print("DEBUG: Persistent session connection timeout")
                return False
            
            # Store for later use
            self._persistent_context = connect_context
            self._persistent_session = session
            self.session = session  # Also set the legacy session reference
            self.is_connected = True
            self._last_session_activity = time.time()
            
            # Start background tasks
            self._refresh_task = asyncio.create_task(self._refresh_session_loop())
            self._receive_task = asyncio.create_task(self._background_receive_loop()) # Added
            
            # ========== INITIAL HISTORY INJECTION ==========
            # Inject conversation history once at the start of the persistent session
            memory_manager = self.config.get('memory_manager')
            if memory_manager and memory_manager.has_history():
                try:
                    context_turns = self.config.get('context_turns', 20)
                    history_turns = memory_manager.get_history_turns(max_turns=context_turns)
                    if history_turns:
                        clean_history = reconstruct_history_safe(history_turns)
                        if clean_history and clean_history[0].role == "user":
                            debug_print(f"DEBUG: Injecting {len(clean_history)} turns of history into persistent session...")
                            client_content = types.LiveClientContent(turns=clean_history)
                            # FIX: Use send(input=...) instead of send_client_content (API mismatch)
                            await session.send(input=client_content)
                            debug_print("DEBUG: History injected successfully")
                except Exception as e:
                    debug_print(f"DEBUG: Failed to inject initial history: {e}")

            # Start the background refresh timer
            self._refresh_task = asyncio.create_task(self._refresh_session_loop())
            
            debug_print(f"DEBUG: Persistent session started successfully (voice: {voice_name})")
            logger.warning(f"[ GEMINI ] : Persistent connection established. Cold start eliminated.")
            return True
            
        except Exception as e:
            debug_print(f"DEBUG: Failed to start persistent session: {e}")
            logger.error(f"Failed to start persistent session: {e}", exc_info=True)
            return False
    
    def pause_streaming(self) -> None:
        """
        Pause audio streaming (Audio Gate = CLOSED).
        The WebSocket stays open, but no audio is sent.
        Called when VAD detects silence after a turn.
        """
        self.is_streaming = False
        debug_print("DEBUG: Audio streaming PAUSED (gate closed)")
    
    def resume_streaming(self) -> None:
        """
        Resume audio streaming (Audio Gate = OPEN).
        Called when wake word is detected.
        """
        self.is_streaming = True
        self._last_session_activity = time.time()
        debug_print("DEBUG: Audio streaming RESUMED (gate open)")
    
    async def _refresh_session_loop(self) -> None:
        """
        Background task that prevents session timeouts.
        1. Sends a silent 'heartbeat' every 15 seconds to prevent 1006 abnormal closure.
        2. Refreshes the entire session every 10 minutes to prevent server-side expiration.
        """
        try:
            from google.genai import types
            while True:
                await asyncio.sleep(15)  # Heartbeat interval
                
                if not self.is_connected or not self._persistent_session:
                    continue
                    
                # Skip heartbeat if actively streaming (real audio is being sent)
                if self.is_streaming:
                    self._last_session_activity = time.time()
                    continue
                
                # HEARTBEAT: Send a tiny silent audio chunk to keep the WebSocket alive
                # Code 1006 often occurs on 30s idle; 15s heartbeat prevents this.
                try:
                    # check elapsed again to be sure we are truly idle
                    if time.time() - self._last_session_activity < 15.0:
                        continue

                    # Silence chunk: 50ms of PCM 16kHz Mono
                    silence_data = b'\x00' * 1600 
                    silence_blob = types.Blob(
                         data=silence_data,
                         mime_type='audio/pcm;rate=16000'
                    )
                    # Use send_realtime_input with explicit Blob to prevent 1007 Invalid Argument
                    await self._persistent_session.send_realtime_input(audio=silence_blob)
                    logger.debug("Persistent session: Sent idle heartbeat.")
                except Exception as e:
                    logger.debug(f"Heartbeat failed (expected if socket closed): {e}")
                    self.is_connected = False
                
                # 10-MINUTE REFRESH: Full session reset
                elapsed = time.time() - self._last_session_activity
                if elapsed > self._session_refresh_interval:
                    debug_print(f"DEBUG: Session idle for {elapsed:.0f}s, refreshing...")
                    await self._refresh_persistent_session()
                    self._last_session_activity = time.time()
                    
        except asyncio.CancelledError:
            debug_print("DEBUG: Session refresh loop cancelled")
        except Exception as e:
            debug_print(f"DEBUG: Session refresh loop error: {e}")
    
    async def _refresh_persistent_session(self) -> None:
        """
        Refresh the persistent session by closing and reopening.
        Called by the background timer to prevent timeout.
        """
        debug_print("DEBUG: Refreshing persistent session...")
        
        try:
            # Re-fetch memory facts during refresh to pick up new info
            base_instruction = self.config.get('system_instruction', '')
            memory_block = None
            memory_manager = self.config.get('memory_manager')
            if memory_manager:
                memory_block = memory_manager.get_memory_block()
            
            # Use cache-optimized prompt ordering (consistent with initial session)
            system_instruction = self._build_cache_optimized_instruction(
                base_instruction=base_instruction,
                tool_schemas=self.current_tools,
                memory_block=memory_block
            )
            logger.info("[ CACHE ] Session refresh: instruction rebuilt with cache-optimized ordering.")

            # Update the configuration with potentially new instructions
            if self._session_config:
                self._session_config.system_instruction = system_instruction

            # Mark as disconnected during the refresh to prevent race conditions
            self.is_connected = False
            self.session = None
            old_context = self._persistent_context
            self._persistent_context = None
            self._persistent_session = None
            
            # Close old session
            if old_context:
                try:
                    await old_context.__aexit__(None, None, None)
                except Exception as e:
                    debug_print(f"DEBUG: Error closing old session: {e}")
            
            # Open new session with same config
            connect_context = self.client.aio.live.connect(
                model=self.model_name,
                config=self._session_config
            )
            
            session = await asyncio.wait_for(connect_context.__aenter__(), timeout=30.0)
            
            self._persistent_context = connect_context
            self._persistent_session = session
            self.session = session
            self.is_connected = True
            
            debug_print("DEBUG: Session refreshed successfully")
            logger.info("[ GEMINI ] : Session refreshed (Uplink Restored)")
            return True
            
        except Exception as e:
            debug_print(f"DEBUG: Session refresh failed: {e}")
            logger.error(f"Session refresh failed: {e}")
            self.is_connected = False
            return False
    
    async def close_persistent_session(self) -> None:
        """
        Close the persistent session (on app exit or explicit 'Goodbye').
        """
        debug_print("DEBUG: Closing persistent session...")
        
        # Cancel background tasks
        for task in [self._refresh_task, self._receive_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close session
        self.is_streaming = False
        self.is_connected = False
        
        if self._persistent_context:
            try:
                await self._persistent_context.__aexit__(None, None, None)
            except Exception as e:
                debug_print(f"DEBUG: Error closing persistent session: {e}")
            finally:
                self._persistent_context = None
                self._persistent_session = None
                self.session = None
        
        debug_print("DEBUG: Persistent session closed")
        logger.warning("[ GEMINI ] : Persistent session closed.")
    
    async def pause_receive_loop(self) -> None:
        """
        Pause the persistent WebSocket during Frontal Lobe (ReAct) processing.
        CRITICAL: We do NOT refresh or disconnect - we keep the SAME session alive.
        This preserves conversation context so the model remembers the tool call.
        """
        debug_print("DEBUG: Pausing WebSocket receive loop for agentic processing...")
        self._agentic_pause = True
        # DO NOT set is_connected = False - keep the session alive!
        # The background loop will just sleep while _agentic_pause is True
        logger.info("[ GEMINI ] : WebSocket paused for agentic processing (session preserved).")
    
    async def resume_receive_loop(self) -> None:
        """
        Resume the persistent WebSocket after agentic processing.
        CRITICAL: We do NOT refresh - the SAME session is still alive.
        This allows the model to receive the tool response in its original context.
        """
        debug_print("DEBUG: Resuming WebSocket receive loop after agentic processing...")
        
        # Simply clear the pause flag - the same session is still connected
        self._agentic_pause = False
        # is_connected should still be True (we never changed it)
        
        debug_print("DEBUG: WebSocket resume complete - same session, context preserved")
        logger.info("[ GEMINI ] : WebSocket resumed after agentic processing (context intact).")
    
    
    async def _background_receive_loop(self) -> None:
        """
        Runs 24/7 in the background for persistent sessions.
        Receives raw responses and puts them into the response queue.
        """
        debug_print("DEBUG: Persistent background receive loop STARTED")
        retry_delay = 1.0
        max_retry_delay = 60.0
        
        while True:
            try:
                # AGENTIC PAUSE: When Frontal Lobe is running, just sleep
                # Don't try to receive or reconnect - the session will be refreshed after ReAct completes
                if self._agentic_pause:
                    await asyncio.sleep(0.5)
                    continue
                
                # 1. Ensure we have a valid session
                if not self.is_connected or not self._persistent_session:
                    # If disconnected, attempt to reconnect
                    if not self.is_connected:
                        debug_print(f"DEBUG: Background loop attempting reconnection (delay: {retry_delay}s)...")
                        success = await self._refresh_persistent_session()
                        
                        if success:
                            debug_print("DEBUG: Background loop: Reconnection successful")
                            retry_delay = 1.0 # Reset backoff
                        else:
                            await asyncio.sleep(retry_delay)
                            retry_delay = min(retry_delay * 2, max_retry_delay)
                            continue
                    else:
                        # Session is None but marked as connected - wait for initialization
                        await asyncio.sleep(0.5)
                        continue

                # 2. Receive responses from the active session
                async for response in self._receive_responses(self._persistent_session):
                    # Check for fatal connection errors yielded by parser
                    if response.get('type') == 'error':
                        msg = response.get('message', '')
                        error_code = response.get('error_code')
                        
                        # Detect fatal WebSocket errors (1011, 1008, 1006, or explicit 'Connection closed')
                        is_fatal = (
                            'Connection closed' in msg or 
                            error_code in [1011, 1008, 1006] or
                            'unavailable' in msg.lower()
                        )
                        
                        if is_fatal:
                            debug_print(f"DEBUG: Background loop detected fatal error: {msg}")
                            self.is_connected = False
                            
                            # During agentic pause, errors are expected - don't propagate to queue
                            if self._agentic_pause:
                                debug_print("DEBUG: Error during agentic pause - suppressing (expected behavior)")
                                break
                            
                            await self._response_queue.put(response)
                            break # Break async for to trigger reconnection
                            
                    await self._response_queue.put(response)
                    retry_delay = 1.0 # Reset delay on any successful message
                    
            except asyncio.CancelledError:
                debug_print("DEBUG: Background receive loop CANCELLED")
                break
            except Exception as e:
                debug_print(f"DEBUG: Background receive loop ERROR: {e}")
                self.is_connected = False
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            
    async def _stream_with_persistent_queue(
        self,
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Consumes from the background response queue for the current turn.
        This provides the link between SessionManager and the persistent background connection.
        """
        try:
            self.current_tools = tool_schemas
            
            # CLEAR THE QUEUE: Remove any 'zombie' responses or errors 
            # from previous background reconnection attempts to ensure a clean turn.
            while not self._response_queue.empty():
                try:
                    self._response_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Log thinking at turn start (Removed to fix Ghost Thinking - SessionManager handles this)
            # log_thinking(logger=logger)
            
            # DIAGNOSTIC: Verify audio streaming state
            logger.info(f"[ AUDIO TX ] : Starting audio send task. is_streaming={self.is_streaming}")
            
            # Start sending audio (gate is controlled via self.is_streaming)
            send_task = asyncio.create_task(
                self._send_audio(self._persistent_session, audio_input)
            )
            logger.debug(f"[ AUDIO TX ] : Send task created: {send_task}")
            
            try:
                tool_call_seen = False
                audio_seen_after_tool = False  # Track if AI has spoken the tool result
                
                # Consume from the background queue until the turn is over
                while True:
                    # Check if the gate was closed externally (Neural Hang / Stop command)
                    # We check this periodically and before waiting for a response
                    if not self.is_streaming:
                        debug_print("DEBUG: Audio gate CLOSED, ending turn consumption")
                        break
                        
                    try:
                        # Wait for a response with a timeout to allow checking is_streaming
                        response = await asyncio.wait_for(self._response_queue.get(), timeout=0.5)
                        response_type = response.get('type')
                        
                        # ALWAYS yield the response to SessionManager
                        yield response
                        
                        # Set flag for ANY tool activity
                        if response_type in ['tool_call', 'tool_call_batch']:
                            tool_call_seen = True
                            audio_seen_after_tool = False  # Reset - waiting for audio from tool result
                        
                        # Track if audio comes after a tool call (AI is speaking the result)
                        if response_type == 'audio' and tool_call_seen:
                            audio_seen_after_tool = True
                            
                        if response_type == 'turn_complete':
                            # PHYSICS-FIRST: Set the generation complete flag
                            self._generation_complete = True
                            
                            # CRITICAL: Drain any remaining events from queue before breaking
                            # This ensures usage events that arrive just after turn_complete are not lost
                            while True:
                                try:
                                    remaining = self._response_queue.get_nowait()
                                    yield remaining
                                except asyncio.QueueEmpty:
                                    break
                            
                            # If we saw a tool call BUT also saw audio after it, 
                            # the AI has already spoken the result - this is the final turn_complete
                            if tool_call_seen and audio_seen_after_tool:
                                logger.debug("turn_complete after tool audio - ending turn")
                                break
                            elif tool_call_seen and not audio_seen_after_tool:
                                # Tool called but no audio yet - wait for AI to speak the result
                                logger.debug("turn_complete with tools pending audio - continuing")
                                # Don't reset tool_call_seen - still waiting for audio
                            else:
                                # Normal turn complete (no tools) - exit loop
                                logger.debug("turn_complete with no tools - ending turn")
                                break
                                
                    except asyncio.TimeoutError:
                        # Timeout is fine, just loop and check if is_streaming is still True
                        continue
                        
            except Exception as e:
                logger.error(f"Error in persistent queue stream: {e}")
                yield {'type': 'error', 'message': str(e)}
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                    
        except Exception as e:
            logger.error(f"Critical error in persistent turn: {e}")
            yield {'type': 'error', 'message': str(e)}
    
    async def _stream_with_session(
        self,
        session,
        audio_input: AsyncGenerator[bytes, None],
        tool_schemas: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream audio using an existing session (persistent mode).
        
        Args:
            session: The existing WebSocket session
            audio_input: Async generator yielding audio chunks
            tool_schemas: List of available tool schemas
            
        Yields:
            Response dictionaries
        """
        try:
            self.current_tools = tool_schemas
            
            # Create task for sending audio (runs in background)
            send_task = asyncio.create_task(
                self._send_audio(session, audio_input)
            )
            
            # Process responses as they come in
            try:
                tool_call_seen = False
                
                async for response in self._receive_responses(session):
                    response_type = response.get('type')
                    
                    yield response
                    
                    # Track if we've seen a tool call
                    if response_type in ['tool_call', 'tool_call_batch']:
                        tool_call_seen = True
                    
                    # Only break on turn_complete if turn is actually done
                    if response_type == 'turn_complete':
                        # PHYSICS-FIRST: Set the generation complete flag
                        self._generation_complete = True
                        
                        if tool_call_seen:
                            # Tool run pending - keep socket open
                            logger.debug("turn_complete with tools - resetting flag and continuing")
                            tool_call_seen = False
                        else:
                            # Normal turn complete - exit loop (followup handled by main_gui)
                            logger.debug("turn_complete with no tools - ending turn")
                            break
                        
            except Exception as e:
                logger.error(f"Error in response stream: {e}", exc_info=True)
                yield {'type': 'error', 'message': str(e)}
            
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                
        except Exception as e:
            logger.error(f"Error in persistent stream: {e}", exc_info=True)
            yield {'type': 'error', 'message': str(e)}
    
    async def _send_audio(
        self,
        session,
        audio_input: AsyncGenerator[bytes, None]
    ) -> None:
        """
        Send audio chunks to Gemini.
        
        Args:
            session: Live API session
            audio_input: Audio chunk generator
        """
        logger.info("[ AUDIO TX ] : _send_audio task STARTED")
        try:
            chunks_sent = 0
            async for audio_chunk in audio_input:
                chunks_sent += 1
                # AUDIO GATE: Only send audio if streaming is enabled
                if not self.is_streaming:
                    continue  # Gate closed - skip but don't close socket
                    
                if audio_chunk:
                    try:
                        # Stop thinking loop if it's active - user is speaking
                        sound_manager = self.config.get('sound_manager')
                        if sound_manager:
                            try:
                                # sound_manager.stop_thinking_loop()
                                pass
                            except Exception as e:
                                logger.warning(f"Failed to stop thinking loop: {e}")
                        
                        audio_blob = types.Blob(
                            data=audio_chunk,
                            mime_type='audio/pcm;rate=16000'
                        )
                        await session.send_realtime_input(audio=audio_blob)
                        chunks_sent += 1
                        
                        # DIAGNOSTIC: Log every 50 chunks to verify transmission
                        if chunks_sent % 50 == 0:
                            logger.debug(f"[ AUDIO TX ] : {chunks_sent} chunks sent to Gemini ({len(audio_chunk)} bytes each)")
                        
                    except Exception as e:
                        logger.error(f"Error sending audio chunk: {e}", exc_info=True)
            
            logger.info(f"[ AUDIO TX ] : Audio stream ended. Total chunks sent: {chunks_sent}")
            
            # User input has ended - start thinking loop
            sound_manager = self.config.get('sound_manager')
            if sound_manager:
                try:
                    # sound_manager.start_thinking_loop()
                    pass
                    # logger.debug("Started thinking loop (user input ended)")
                except Exception as e:
                    logger.warning(f"Failed to start thinking loop: {e}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in audio sending loop: {e}", exc_info=True)
    
    async def _receive_responses(self, session) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Receive and process responses from Gemini.
        
        Uses ResponseParser for centralized parsing with deduplication.
        Maintains session-level state for transcript accumulation and question detection.
        
        Args:
            session: Live API session
            
        Yields:
            Response dictionaries
        """
        thinking_shown = False
        received_audio = False
        received_text = None
        first_audio_in_turn = True
        full_transcript = ""
        
        # Initialize ResponseParser for this session
        parser = ResponseParser()
        
        try:
            # log_thinking(logger=logger)  # MOVED to stream_audio to fix 'Ghost Thinking'
            # thinking_shown = True
            
            try:
                async for response in session.receive():
                    # Update activity timestamp prevents heartbeats during playback
                    self._last_session_activity = time.time()
                    
                    # Stop thinking loop as soon as first server_content arrives
                    sound_manager = self.config.get('sound_manager')
                    if sound_manager:
                        # Only stop if it's NOT a tool call, thinking, or reasoning
                        is_thinking = False
                        if hasattr(response, 'server_content') and response.server_content:
                            if hasattr(response.server_content, 'model_turn') and response.server_content.model_turn:
                                for part in response.server_content.model_turn.parts:
                                    if part.thought or (part.call and part.call.function_calls):
                                        is_thinking = True
                                        break
                        
                        if not is_thinking:
                             pass
                    
                    # Parse response into events using ResponseParser
                    events = parser.parse(response)
                    
                    # Group tool calls into a single batch event if multiple exist in one message
                    tool_calls = [e for e in events if e.get('type') == 'tool_call']
                    other_events = [e for e in events if e.get('type') != 'tool_call']
                    
                    if len(tool_calls) > 1:
                        self._is_processing_tool = True
                        yield {
                            'type': 'tool_call_batch',
                            'calls': tool_calls
                        }
                        thinking_shown = False
                    elif len(tool_calls) == 1:
                        self._is_processing_tool = True
                        yield tool_calls[0]
                        thinking_shown = False
                    
                    for event in other_events:
                        event_type = event.get('type')
                        
                        # Handle tool cancellations (already in other_events)
                        if event_type == 'tool_call_cancellation':
                            logger.warning(f"Tool cancellation: {event.get('cancelled_ids')}")
                            yield event
                        
                        # Handle audio
                        elif event_type == 'audio':
                            received_audio = True
                            is_first_audio = first_audio_in_turn
                            if first_audio_in_turn:
                                first_audio_in_turn = False
                            
                            if thinking_shown:
                                thinking_shown = False
                            
                            yield {
                                'type': 'audio',
                                'data': event['data'],
                                '_mute_input': is_first_audio
                            }
                        
                        # Handle transcripts
                        elif event_type == 'transcript':
                            text_content = event.get('text', '')
                            source = event.get('source', 'model')
                            
                            if source == 'model':
                                # Store for text-only fallback
                                if received_text is None:
                                    received_text = ""
                                received_text += text_content + " "
                                # Accumulate for question detection
                                full_transcript += text_content + " "
                            
                            yield event
                        
                        # Handle thinking
                        elif event_type == 'thinking':
                            # GATE: Only log thinking if we are in an active turn (Audio Gate Open)
                            if self.is_streaming:
                                log_thinking(event.get('text', ''), logger=logger)
                            else:
                                debug_print(f"DEBUG: Gated thinking log (is_streaming={self.is_streaming})")
                            yield event
                            thinking_shown = False
                        
                        # Handle reasoning
                        elif event_type == 'reasoning':
                            # GATE: Only log reasoning if we are in an active turn
                            if self.is_streaming:
                                log_reasoning(event.get('text', ''), logger=logger)
                            yield event
                            thinking_shown = False
                        
                        # Handle usage metadata (token counts)
                        elif event_type == 'usage':
                            yield event
                        
                        # Handle grounding metadata
                        elif event_type == 'grounding_metadata':
                            yield event
                        
                        # Handle turn complete
                        elif event_type == 'turn_complete':
                            # Accumulate any text from turn_complete
                            text_parts = event.get('text_parts', [])
                            for text in text_parts:
                                full_transcript += text + " "
                            
                            # Text-only fallback
                            if not received_audio and received_text:
                                text_content = received_text.strip()
                                if text_content:
                                    logger.info(f"[TEXT-ONLY RESPONSE] {text_content[:200]}")
                            
                            # Log transcript
                            clean_text = full_transcript.strip()
                            if clean_text:
                                logger.info(f"[ASSISTANT RESPONSE]: {clean_text}")
                            else:
                                if received_text:
                                    clean_text = received_text.strip()
                                    logger.debug(f"[ASSISTANT] (from received_text): {clean_text}")
                                else:
                                    logger.warning("No transcript captured!")
                            
                            # Question detection for follow-up
                            expecting_followup = self._detect_question(clean_text)
                            self.expecting_followup = expecting_followup
                            
                            # Stop thinking sound loop if it's active
                            sound_manager = self.config.get('sound_manager')
                            if sound_manager:
                                pass
                            
                            if expecting_followup:
                                logger.info(f"Question detected -> Follow-Up Triggered")
                            
                            # Reset for next turn
                            full_transcript = ""
                            thinking_shown = False
                            first_audio_in_turn = True
                            
                            yield {
                                'type': 'turn_complete',
                                '_unmute_input': True,
                                'has_tool': event.get('has_tool', False),
                                'expecting_followup': self.expecting_followup
                            }
                
                # Reset tool processing flag at end of response loop
                self._is_processing_tool = False
            
            except asyncio.CancelledError:
                logger.debug("Response reception cancelled")
                pass
            except Exception as e:
                # Handle connection errors
                error_type = type(e).__name__
                error_message = str(e)
                
                is_connection_error = False
                error_code = None
                
                if HAS_WEBSOCKETS and isinstance(e, websockets.exceptions.ConnectionClosedError):
                    is_connection_error = True
                    error_code = e.code if hasattr(e, 'code') else None
                elif 'ConnectionClosedError' in error_type or 'ConnectionClosed' in error_type:
                    is_connection_error = True
                    # Attempt to extract error code from message if present
                    match = re.search(r'Code: (\d+)', error_message)
                    if match:
                        error_code = int(match.group(1))
                    elif '1007' in error_message:
                        error_code = 1007
                
                if is_connection_error:
                    # Map common fatal codes for higher-level detection
                    if not error_code:
                        if '1008' in error_message: error_code = 1008
                        elif '1006' in error_message: error_code = 1006
                        elif '1011' in error_message: error_code = 1011
                        
                    logger.error(f"WebSocket connection closed: {error_message} (Code: {error_code})")
                    yield {
                        'type': 'error',
                        'message': f"Connection closed: {error_message}",
                        'error_code': error_code
                    }
                else:
                    logger.error(f"Error receiving responses: {e}", exc_info=True)
                    yield {'type': 'error', 'message': f"{error_type}: {error_message}"}
        finally:
            try:
                if not received_audio and received_text:
                    text_content = received_text.strip()
                    if text_content:
                        logger.info(f"[TEXT-ONLY RESPONSE] Exiting loop: {text_content[:200]}")
                
                logger.debug("Response reception loop exiting")
            except Exception as cleanup_error:
                logger.warning(f"Error in response reception cleanup: {cleanup_error}", exc_info=True)
    
    def _detect_question(self, text: str) -> bool:
        """
        Detect if the model's response is a question expecting follow-up.
        
        Args:
            text: The model's response text
            
        Returns:
            True if a question was detected
        """
        if not text:
            return False
        
        clean_lower = text.lower()
        
        # Check for question mark at end (strongest signal)
        if text.rstrip().endswith("?"):
            return True
        
        # Direct clarifying questions
        direct_question_phrases = [
            "which one",
            "do you mean",
            "did you mean",
            "what would you like",
            "what should i",
            "can you clarify",
            "could you specify",
            "what exactly",
            "which video",
            "which file",
            "which website",
            "which app",
            "the first or",
            "the second or",
        ]
        for phrase in direct_question_phrases:
            if phrase in clean_lower:
                return True
        
        # General clarifying phrases
        clarifying_phrases = [
            "would you like",
            "should i",
            "do you want",
            "would you prefer",
            "your thoughts",
        ]
        for phrase in clarifying_phrases:
            if phrase in clean_lower:
                return True
        
        return False
    
    def _sanitize_schema(self, schema: Any) -> Any:
        """
        Recursively remove 'additionalProperties' and 'additional_properties' from schema.
        Gemini API rejects these fields.
        """
        if isinstance(schema, dict):
            # Create a copy to avoid modifying original
            new_schema = {}
            for k, v in schema.items():
                if k in ['additionalProperties', 'additional_properties']:
                    continue
                new_schema[k] = self._sanitize_schema(v)
            return new_schema
        elif isinstance(schema, list):
            return [self._sanitize_schema(item) for item in schema]
        return schema

    def convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        """Unified interface for tool conversion."""
        return self._convert_tool_schemas(schemas)

    def _convert_tool_schemas(self, schemas: List[Dict[str, Any]]) -> List[Any]:
        """
        Convert our tool schemas to Gemini format.
        
        Args:
            schemas: Our tool schemas
            
        Returns:
            Gemini-formatted tools
        """
        tools = []
        for schema in schemas:
            try:
                # Sanitize parameters schema
                params = self._sanitize_schema(schema.get('parameters', {}))
                
                function_declaration = types.FunctionDeclaration(
                    name=schema['name'],
                    description=schema['description'],
                    parameters=params
                )
                tools.append(types.Tool(function_declarations=[function_declaration]))
            except Exception as e:
                logger.error(f"Failed to convert tool schema '{schema.get('name')}': {e}")
        
        return tools
    
    async def send_tool_response(
        self, 
        tool_call_id: str, 
        result: Dict[str, Any], 
        tool_name: Optional[str] = None
    ) -> None:
        """
        Send a single tool execution result back to Gemini.
        Maintained for backward compatibility.
        """
        await self.send_tool_batch_response([{
            'id': tool_call_id,
            'name': tool_name,
            'result': result
        }])

    async def send_tool_batch_response(self, responses: List[Dict[str, Any]]) -> None:
        """
        Send a batch of tool execution results back to Gemini in a single message.
        
        Args:
            responses: List of dicts, each containing 'id', 'name', and 'result'
        """
        if not self.session:
            logger.warning("No active session to send tool responses")
            return
        
        try:
            function_responses = []
            for resp in responses:
                tool_call_id = resp.get('id')
                tool_name = resp.get('name')
                result = resp.get('result')
                
                # Unwrap dict results to provide direct spoken content to the model
                # This ensures the tool execution summary is vocalized clearly
                final_result = result
                if isinstance(result, dict) and 'result' in result:
                    final_result = result['result']
                
                function_responses.append(types.FunctionResponse(
                    id=tool_call_id,
                    name=tool_name,
                    response={'result': final_result}
                ))
            
            # Use the correct method: session.send_tool_response() (takes a list)
            try:
                await self.session.send_tool_response(function_responses=function_responses)
                logger.debug(f"Sent batch of {len(function_responses)} tool responses")
                return
            except AttributeError:
                logger.warning("session.send_tool_response() not available, trying alternatives")
            except Exception as e:
                logger.error(f"session.send_tool_response() failed: {e}", exc_info=True)
            
            # Fallback: Try sending through realtime_input (client_content)
            # Some SDK versions might require this wrapper
            error_details = []
            try:
                parts = [types.Part(function_response=fr) for fr in function_responses]
                client_content = types.LiveClientContent(parts=parts)
                # Note: realtime_input expects a wrapper or direct call depending on version
                # In most recent versions of Live API, send_tool_response is the correct method.
                # If that failed, we try the most likely alternative.
                await self.session.send_realtime_input(client_content=client_content)
                return
            except Exception as e1:
                error_details.append(f"Fallback (realtime_input): {str(e1)}")
            
            logger.warning(f"Could not send tool batch response. Errors: {error_details}")
            
        except Exception as e:
            logger.error(f"Failed to send tool batch response: {e}", exc_info=True)
    
    async def send_image(self, image_bytes: bytes) -> None:
        """
        Send an image to Gemini as multimodal input.
        
        Args:
            image_bytes: JPEG image bytes to send
        """
        if not self.session:
            logger.warning("No active session to send image")
            return
        
        try:
            image_blob = types.Blob(
                data=image_bytes,
                mime_type='image/jpeg'
            )
            image_part = types.Part(inline_data=image_blob)
            client_content = types.LiveClientContent(parts=[image_part])
            await self.session.send_realtime_input(client_content=client_content)
            
        except Exception as e:
            logger.error(f"Failed to send image: {e}", exc_info=True)
            raise
    
    async def execute_text(
        self,
        prompt: str,
        tool_schemas: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Execute a text-only interaction (not used for audio assistant).
        Moves network call to worker thread to prevent loop blocking.
        """
        try:
            if tool_schemas:
                tools = self._convert_tool_schemas(tool_schemas)
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=self.temperature,
                        tools=tools
                    )
                )
            else:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=self.temperature)
                )
            
            if response.text:
                return {
                    'type': 'text',
                    'content': response.text
                }
            
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'function_calls'):
                    return {
                        'type': 'tool_call',
                        'calls': candidate.function_calls
                    }
            
            return {
                'type': 'text',
                'content': str(response)
            }
            
        except Exception as e:
            logger.error(f"Text execution failed: {e}", exc_info=True)
            return {
                'type': 'error',
                'message': str(e)
            }
    
    async def interrupt(self) -> None:
        """
        Interrupt the model's current output.
        Sends a signal to stop generating.
        """
        logger.info("[ GEMINI ] : Interruption requested.")
        self.is_streaming = False
        self._generation_complete = True
        
        # Drain the response queue to prevent zombie events in the next turn
        while not self._response_queue.empty():
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        if not self.session:
            return
        
        try:
            # We don't necessarily need to send a message to the server, 
            # but clearing the queue ensures we don't process any more stale data.
            logger.debug("[ GEMINI ] : Interruption cleanup complete.")
        except Exception as e:
            logger.error(f"Failed to interrupt: {e}")
    
    async def cleanup(self) -> None:
        """Clean up resources."""
        if self.session:
            pass
        
        self.session = None

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> 'GenerateResponse':
        """
        Unified generate method for ReAct orchestration.
        Maps the text-based ReAct turn to the Gemini models.generate_content API.
        """
        from src.core.intelligence.adapters.ai_adapter_base import GenerateResponse
        from google.genai import types
        
        # Extract last user message as prompt (execute_text fallback pattern)
        prompt = ""
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                prompt = msg.get('content', '')
                break
        
        try:
            # Convert tools to Gemini format
            gemini_tools = self._convert_tool_schemas(tools) if tools else None
            
            # Execute text interaction
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    tools=gemini_tools
                )
            )
            
            # Parse response
            text = response.text
            tool_calls = []
            
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'function_calls'):
                    for fc in candidate.function_calls:
                        tool_calls.append({
                            'id': getattr(fc, 'id', f"call_{int(time.time())}"),
                            'name': fc.name,
                            'args': fc.args
                        })
            
            return GenerateResponse(
                text=text,
                tool_calls=tool_calls if tool_calls else None,
                finish_reason="stop" if not tool_calls else "tool_calls",
                raw=response
            )
            
        except Exception as e:
            logger.error(f"[ GEMINI_LIVE ] Generate failed: {e}", exc_info=True)
            return GenerateResponse(
                text=None,
                tool_calls=None,
                finish_reason="error",
                raw={'error': str(e)}
            )

