"""
Action Scratchpad Mixin - Tracks turn-based progress for autonomous agents.
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("ActionScratchpad")

class ActionScratchpadMixin:
    """
    Shared logic for tracking and injecting an 'Action Scratchpad' into LLM turns.
    Helps prevent Vision/State Reset loops where models forget previous successes.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._action_log: List[str] = []
        self._turn_objective: str = ""

    def _reset_scratchpad(self, objective: str = ""):
        """
        Clears the log and sets the high-level objective for the turn.
        """
        self._action_log = []
        self._turn_objective = objective

    def _log_action(self, name: str, args: Dict[str, Any], result: Any):
        """
        Logs a successful tool execution with a concise argument summary.
        
        A result is considered successful ONLY if:
        1. It is a dict with 'success': True
        2. It is a dict with NO 'error' key AND NO 'success' key (optimistic fallback)
        """
        # Strict success detection (no defaults to True)
        is_success = False
        if isinstance(result, dict):
            if result.get("success") is True:
                is_success = True
            elif "error" not in result and "success" not in result:
                is_success = True
        
        if not is_success:
            return
            
        # Clean arguments: remove binary data and truncate long strings
        clean_args = {}
        for k, v in args.items():
            val_str = str(v)
            # Filter out images, long base64 strings, or data blobs
            # The length truncation already handles large content values
            if any(key in k.lower() for key in ["base64", "image", "data"]) or len(val_str) > 100:
                clean_args[k] = f"<{type(v).__name__}...>"
            else:
                clean_args[k] = v
                
        try:
            arg_str = json.dumps(clean_args)
        except Exception:
            arg_str = str(clean_args)

        step_num = len(self._action_log) + 1
        is_ephemeral = isinstance(result, dict) and (result.get("ephemeral") or result.get("terminal"))
        ephemeral_note = " [Result is ephemeral — do not attempt visual verification]" if is_ephemeral else ""
        log_entry = f"Step {step_num}: {name}({arg_str}) -> SUCCESS{ephemeral_note}"
        self._action_log.append(log_entry)
        logger.debug(f"[ SCRATCHPAD ] Logged: {log_entry}")

    def _build_scratchpad_message(self) -> Optional[Dict[str, Any]]:
        """
        Builds a 'user' role message containing the progress log.
        """
        if not self._action_log:
            return None
            
        lines = "\n".join(f"- {s}" for s in self._action_log)
        objective_section = f"\n\n[NEXT OBJECTIVE]: {self._turn_objective}" if self._turn_objective else ""
        
        content = (
            "### 📝 TURN PROGRESS (Already Completed)\n"
            f"{lines}\n\n"
            "**IMPORTANT**: The current environment state reflects the completion of the steps above. "
            "Do NOT re-execute them. Continue from the current state to reach the objective."
            f"{objective_section}"
        )
        
        return {"role": "system", "content": content}
