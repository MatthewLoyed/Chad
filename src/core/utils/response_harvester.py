"""
Response Harvester - Centralized tool extraction from Gemini Live responses.

This module eliminates 'Ghost' and 'Double' tool calls by:
1. Checking all possible tool locations in priority order
2. Deduplicating via processed ID tracking
3. Providing a single point of truth for tool extraction
"""

import logging
from typing import Dict, Any, Optional, List
from src.shared.logger import setup_logger

logger = setup_logger("ResponseHarvester")
logger.setLevel(logging.WARNING)


class ResponseHarvester:
    """
    Stateful parser for Gemini Live response stream.
    
    Extracts tool calls from responses while preventing duplicates.
    Should be instantiated once per session and reset between sessions.
    """
    
    def __init__(self):
        """Initialize the harvester with empty state."""
        self._processed_tool_ids: set = set()
        self._tool_id_counter: int = 0  # Fallback for tools without IDs
    
    def reset(self) -> None:
        """Reset state for a new session."""
        self._processed_tool_ids.clear()
        self._tool_id_counter = 0
        logger.debug("ResponseHarvester state reset")
    
    def process_response(self, response) -> Optional[List[Dict[str, Any]]]:
        """
        Extract tool calls from a response if present and not already processed.
        
        Checks all possible locations in priority order:
        1. response.tool_call (Protocol Level - highest priority)
        2. response.server_content.model_turn.parts[].function_call (Content Level)
        3. response.tool_call_cancellation (Handle cancellations)
        
        Args:
            response: Raw LiveServerMessage from the Gemini socket
            
        Returns:
            List of tool call dicts if new tools found, None if no tools or all duplicates.
            Each dict has: {type, id, name, params}
        """
        tool_calls_found: List[Dict[str, Any]] = []
        
        # LOCATION 1: response.tool_call (Protocol Level - highest priority)
        tool_calls_found.extend(self._extract_from_tool_call(response))
        
        # LOCATION 2: response.server_content.model_turn.parts[].function_call
        tool_calls_found.extend(self._extract_from_server_content(response))
        
        # LOCATION 3: response.tool_call_cancellation
        cancellation = self._check_cancellation(response)
        if cancellation:
            # Return cancellation as a special tool call type
            return [cancellation]
        
        # Filter out duplicates
        unique_tools = self._deduplicate(tool_calls_found)
        
        if unique_tools:
            logger.info(f"[HARVESTER] Yielding {len(unique_tools)} tool call(s): {[t['name'] for t in unique_tools]}")
            return unique_tools
        
        return None
    
    def _extract_from_tool_call(self, response) -> List[Dict[str, Any]]:
        """Extract tool calls from response.tool_call location."""
        tools = []
        
        if not hasattr(response, 'tool_call') or response.tool_call is None:
            return tools
        
        tool_call_obj = response.tool_call
        logger.debug(f"[HARVESTER] Found tool_call at response.tool_call")
        
        # LiveServerToolCall has function_calls (plural) - a list
        if hasattr(tool_call_obj, 'function_calls') and tool_call_obj.function_calls:
            for fc in tool_call_obj.function_calls:
                tool_data = self._extract_function_call_data(fc, 'response.tool_call.function_calls')
                if tool_data:
                    tools.append(tool_data)
        elif hasattr(tool_call_obj, 'name'):
            # Single function call format
            tools.append({
                'type': 'tool_call',
                'name': tool_call_obj.name,
                'id': getattr(tool_call_obj, 'id', None),
                'params': getattr(tool_call_obj, 'args', {}) or {},
                '_source': 'response.tool_call'
            })
        
        return tools
    
    def _extract_from_server_content(self, response) -> List[Dict[str, Any]]:
        """Extract tool calls from response.server_content.model_turn.parts."""
        tools = []
        
        if not hasattr(response, 'server_content') or response.server_content is None:
            return tools
        
        sc = response.server_content
        
        # Check model_turn.parts for function_call
        # CRITICAL: Iterate through ALL parts - do NOT stop at text parts
        if hasattr(sc, 'model_turn') and sc.model_turn is not None:
            mt = sc.model_turn
            if hasattr(mt, 'parts') and mt.parts:
                logger.debug(f"[HARVESTER] Scanning {len(mt.parts)} parts in model_turn")
                for i, part in enumerate(mt.parts):
                    # Log what we find in each part for debugging
                    part_attrs = [a for a in dir(part) if not a.startswith('_') and not callable(getattr(part, a, None))]
                    has_fc = hasattr(part, 'function_call') and part.function_call is not None
                    logger.debug(f"[HARVESTER] Part[{i}] has_function_call={has_fc}, attrs={part_attrs[:5]}")
                    
                    if has_fc:
                        fc = part.function_call
                        logger.info(f"[HARVESTER] Found function_call in part[{i}]: {type(fc).__name__}")
                        tool_data = self._extract_function_call_data(
                            fc, 
                            f'server_content.model_turn.parts[{i}].function_call'
                        )
                        if tool_data:
                            tools.append(tool_data)
                        else:
                            logger.warning(f"[HARVESTER] function_call found but extraction failed for part[{i}]")
        
        # Also check server_content.tool_call (alternative structure)
        if hasattr(sc, 'tool_call') and sc.tool_call is not None:
            tool_call_obj = sc.tool_call
            logger.debug(f"[HARVESTER] Found tool_call at server_content.tool_call")
            
            if hasattr(tool_call_obj, 'function_calls') and tool_call_obj.function_calls:
                for fc in tool_call_obj.function_calls:
                    tool_data = self._extract_function_call_data(fc, 'server_content.tool_call.function_calls')
                    if tool_data:
                        tools.append(tool_data)
            elif hasattr(tool_call_obj, 'name'):
                tools.append({
                    'type': 'tool_call',
                    'name': tool_call_obj.name,
                    'id': getattr(tool_call_obj, 'id', None),
                    'params': getattr(tool_call_obj, 'args', {}) or {},
                    '_source': 'server_content.tool_call'
                })
        
        return tools
    
    def _extract_function_call_data(self, fc, source: str) -> Optional[Dict[str, Any]]:
        """Extract data from a function call object with multiple attribute fallbacks."""
        # Try multiple attribute names for tool name (Gemini API variations)
        tool_name = getattr(fc, 'name', None)
        if not tool_name:
            tool_name = getattr(fc, 'function_name', None)
        if not tool_name:
            tool_name = getattr(fc, 'tool_name', None)
        
        if not tool_name:
            # Log the function_call object structure for debugging
            fc_attrs = {a: str(getattr(fc, a, None))[:100] for a in dir(fc) if not a.startswith('_') and not callable(getattr(fc, a, None))}
            logger.warning(f"[HARVESTER] function_call has no 'name' attribute. Available: {fc_attrs}")
            return None
        
        # Try multiple attribute names for arguments
        tool_args = getattr(fc, 'args', None)
        if tool_args is None:
            tool_args = getattr(fc, 'arguments', None)
        if tool_args is None:
            tool_args = getattr(fc, 'parameters', None)
        tool_args = tool_args or {}
        
        # Try multiple attribute names for ID
        tool_id = getattr(fc, 'id', None)
        if not tool_id:
            tool_id = getattr(fc, 'call_id', None)
        
        logger.info(f"[HARVESTER] Extracted function_call: {tool_name} (id={tool_id}) from {source}")
        
        return {
            'type': 'tool_call',
            'name': tool_name,
            'id': tool_id,
            'params': tool_args,
            '_source': source
        }
    
    def _check_cancellation(self, response) -> Optional[Dict[str, Any]]:
        """Check for tool call cancellation."""
        if not hasattr(response, 'tool_call_cancellation') or response.tool_call_cancellation is None:
            return None
        
        cancellation = response.tool_call_cancellation
        cancelled_ids = getattr(cancellation, 'ids', []) or []
        
        if cancelled_ids:
            logger.warning(f"[HARVESTER] Tool call cancellation received for IDs: {cancelled_ids}")
            return {
                'type': 'tool_call_cancellation',
                'cancelled_ids': cancelled_ids
            }
        
        return None
    
    def _deduplicate(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out already-processed tool calls."""
        unique = []
        
        for tool in tools:
            tool_name = tool.get('name', 'unknown')
            tool_id = tool.get('id')
            
            # Generate composite ID for deduplication
            if tool_id:
                composite_id = f"{tool_name}:{tool_id}"
            else:
                # Fallback: use counter-based ID for tools without explicit IDs
                self._tool_id_counter += 1
                composite_id = f"{tool_name}:auto_{self._tool_id_counter}"
                # Store the generated ID back in the tool dict
                tool['id'] = f"auto_{self._tool_id_counter}"
            
            if composite_id in self._processed_tool_ids:
                logger.debug(f"[HARVESTER] Skipping duplicate tool: {composite_id}")
                continue
            
            # Mark as processed
            self._processed_tool_ids.add(composite_id)
            logger.info(f"[HARVESTER] New tool call: {tool_name} (ID: {composite_id})")
            
            # Remove internal source tracking before yielding
            tool_clean = {k: v for k, v in tool.items() if not k.startswith('_')}
            unique.append(tool_clean)
        
        return unique

