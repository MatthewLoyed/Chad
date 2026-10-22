import logging
import json
import asyncio
from typing import List, Dict, Any, Optional, Callable, Set
from datetime import datetime
from dataclasses import dataclass, field

from src.core.utils.compaction_context import extraction_guidance, normalize_context, render_context
from src.core.tools import limits
from src.core.utils.token_counter import TokenCounter
from src.core.intelligence.memory_engine import MemoryEngine
# We'll need a way to call the model for summarization.
# For now, we'll assume we pass a `generate_fn` or similar to the method.

logger = logging.getLogger("ConsolidationManager")


@dataclass
class CompactionSafeguards:
    """
    Safeguards to preserve critical context during compaction (Pi/OpenClaw pattern).

    Prevents losing important information during memory compression:
    - Tool failures and errors
    - File operations (created, modified, deleted)
    - Incomplete tasks
    - Critical turns with high importance
    """
    tool_failures: List[Dict[str, Any]] = field(default_factory=list)
    file_operations: List[Dict[str, Any]] = field(default_factory=list)
    incomplete_tasks: List[str] = field(default_factory=list)
    critical_turn_ids: Set[int] = field(default_factory=set)
    important_memories: List[str] = field(default_factory=list)

class ConsolidationManager:
    """
    Manages the 'Auto-Sleep' protocol:
    1. Monitors token usage via TokenCounter.
    2. Triggers Soft/Hard limits.
    3. Performs 'Consolidation' (Extraction -> Flush -> Wake).
    """
    
    # Context Budgets (Tokens)
    # Gemini 2.0 has 1M context, so cloud profiles can be generous.
    # local_small / local_large are sized for 32K and 128K local models respectively,
    # triggered at ~65% (soft) and ~80% (hard) of the model's effective context window.
    PROFILES = {
        "local_small": {"soft": 20000, "hard": 26000, "max": 30000},   # 32K models  (~65%/~80%)
        "local_large": {"soft": 80000, "hard": 100000, "max": 110000}, # 128K models (~65%/~80%)
        "aggressive":  {"soft": 30000, "hard": 40000, "max": 45000},   # ~3x baseline (cloud)
        "balanced":    {"soft": 120000, "hard": 150000, "max": 160000}, # ~12x baseline (Standard)
        "permissive":  {"soft": 400000, "hard": 480000, "max": 500000}, # ~40x baseline (Deep Work)
    }
    
    CONSOLIDATION_OVERHEAD = 2000  # Tokens reserved for the extraction prompt/response
    
    def __init__(self, memory_engine: MemoryEngine, profile: str = "balanced"):
        self.memory_engine = memory_engine
        self.profile_name = profile
        self.limits = self.PROFILES.get(profile, self.PROFILES["balanced"])
        self._last_system_injection_turn = -1  # Track when we last warned user
        
    def set_profile(self, profile_name: str):
        """Update the context budget profile."""
        if profile_name in self.PROFILES:
            self.profile_name = profile_name
            self.limits = self.PROFILES[profile_name]
            logger.info(f"[ CONSOLIDATION ] Switched to '{profile_name}' profile: {self.limits}")

    def get_status(self) -> Dict[str, Any]:
        """Get current configuration and profile status."""
        return {
            "profile": self.profile_name,
            "limits": self.limits,
            "overhead": self.CONSOLIDATION_OVERHEAD
        }

    def check_triggers(self, current_tokens: int, turn_count: int) -> str:
        """
        Check if any triggers are hit.
        Returns: 'none', 'soft', or 'hard'
        """
        if current_tokens > self.limits["hard"]:
            return "hard"
        
        if current_tokens > self.limits["soft"]:
            # Only trigger soft warning once every 10 turns to avoid nagging
            if turn_count - self._last_system_injection_turn > 10:
                self._last_system_injection_turn = turn_count
                return "soft"
                
        return "none"

    def get_system_warning(self, current_tokens: int) -> str:
        """Get the text for the 'Soft Trigger' injection."""
        limit = self.limits["hard"]
        percent = int((current_tokens / limit) * 100)
        return (
            f"[SYSTEM ALERT: Context load at {percent}% ({current_tokens} tokens). "
            f"Please wrap up the current thought and find a stopping point to consolidate memory.]"
        )
        
    async def perform_consolidation(
        self, 
        history: List[Dict[str, Any]], 
        model_generate_fn: Callable,
        reasoning_logger: Callable = None
    ) -> Dict[str, Any]:
        """
        Executes the Ship's Log Protocol:
        1. Extract structured data from history.
        2. Save to MemoryEngine.
        3. Return the 'Wake Up' context block.
        """
        logger.info("[ CONSOLIDATION ] Starting consolidation sequence...")
        if reasoning_logger:
            reasoning_logger("Minimizing neural load... consolidating memories...")

        # 1. Extraction
        extraction_prompt = self._get_extraction_prompt() + extraction_guidance(
            int(limits.get("resume_context_target_chars"))
        )
        
        try:
            # We use a separate generation call to avoid polluting the main context
            # Simulating a specialized 'Cognitive Operation'
            response = await model_generate_fn(
                prompt=extraction_prompt, 
                history=history, # Pass history so model can analyze it
                json_mode=True
            )
            
            extracted_data = self._parse_extraction(response)
            
        except Exception as e:
            logger.error(f"[ CONSOLIDATION ] Extraction failed: {e}")
            # Fallback: Create a minimal rescue record
            extracted_data = {
                "completed_tasks": ["Consolidation failed - context flushed"],
                "pending_tasks": ["Resume previous activity"],
                "project_state": "Recovering from error",
                "user_data": {},
                "system_changes": []
            }

        # 2. Persist to MemoryEngine (SQLite)
        self._persist_data(extracted_data)
        
        # 3. Return the 'Wake Up' context
        wake_up_message = (
            f"[SYSTEM: Memory consolidated. Resuming session.\n"
            f"Previous Project State: {extracted_data.get('project_state', 'Unknown')}\n"
            f"Pending Tasks: {', '.join(extracted_data.get('pending_tasks', []))}]"
        )
        
        context = render_context(extracted_data.get("resume_context"), int(limits.get("resume_context_max_chars")))
        if context:
            wake_up_message += "\n" + context

        logger.info("[ CONSOLIDATION ] Sequence complete. Ready to wake.")
        return {
            "wake_message": wake_up_message,
            "summary": extracted_data
        }

    def _get_extraction_prompt(self) -> str:
        return """
[SYSTEM: CONSOLIDATION PHASE]
Analyzes the conversation history provided and EXTRACT the following.
Format as strict JSON. Do NOT summarize conversational fluff.
FOCUS on actions, decisions, and system state.

REQUIRED JSON STRUCTURE:
{
    "completed_tasks": ["task 1", "task 2"],
    "pending_tasks": ["task 3", "queue item"],
    "user_data": {"preference_key": "preference_value"},
    "system_changes": ["file modified: x.py", "setting changed: y"],
    "project_state": "Brief one-line summary of current focus"
}
"""

    def _parse_extraction(self, response_text: str) -> Dict[str, Any]:
        """Safe JSON parsing of the model response."""
        try:
            # Strip markdown code blocks if present
            clean_text = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            if not isinstance(data, dict):
                raise ValueError("Consolidation response must be an object")
            context = normalize_context(data.get("resume_context"), int(limits.get("resume_context_max_chars")))
            data.pop("resume_context", None)
            if context:
                data["resume_context"] = context
            return data
        except json.JSONDecodeError:
            logger.warning("[ CONSOLIDATION ] JSON parse failed, attempting loose repair")
            # In a real impl, we might use a robust parser here
            raise

    def _persist_data(self, data: Dict[str, Any]):
        """Save extracted facts to the database."""
        # 1. Save User Data (Preferences)
        if "user_data" in data and isinstance(data["user_data"], dict):
            for key, value in data["user_data"].items():
                self.memory_engine.save_memory(
                    content=f"User Preference: {key} is {value}", 
                    category="preference"
                )

        # 2. Save Tasks/State as a 'Session Log' memory
        # This allows semantic search to find "What did we do last week?"
        log_content = (
            f"SESSION LOG ({datetime.now().strftime('%Y-%m-%d %H:%M')}):\n"
            f"State: {data.get('project_state')}\n"
            f"Completed: {', '.join(data.get('completed_tasks', []))}\n"
            f"Changes: {', '.join(data.get('system_changes', []))}"
        )
        self.memory_engine.save_memory(content=log_content, category="session_log", importance=0.4)

    # =========================================================================
    # COMPACTION SAFEGUARDS (Phase 2 - Pi/OpenClaw Pattern)
    # =========================================================================

    def extract_safeguards(self, history: List[Dict[str, Any]]) -> CompactionSafeguards:
        """
        Extract critical information that must be preserved during compaction.

        Args:
            history: Conversation history turns

        Returns:
            CompactionSafeguards with extracted critical data

        Safeguards:
            1. Tool failures - Preserve error context for debugging
            2. File operations - Track what was modified
            3. Incomplete tasks - Preserve ongoing work
            4. Critical turns - High importance turns to keep
        """
        safeguards = CompactionSafeguards()

        for i, turn in enumerate(history):
            role = turn.get('role', '')
            content = turn.get('content', '')

            # Extract tool failures
            if 'tool_result' in turn or 'function_call' in turn:
                if 'error' in str(content).lower() or turn.get('success') is False:
                    safeguards.tool_failures.append({
                        'turn_index': i,
                        'tool_name': turn.get('tool_name', 'unknown'),
                        'error': turn.get('error', 'Unknown error'),
                        'timestamp': turn.get('timestamp', datetime.now().isoformat())
                    })

            # Extract file operations
            file_keywords = ['created', 'modified', 'deleted', 'wrote', 'saved', 'updated']
            if any(keyword in content.lower() for keyword in file_keywords):
                # Try to extract file paths
                files = self._extract_file_paths(content)
                if files:
                    safeguards.file_operations.append({
                        'turn_index': i,
                        'operation': self._detect_operation(content),
                        'files': files,
                        'timestamp': turn.get('timestamp', datetime.now().isoformat())
                    })

            # Extract incomplete tasks
            task_keywords = ['working on', 'in progress', 'pending', 'need to', 'todo', 'next step']
            if any(keyword in content.lower() for keyword in task_keywords):
                incomplete = self._extract_incomplete_task(content)
                if incomplete:
                    safeguards.incomplete_tasks.append(incomplete)

            # Mark critical turns (high importance or user questions)
            if role == 'user' or 'important' in turn.get('tags', []):
                safeguards.critical_turn_ids.add(i)

            # Long model responses are likely important
            if role == 'model' and len(content) > 500:
                safeguards.critical_turn_ids.add(i)

        logger.info(
            f"[ SAFEGUARDS ] Extracted: {len(safeguards.tool_failures)} failures, "
            f"{len(safeguards.file_operations)} file ops, "
            f"{len(safeguards.incomplete_tasks)} tasks, "
            f"{len(safeguards.critical_turn_ids)} critical turns"
        )

        return safeguards

    def _extract_file_paths(self, content: str) -> List[str]:
        """Extract file paths from content."""
        import re

        # Match common file path patterns
        patterns = [
            r'["\']([^"\']*\.[a-zA-Z]{2,4})["\']',  # Quoted paths with extensions
            r'(?:file:|path:)\s*([^\s,]+)',         # file: or path: prefix
            r'([a-zA-Z]:\\[^\s,]+)',                # Windows paths
            r'(/[^\s,]+\.[a-zA-Z]{2,4})',           # Unix paths
        ]

        files = []
        for pattern in patterns:
            matches = re.findall(pattern, content)
            files.extend(matches)

        # Remove duplicates and common false positives
        files = list(set(f for f in files if len(f) > 3 and '.' in f))
        return files[:10]  # Limit to 10 files

    def _detect_operation(self, content: str) -> str:
        """Detect the type of file operation."""
        content_lower = content.lower()

        if 'created' in content_lower or 'new file' in content_lower:
            return 'created'
        elif 'deleted' in content_lower or 'removed' in content_lower:
            return 'deleted'
        elif 'modified' in content_lower or 'updated' in content_lower or 'changed' in content_lower:
            return 'modified'
        elif 'wrote' in content_lower or 'saved' in content_lower:
            return 'saved'
        else:
            return 'unknown'

    def _extract_incomplete_task(self, content: str) -> Optional[str]:
        """Extract incomplete task description from content."""
        import re

        # Look for task patterns
        patterns = [
            r'(?:working on|in progress):\s*([^.!?\n]+)',
            r'(?:need to|todo|next step):\s*([^.!?\n]+)',
            r'pending:\s*([^.!?\n]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, content.lower())
            if match:
                task = match.group(1).strip()
                if len(task) > 10:  # Reasonable task length
                    return task[:200]  # Limit length

        return None

    def calculate_adaptive_budget(
        self,
        history: List[Dict[str, Any]],
        safeguards: CompactionSafeguards,
        target_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculate adaptive token budget for compaction.

        Allocates budget based on turn importance:
        - Critical turns: 100% preserved
        - Tool failures: 80% preserved (error context)
        - File operations: 60% preserved
        - Recent turns: 50% preserved (recency bias)
        - Old turns: 20% preserved (compressed)

        Args:
            history: Conversation history
            safeguards: Extracted safeguards
            target_tokens: Target token count (defaults to soft limit)

        Returns:
            Dictionary with budget allocation
        """
        target = target_tokens or self.limits['soft']

        # Estimate current token count (rough approximation)
        current_tokens = sum(len(turn.get('content', '')) // 4 for turn in history)

        # Calculate how much we need to compress
        compression_ratio = target / current_tokens if current_tokens > target else 1.0

        # Allocate budget by priority
        budget = {
            'critical_turns': [],         # Fully preserve
            'tool_failures': [],          # High priority
            'file_operations': [],        # Medium priority
            'recent_turns': [],           # Recency bias
            'compressible_turns': [],     # Can be heavily compressed
            'compression_ratio': compression_ratio,
            'target_tokens': target
        }

        recent_threshold = len(history) - 10  # Last 10 turns are "recent"

        for i, turn in enumerate(history):
            # Critical turns: full preservation
            if i in safeguards.critical_turn_ids:
                budget['critical_turns'].append(i)

            # Tool failures: high priority
            elif any(f['turn_index'] == i for f in safeguards.tool_failures):
                budget['tool_failures'].append(i)

            # File operations: medium priority
            elif any(f['turn_index'] == i for f in safeguards.file_operations):
                budget['file_operations'].append(i)

            # Recent turns: recency bias
            elif i >= recent_threshold:
                budget['recent_turns'].append(i)

            # Everything else is compressible
            else:
                budget['compressible_turns'].append(i)

        logger.info(
            f"[ ADAPTIVE BUDGET ] Compression ratio: {compression_ratio:.2f}, "
            f"Critical: {len(budget['critical_turns'])}, "
            f"Failures: {len(budget['tool_failures'])}, "
            f"Files: {len(budget['file_operations'])}, "
            f"Recent: {len(budget['recent_turns'])}, "
            f"Compressible: {len(budget['compressible_turns'])}"
        )

        return budget

    def split_history_by_tokens(
        self, 
        history: List[Dict[str, Any]], 
        keep_tokens: int = 20000
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Split history into (to_compact, to_keep) based on token size from the end.
        Ensures the last keep_tokens worth of message tokens are kept raw (to_keep).
        
        Args:
            history: List of conversation messages
            keep_tokens: Number of tokens to preserve raw at the end of the history
            
        Returns:
            Tuple of (messages_to_compact, messages_to_keep)
        """
        accumulated_tokens = 0
        split_index = len(history)
        
        for i in range(len(history) - 1, -1, -1):
            msg_tokens = TokenCounter.count_message(history[i])
            if accumulated_tokens + msg_tokens > keep_tokens:
                split_index = i + 1
                break
            accumulated_tokens += msg_tokens
        else:
            # If the entire history fits within keep_tokens, we don't need to compact anything
            split_index = 0
            
        return history[:split_index], history[split_index:]

    async def perform_consolidation_with_safeguards(
        self,
        history: List[Dict[str, Any]],
        model_generate_fn: Callable,
        reasoning_logger: Callable = None,
        persist: bool = True
    ) -> Dict[str, Any]:
        """
        Enhanced consolidation with safeguard preservation (Pi/OpenClaw pattern).

        Steps:
            1. Extract safeguards (failures, files, tasks)
            2. Calculate adaptive budget
            3. Preserve critical context
            4. Compress non-critical turns
            5. Generate enhanced wake-up message

        Args:
            history: Conversation history
            model_generate_fn: Function to generate summaries
            reasoning_logger: Optional logger for reasoning updates
            persist: Whether to immediately persist extracted summary data to SQLite

        Returns:
            Dictionary with wake_message, summary, and safeguards
        """
        logger.info("[ CONSOLIDATION ] Starting enhanced consolidation with safeguards...")
        if reasoning_logger:
            reasoning_logger("Minimizing neural load... preserving critical context...")

        # Step 1: Extract safeguards
        safeguards = self.extract_safeguards(history)

        # Step 2: Calculate adaptive budget
        budget = self.calculate_adaptive_budget(history, safeguards)

        # Step 3: Enhanced extraction with safeguards
        extraction_prompt = self._get_enhanced_extraction_prompt(safeguards) + extraction_guidance(
            int(limits.get("resume_context_target_chars"))
        )

        try:
            response = await model_generate_fn(
                prompt=extraction_prompt,
                history=history,
                json_mode=True
            )

            extracted_data = self._parse_extraction(response)

        except Exception as e:
            logger.error(f"[ CONSOLIDATION ] Extraction failed: {e}")
            # Fallback with safeguards
            extracted_data = {
                "completed_tasks": ["Consolidation failed - context flushed"],
                "pending_tasks": safeguards.incomplete_tasks or ["Resume previous activity"],
                "project_state": "Recovering from error",
                "user_data": {},
                "system_changes": [],
                "tool_failures": [f['error'] for f in safeguards.tool_failures],
                "file_operations": [f"{f['operation']}: {', '.join(f['files'])}" for f in safeguards.file_operations]
            }

        # Step 4: Persist with safeguards
        if persist:
            self._persist_data_with_safeguards(extracted_data, safeguards)

        # Step 5: Enhanced wake-up message with safeguards
        wake_up_message = self._generate_wake_up_message(extracted_data, safeguards)

        logger.info("[ CONSOLIDATION ] Enhanced sequence complete with safeguards.")
        return {
            "wake_message": wake_up_message,
            "summary": extracted_data,
            "safeguards": safeguards,
            "budget": budget
        }

    def _get_enhanced_extraction_prompt(self, safeguards: CompactionSafeguards) -> str:
        """Use the shared extraction schema with the observed safeguard evidence."""
        evidence = {
            "tool_failures": safeguards.tool_failures[:5],
            "file_operations": safeguards.file_operations[:10],
            "incomplete_tasks": safeguards.incomplete_tasks[:5],
        }
        return (self._get_extraction_prompt() +
                "\nPreserve these observed safeguards: " + json.dumps(evidence, ensure_ascii=False) +
                "\nAlso include tool_failures and file_operations arrays in the JSON result.\n")

    def _persist_data_with_safeguards(self, data: Dict[str, Any], safeguards: CompactionSafeguards):
        """Save extracted data with safeguard preservation."""
        # Standard persistence
        self._persist_data(data)

        # Additional safeguard persistence
        if safeguards.tool_failures:
            failure_summary = "Tool Failures:\n" + "\n".join(
                f"  - {f['tool_name']}: {f['error']}"
                for f in safeguards.tool_failures
            )
            self.memory_engine.save_memory(
                content=failure_summary,
                category="errors",
                importance=0.8  # High importance for debugging
            )

        if safeguards.file_operations:
            file_summary = "File Operations:\n" + "\n".join(
                f"  - {f['operation']}: {', '.join(f['files'][:3])}"
                for f in safeguards.file_operations
            )
            self.memory_engine.save_memory(
                content=file_summary,
                category="file_operations",
                importance=0.6
            )

        if safeguards.incomplete_tasks:
            task_summary = "Incomplete Tasks:\n" + "\n".join(
                f"  - {task}"
                for task in safeguards.incomplete_tasks
            )
            self.memory_engine.save_memory(
                content=task_summary,
                category="pending_tasks",
                importance=0.7
            )

    def _generate_wake_up_message(self, data: Dict[str, Any], safeguards: CompactionSafeguards) -> str:
        """Generate enhanced wake-up message with safeguards."""
        message_parts = [
            "[SYSTEM: Memory consolidated with safeguards. Resuming session.]",
            f"Project State: {data.get('project_state', 'Unknown')}"
        ]

        if safeguards.tool_failures:
            message_parts.append(
                f"⚠️  {len(safeguards.tool_failures)} tool failure(s) preserved for debugging"
            )

        if safeguards.file_operations:
            message_parts.append(
                f"📝 {len(safeguards.file_operations)} file operation(s) tracked"
            )

        if safeguards.incomplete_tasks:
            message_parts.append(
                f"📋 {len(safeguards.incomplete_tasks)} incomplete task(s) preserved"
            )

        pending = data.get('pending_tasks', [])
        if pending:
            message_parts.append(f"Pending: {', '.join(pending[:3])}")

        context = render_context(data.get("resume_context"), int(limits.get("resume_context_max_chars")))
        if context:
            message_parts.append(context)
        return "\n".join(message_parts)
