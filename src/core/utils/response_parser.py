"""
Response Parser - Logic layer for parsing Gemini Live API responses.

This module provides centralized parsing of raw Gemini Live responses
into typed events that the network layer can yield to consumers.

Extracted from models/gemini_live.py as part of the 3-layer refactor:
- Network Layer: models/gemini_live.py (WebSocket management)
- Logic Layer: core/response_parser.py (response parsing)
- Data Layer: core/history_utils.py (history conversion)

Features:
- Tool deduplication via ResponseHarvester
- Deep inspection of all response locations
- Typed event output for clean handling
"""

from typing import Dict, Any, Optional, List

from src.shared.logger import setup_logger
from src.core.utils.response_harvester import ResponseHarvester

logger = setup_logger("ResponseParser")


class ResponseParser:
    """
    Stateful parser for Gemini Live response stream.
    
    Extracts all event types from responses:
    - Audio bytes
    - Tool calls (with deduplication)
    - Transcripts (model and user)
    - Thinking/reasoning content
    - Grounding metadata
    - Turn complete signals
    
    Should be instantiated once per session and reset between sessions.
    """
    
    def __init__(self):
        """Initialize the parser with empty state."""
        self._harvester = ResponseHarvester()
    
    def reset(self) -> None:
        """Reset state for a new session."""
        self._harvester.reset()
        logger.debug("ResponseParser state reset")
    
    def parse(self, response) -> List[Dict[str, Any]]:
        """
        Parse a single response and return list of events.
        
        Checks all possible locations in the response structure and
        returns typed event dictionaries that the caller can process.
        
        Args:
            response: Raw LiveServerMessage from the Gemini socket
            
        Returns:
            List of event dictionaries. Each has 'type' key and relevant data.
            Possible types: 'tool_call', 'tool_call_cancellation', 'audio',
            'transcript', 'thinking', 'reasoning', 'grounding_metadata', 'turn_complete'
        """
        events: List[Dict[str, Any]] = []
        
        # STEP 1: Tool extraction (via ResponseHarvester for deduplication)
        tool_calls = self._harvester.process_response(response)
        if tool_calls:
            events.extend(tool_calls)
        
        # STEP 2: Audio extraction
        audio_event = self._extract_audio(response)
        if audio_event:
            events.append(audio_event)
        
        # STEP 3: Grounding metadata extraction
        grounding_event = self._extract_grounding_metadata(response)
        if grounding_event:
            events.append(grounding_event)
        
        # STEP 4: Content extraction (transcripts, thinking, reasoning)
        content_events = self._extract_content(response)
        events.extend(content_events)
        
        # STEP 5: User input transcription (from client_content)
        user_transcript_events = self._extract_user_transcripts(response)
        events.extend(user_transcript_events)
        
        # STEP 6: Turn complete detection
        turn_complete_event = self._extract_turn_complete(response)
        if turn_complete_event:
            events.append(turn_complete_event)
        
        # STEP 7: Response-level thinking/reasoning/text (fallback locations)
        fallback_events = self._extract_response_level_content(response)
        events.extend(fallback_events)
        
        # STEP 8: Usage metadata extraction
        usage_event = self._extract_usage_metadata(response)
        if usage_event:
            events.append(usage_event)
        
        return events
    
    def _extract_usage_metadata(self, response) -> Optional[Dict[str, Any]]:
        """Extract usage metadata (token counts) from response."""
        usage = None
        
        # Location 1: Top-level usage_metadata
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage = response.usage_metadata
            
        # Location 2: server_content.model_turn.usage_metadata
        elif hasattr(response, 'server_content') and response.server_content:
            server_content = response.server_content
            if hasattr(server_content, 'model_turn') and server_content.model_turn:
                if hasattr(server_content.model_turn, 'usage_metadata'):
                    usage = server_content.model_turn.usage_metadata

        if usage:
            # Extract and convert to int, handling None values
            prompt_tokens = int(getattr(usage, 'prompt_token_count', 0) or 0)
            response_tokens = int(getattr(usage, 'candidates_token_count', 0) or 0)
            total_tokens = int(getattr(usage, 'total_token_count', 0) or 0)
            cached_tokens = int(getattr(usage, 'cached_content_token_count', 0) or 0)
            
            # CRITICAL FIX: Only emit usage event if we have actual token counts
            # Live API may report total_tokens even when response_tokens=0 during streaming
            # We accept either response_tokens > 0 OR total_tokens > prompt_tokens
            if total_tokens > 0:
                return {
                    'type': 'usage',
                    'prompt_tokens': prompt_tokens,
                    'response_tokens': response_tokens,
                    'total_tokens': total_tokens,
                    'cached_tokens': cached_tokens
                }
        return None
    
    def _extract_audio(self, response) -> Optional[Dict[str, Any]]:
        """
        Extract audio bytes from a Gemini response.
        
        Checks all possible locations in priority order:
        1. response.data (direct audio)
        2. server_content.model_turn.parts[].inline_data.data (PRIMARY PATH)
        3. server_content.model_turn.data (fallback)
        4. server_content.data (last resort)
        5. Nested attribute search (emergency fallback)
        
        Args:
            response: Raw LiveServerMessage from the socket
            
        Returns:
            Audio event dict if found, None otherwise
        """
        audio_bytes = None
        
        # Location 1: response.data (direct audio)
        if hasattr(response, 'data') and response.data is not None:
            audio_bytes = response.data
        
        # Location 2: server_content.model_turn.parts[].inline_data.data (PRIMARY PATH)
        if audio_bytes is None and hasattr(response, 'server_content') and response.server_content is not None:
            server_content = response.server_content
            
            # PRIORITY 1: Check server_content.model_turn.parts[].inline_data.data FIRST
            # This is where Gemini Live outputs 24kHz PCM audio
            if hasattr(server_content, 'model_turn') and server_content.model_turn is not None:
                model_turn = server_content.model_turn
                if hasattr(model_turn, 'parts') and model_turn.parts:
                    for part in model_turn.parts:
                        if hasattr(part, 'inline_data') and part.inline_data is not None:
                            inline_data = part.inline_data
                            if hasattr(inline_data, 'mime_type') and inline_data.mime_type == "audio/pcm":
                                if hasattr(inline_data, 'data') and inline_data.data is not None:
                                    audio_bytes = inline_data.data
                                    break
            
            # PRIORITY 2: Check server_content.model_turn.data (fallback)
            if audio_bytes is None and hasattr(server_content, 'model_turn') and server_content.model_turn is not None:
                model_turn = server_content.model_turn
                if hasattr(model_turn, 'data') and model_turn.data is not None:
                    audio_bytes = model_turn.data
            
            # PRIORITY 3: Check server_content.data (last resort)
            if audio_bytes is None and hasattr(server_content, 'data') and server_content.data is not None:
                audio_bytes = server_content.data
        
        # Emergency fallback: Check ALL attributes for bytes data
        if audio_bytes is None:
            attrs = [attr for attr in dir(response) if not attr.startswith('_') and not callable(getattr(response, attr, None))]
            for attr in attrs:
                try:
                    value = getattr(response, attr)
                    if value is not None:
                        # Check if it's bytes directly
                        if isinstance(value, bytes):
                            audio_bytes = value
                            break
                        
                        # Check if it has a 'data' attribute
                        if hasattr(value, 'data'):
                            nested_data = getattr(value, 'data')
                            if nested_data is not None and isinstance(nested_data, bytes):
                                audio_bytes = nested_data
                                break
                except Exception:
                    pass
        
        if audio_bytes:
            return {
                'type': 'audio',
                'data': audio_bytes,
                '_mute_input': True # Always mute input during AI playback to prevent echo
            }
        
        return None
    
    def _extract_grounding_metadata(self, response) -> Optional[Dict[str, Any]]:
        """
        Extract grounding metadata (Google Search sources) from response.
        
        Args:
            response: Raw LiveServerMessage
            
        Returns:
            Grounding metadata event dict if found, None otherwise
        """
        grounding_metadata = None
        sources = []
        
        # Check multiple possible locations for grounding_metadata
        if hasattr(response, 'grounding_metadata') and response.grounding_metadata is not None:
            grounding_metadata = response.grounding_metadata
        elif hasattr(response, 'server_content') and response.server_content is not None:
            server_content = response.server_content
            if hasattr(server_content, 'model_turn') and server_content.model_turn is not None:
                model_turn = server_content.model_turn
                if hasattr(model_turn, 'grounding_metadata') and model_turn.grounding_metadata is not None:
                    grounding_metadata = model_turn.grounding_metadata
        
        # Extract sources from grounding_metadata
        if grounding_metadata:
            try:
                # Check for grounding_chunks (typical structure)
                if hasattr(grounding_metadata, 'grounding_chunks') and grounding_metadata.grounding_chunks:
                    for chunk in grounding_metadata.grounding_chunks:
                        source = {}
                        if hasattr(chunk, 'web') and chunk.web is not None:
                            web = chunk.web
                            if hasattr(web, 'uri'):
                                source['url'] = web.uri
                            if hasattr(web, 'title'):
                                source['title'] = web.title
                        elif hasattr(chunk, 'uri'):
                            source['url'] = chunk.uri
                        elif hasattr(chunk, 'url'):
                            source['url'] = chunk.url
                        if hasattr(chunk, 'title'):
                            source['title'] = chunk.title
                        if 'url' in source:  # Only add if we have a URL
                            sources.append(source)
                
                # Alternative: Check for web_search_grounding_metadata
                if not sources and hasattr(grounding_metadata, 'web_search_grounding_metadata'):
                    web_grounding = grounding_metadata.web_search_grounding_metadata
                    if hasattr(web_grounding, 'grounding_chunks') and web_grounding.grounding_chunks:
                        for chunk in web_grounding.grounding_chunks:
                            source = {}
                            if hasattr(chunk, 'web') and chunk.web is not None:
                                web = chunk.web
                                if hasattr(web, 'uri'):
                                    source['url'] = web.uri
                                if hasattr(web, 'title'):
                                    source['title'] = web.title
                            if 'url' in source:
                                sources.append(source)
                
                # If we found sources, return event
                if sources:
                    return {
                        'type': 'grounding_metadata',
                        'sources': sources
                    }
            except Exception as e:
                logger.warning(f"Error extracting grounding metadata: {e}", exc_info=True)
        
        return None
    
    def _extract_content(self, response) -> List[Dict[str, Any]]:
        """
        Extract transcripts, thinking, and reasoning from server_content.
        
        Args:
            response: Raw LiveServerMessage
            
        Returns:
            List of content event dicts
        """
        events = []
        
        if not hasattr(response, 'server_content') or response.server_content is None:
            return events
        
        server_content = response.server_content
        
        if not hasattr(server_content, 'model_turn') or server_content.model_turn is None:
            return events
        
        model_turn = server_content.model_turn
        
        if not hasattr(model_turn, 'parts') or not model_turn.parts:
            return events
        
        for part in model_turn.parts:
            # Skip function_call and audio parts (handled separately)
            if hasattr(part, 'function_call') and part.function_call is not None:
                continue
            if hasattr(part, 'inline_data') and part.inline_data is not None:
                continue
            
            # Text parts (transcription metadata from output_audio_transcription)
            if hasattr(part, 'text') and part.text:
                text_content = part.text
                is_partial = getattr(part, 'is_partial', False) if hasattr(part, 'is_partial') else False
                events.append({
                    'type': 'transcript',
                    'text': text_content,
                    'is_partial': is_partial,
                    'source': 'model'  # Model output transcription
                })
            
            # Thought/reasoning parts
            if hasattr(part, 'thought') or hasattr(part, 'reasoning'):
                has_thought = getattr(part, 'thought', None)
                has_reasoning = getattr(part, 'reasoning', None)
                
                # If thought/reasoning flag is True, extract text from part.text
                if (has_thought is True) or (has_reasoning is True):
                    reasoning_str = ""
                    if hasattr(part, 'text') and part.text:
                        reasoning_str = str(part.text)
                    elif isinstance(has_thought, str):
                        reasoning_str = str(has_thought)
                    elif isinstance(has_reasoning, str):
                        reasoning_str = str(has_reasoning)
                    
                    if reasoning_str:
                        events.append({
                            'type': 'reasoning',
                            'text': reasoning_str
                        })
            
            # Thinking tokens
            if hasattr(part, 'thinking') and part.thinking:
                thinking_text = ""
                if isinstance(part.thinking, bool) and part.thinking:
                    if hasattr(part, 'text') and part.text:
                        thinking_text = str(part.text)
                else:
                    thinking_text = str(part.thinking)
                
                if thinking_text:
                    events.append({
                        'type': 'thinking',
                        'text': thinking_text
                    })
        
        return events
    
    def _extract_user_transcripts(self, response) -> List[Dict[str, Any]]:
        """
        Extract user input transcription from client_content.
        
        Args:
            response: Raw LiveServerMessage
            
        Returns:
            List of transcript event dicts
        """
        events = []
        
        if not hasattr(response, 'client_content') or response.client_content is None:
            return events
        
        client_content = response.client_content
        
        if hasattr(client_content, 'parts') and client_content.parts:
            for part in client_content.parts:
                if hasattr(part, 'text') and part.text:
                    text_content = part.text
                    is_partial = getattr(part, 'is_partial', False) if hasattr(part, 'is_partial') else False
                    events.append({
                        'type': 'transcript',
                        'text': text_content,
                        'is_partial': is_partial,
                        'source': 'user'  # User input transcription
                    })
        
        return events
    
    def _extract_turn_complete(self, response) -> Optional[Dict[str, Any]]:
        """
        Check for turn_complete signal in response.
        
        Also extracts tool/thought indicators for the outer loop to know
        whether to continue waiting for more responses.
        
        Args:
            response: Raw LiveServerMessage
            
        Returns:
            Turn complete event dict if found, None otherwise
        """
        # Check BOTH locations for turn_complete:
        # 1. Top-level: response.turn_complete (older API versions)
        # 2. Nested: response.server_content.turn_complete (current API behavior)
        top_level_complete = hasattr(response, 'turn_complete') and response.turn_complete is not None
        
        nested_complete = False
        if hasattr(response, 'server_content') and response.server_content:
            nested_complete = getattr(response.server_content, 'turn_complete', False)
        
        if not top_level_complete and not nested_complete:
            return None
        
        # Check if there's a tool call or thought/reasoning in server_content
        has_tool_in_turn_complete = False
        is_processing_tool = False
        text_parts = []
        
        if hasattr(response, 'server_content') and response.server_content:
            server_content = response.server_content
            if hasattr(server_content, 'model_turn') and server_content.model_turn:
                model_turn = server_content.model_turn
                if hasattr(model_turn, 'parts') and model_turn.parts:
                    for part in model_turn.parts:
                        # Capture text for transcript
                        if hasattr(part, 'text') and part.text:
                            text_parts.append(part.text)
                        # Check for tool indicators
                        if hasattr(part, 'function_call') and part.function_call:
                            has_tool_in_turn_complete = True
                            is_processing_tool = True
                        elif hasattr(part, 'executable_code') and part.executable_code:
                            has_tool_in_turn_complete = True
                            is_processing_tool = True
                        # Check for thought/reasoning indicators
                        elif (hasattr(part, 'thought') and part.thought) or \
                             (hasattr(part, 'reasoning') and part.reasoning) or \
                             (hasattr(part, 'thinking') and part.thinking):
                            is_processing_tool = True
        
        return {
            'type': 'turn_complete',
            '_unmute_input': True,  # Signal to session_manager to unmute when turn completes
            'has_tool': has_tool_in_turn_complete or is_processing_tool,
            'text_parts': text_parts  # Text found in turn_complete for transcript accumulation
        }
    
    def _extract_response_level_content(self, response) -> List[Dict[str, Any]]:
        """
        Extract thinking, reasoning, and text from response-level attributes.
        
        These are fallback locations when content isn't in server_content.
        
        Args:
            response: Raw LiveServerMessage
            
        Returns:
            List of content event dicts
        """
        events = []
        
        # Check for thinking at response level
        if hasattr(response, 'thinking') and response.thinking is not None:
            thinking_text = ""
            if isinstance(response.thinking, bool) and response.thinking:
                if hasattr(response, 'text') and response.text:
                    thinking_text = str(response.text)
            else:
                thinking_text = str(response.thinking)
            
            if thinking_text:
                events.append({
                    'type': 'thinking',
                    'text': thinking_text
                })
        
        # Check for reasoning at response level
        if hasattr(response, 'reasoning') and response.reasoning is not None:
            reasoning_text = ""
            if isinstance(response.reasoning, bool) and response.reasoning:
                if hasattr(response, 'text') and response.text:
                    reasoning_text = str(response.text)
            else:
                reasoning_text = str(response.reasoning)
            
            if reasoning_text:
                events.append({
                    'type': 'reasoning',
                    'text': reasoning_text
                })
        
        # Check for text at response level (only if not already handled)
        # This is transcription metadata, not blocking content
        if hasattr(response, 'text') and response.text is not None:
            # Only add if we haven't already added thinking/reasoning with text
            if not (hasattr(response, 'thinking') and response.thinking) and \
               not (hasattr(response, 'reasoning') and response.reasoning):
                text_content = str(response.text)
                events.append({
                    'type': 'transcript',
                    'text': text_content,
                    'is_partial': False,
                    'source': 'model'
                })
        
        # Check for thought at response level
        if hasattr(response, 'thought') and response.thought is not None:
            thought_text = ""
            if isinstance(response.thought, bool) and response.thought:
                if hasattr(response, 'text') and response.text:
                    thought_text = str(response.text)
            else:
                thought_text = str(response.thought)
            
            if thought_text:
                events.append({
                    'type': 'reasoning',
                    'text': thought_text
                })
        
        return events

