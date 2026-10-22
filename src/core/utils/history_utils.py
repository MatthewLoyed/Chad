"""
History Utilities - Data layer for conversation history management.

This module provides functions to convert raw conversation history
to SDK-compatible format for Gemini Live API injection.

Extracted from models/gemini_live.py as part of the 3-layer refactor:
- Network Layer: models/gemini_live.py (WebSocket management)
- Logic Layer: core/response_parser.py (response parsing)
- Data Layer: core/history_utils.py (history conversion)
"""

from __future__ import annotations
import re
from typing import List, Dict, Any, TYPE_CHECKING
if TYPE_CHECKING:
    from google.genai import types

from src.shared.logger import setup_logger

logger = setup_logger("HistoryUtils")


def reconstruct_history_safe(raw_history: List[Dict[str, Any]]) -> List[types.Content]:
    """
    Reconstruct conversation history using SDK type constructors for validation.
    
    Uses types.Content and types.Part to ensure data structure matches SDK expectations.
    This validates the data locally before sending, preventing WebSocket 1007 errors.
    
    Rules:
    - Only allow role: "user" or "model" (map others to "user")
    - Flatten all parts into a single text string
    - Use SDK constructors (types.Content, types.Part) for validation
    
    Args:
        raw_history: Raw history from MemoryManager
        
    Returns:
        List of types.Content objects ready for SDK injection
    """
    from google.genai import types
    clean_turns = []
    
    for entry in raw_history:
        # Default to user if role is missing or invalid
        role = entry.get("role", "user")
        if role not in ["user", "model"]:
            # Function role turns should be SKIPPED, not converted to user
            if role == "function":
                continue  # Skip function turns - they're metadata
            role = "user"
        
        parts = entry.get("parts", [])
        text_accumulator = ""
        
        # Check for sanitized_text first (new schema with pre-computed sanitized text)
        # This is the preferred path for model turns with tool calls
        if "sanitized_text" in entry:
            sanitized_text_raw = entry["sanitized_text"]
            # Strip thinking blocks from sanitized_text if present
            if sanitized_text_raw:
                # Strip **Thinking** blocks
                sanitized_text_raw = re.sub(r'\*\*[^*]+\*\*', '', sanitized_text_raw)
                # Remove lines starting with **
                lines = sanitized_text_raw.split('\n')
                cleaned_lines = [line for line in lines if not line.strip().startswith('**')]
                text_accumulator = '\n'.join(cleaned_lines).strip()
            else:
                text_accumulator = ""
        # Robust extraction of text from various saved formats
        elif isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict):
                    # Check for sanitized_text in part (if stored per-part)
                    if "sanitized_text" in p:
                        text_accumulator += p["sanitized_text"] + " "
                    # Convert functionCall to text
                    elif "functionCall" in p:
                        func_call = p["functionCall"]
                        tool_name = func_call.get("name", "unknown") if isinstance(func_call, dict) else "unknown"
                        text_accumulator += f"[System: Action '{tool_name}' executed successfully] "
                    # Convert functionResponse to text
                    elif "functionResponse" in p:
                        func_resp = p["functionResponse"]
                        func_name = func_resp.get("name", "unknown") if isinstance(func_resp, dict) else "unknown"
                        text_accumulator += f"[System: Tool '{func_name}' completed] "
                    # Text extraction (keep for backward compatibility)
                    elif "text" in p:
                        text_accumulator += str(p["text"]) + " "
                # Saved as simple string
                elif isinstance(p, str):
                    text_accumulator += p + " "
                # Saved as SDK object repr (rare, but handles edge case)
                elif hasattr(p, "text"):
                    text_accumulator += p.text + " "
        
        # Handle "Ghost User" cases: if user turn has no transcript after extraction, use fallback
        if role == "user" and not text_accumulator.strip():
            has_real_content = False
            if isinstance(parts, list):
                for p in parts:
                    if isinstance(p, dict) and "text" in p:
                        text_content = p["text"]
                        if text_content and text_content.strip() and text_content != "[Audio Message]":
                            has_real_content = True
                            break
            
            if not has_real_content:
                # Use fallback text for ghost user
                text_accumulator = "(Audio input received)"
        
        # Strict check: API crashes on empty parts
        final_text = text_accumulator.strip()
        if final_text:
            # Use the SDK Constructor - validates the data structure immediately
            turn = types.Content(
                role=role,
                parts=[types.Part(text=final_text)]
            )
            clean_turns.append(turn)
    
    # PHASE 3: Enforce "User-First" History Rule
    # Gemini API requires conversation history to alternate User -> Model, starting with User.
    # A dangling Model response at the start creates an invalid state (Error 1007).
    removed_count = 0
    # A dangling Model response at the start creates an invalid state (Error 1007).
    # FIX: Check if first turn is model, and if so, prepend a dummy user turn.
    if clean_turns and len(clean_turns) > 0 and clean_turns[0].role == "model":
        first_turn = clean_turns[0]
        removed_count += 1
        # Extract text for logging
        removed_text = ""
        if hasattr(first_turn, 'parts') and first_turn.parts:
            for part in first_turn.parts:
                if hasattr(part, 'text') and part.text:
                    removed_text = part.text[:50] + "..." if len(part.text) > 50 else part.text
        logger.warning(f"Found model turn at start of history (violates User-First rule): '{removed_text}'")
        
        # FIX: Instead of removing it, inject a dummy user turn to make it valid
        # This preserves the model's "priming" or previous output
        logger.info("Injecting dummy User turn to validate history structure.")
        clean_turns.insert(0, types.Content(
            role="user",
            parts=[types.Part(text="[SYSTEM]: Session context restored.")]
        ))
        # Structure is now valid (User -> Model -> ...)
    
    if removed_count > 0:
        logger.info(f"Removed {removed_count} model turn(s) from start. History now has {len(clean_turns)} valid turn(s).")
    
    return clean_turns


def sanitize_history(history_list: List[Dict[str, Any]]) -> List[types.Content]:
    """
    Sanitize conversation history by stripping extra fields and converting to SDK Content format.
    
    Rules:
    - Keep only: role and parts
    - Strip: timestamp, iso_time, or any other custom keys
    - Format parts: Ensure parts is always a list with proper structure
    - Convert function roles: Map function responses to text format (FunctionResponse requires ID we don't have)
    
    Args:
        history_list: Raw history from MemoryManager
        
    Returns:
        List of Content objects ready for SDK injection
    """
    from google.genai import types
    clean_content = []
    
    for turn in history_list:
        role = turn.get("role", "user")
        parts_raw = turn.get("parts", [])
        
        # Skip if no parts
        if not parts_raw:
            continue
        
        # Handle function role - convert to text format (FunctionResponse requires ID)
        if role == "function":
            # Convert function responses to text for context
            func_resp_texts = []
            for part in parts_raw:
                if isinstance(part, dict) and "functionResponse" in part:
                    func_resp = part["functionResponse"]
                    func_name = func_resp.get("name", "unknown")
                    func_result = func_resp.get("response", {}).get("result", {})
                    func_resp_texts.append(f"[TOOL OUTPUT]: Function {func_name} returned: {func_result}")
            
            if func_resp_texts:
                # Add as model turn with text description
                clean_content.append(types.Content(
                    role="model",
                    parts=[types.Part(text="\n".join(func_resp_texts))]
                ))
            continue
        
        # Process parts for user/model roles
        parts_list = []
        has_text = False
        has_function_only = False
        
        for part in parts_raw:
            if not isinstance(part, dict):
                continue
            
            # Text part
            if "text" in part:
                text_content = part["text"]
                # CRITICAL: Never add empty or whitespace-only text (causes 1007 errors)
                if text_content and text_content.strip():
                    parts_list.append(types.Part(text=text_content))
                    has_text = True
            
            # Function call part
            elif "functionCall" in part:
                func_call = part["functionCall"]
                if isinstance(func_call, dict):
                    func_name = func_call.get("name", "unknown")
                    parts_list.append(types.Part(
                        function_call=types.FunctionCall(
                            name=func_name,
                            args=func_call.get("args", {})
                        )
                    ))
                    if not has_text:
                        has_function_only = True
            
            # Function response part (convert to text to avoid empty parts)
            elif "functionResponse" in part:
                func_resp = part["functionResponse"]
                func_name = func_resp.get("name", "unknown")
                # Convert function response to text to avoid empty parts (causes 1007 errors)
                parts_list.append(types.Part(text=f"[User executed tool: {func_name}]"))
                has_text = True
        
        # CRITICAL: Never add a turn if it has no text content (empty parts cause 1007 errors)
        # If a turn only has function calls/responses, convert to text format
        if parts_list:
            # If we have function calls but no text, convert to text representation
            if has_function_only and not has_text:
                # Convert function-only turn to text
                func_texts = []
                for part in parts_list:
                    if hasattr(part, 'function_call'):
                        func_name = part.function_call.name if hasattr(part.function_call, 'name') else 'unknown'
                        func_texts.append(f"[User executed tool: {func_name}]")
                
                if func_texts:
                    # Replace with text-only part
                    parts_list = [types.Part(text=" ".join(func_texts))]
                    has_text = True
            
            # Final check: ensure we have at least one non-empty text part
            # Skip turns with only empty/whitespace text (causes 1007 errors)
            if has_text:
                clean_content.append(types.Content(
                    role=role,
                    parts=parts_list
                ))
            else:
                logger.debug(f"Skipping turn with no text content: role={role}, parts_count={len(parts_list)}")
    
    return clean_content









