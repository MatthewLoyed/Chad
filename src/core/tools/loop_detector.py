"""
Loop Detector - Prevent Runaway Tool Execution Loops

Implements OpenClaw-inspired loop detection to prevent:
1. Generic repetition (same tool 3+ times in a row)
2. Poll patterns (same tool with identical params repeatedly)
3. Ping-pong sequences (A→B→A→B pattern)

Pattern adopted from OpenClaw's tool execution safeguards.
"""

import logging
import time
import hashlib
import json
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger("LoopDetector")


@dataclass
class ToolExecution:
    """Record of a single tool execution."""
    tool_name: str
    params: Dict[str, Any]
    timestamp: float
    param_hash: str


class LoopDetector:
    """
    Detects and prevents tool execution loops.

    Three types of loop detection:
    1. **Generic Repetition**: Same tool called 3+ times consecutively
    2. **Poll Pattern**: Same tool with identical params called repeatedly (rate limit)
    3. **Ping-Pong**: Alternating pattern between two tools (A→B→A→B)

    Thresholds:
    - Generic repetition: 3 consecutive calls
    - Poll pattern: 3 calls with identical params within 5 seconds
    - Ping-pong: 2 complete cycles (A→B→A→B)
    """

    def __init__(
        self,
        max_consecutive: int = 3,
        max_poll_window: float = 600.0,
        max_poll_count: int = 3,
        max_ping_pong_cycles: int = 2
    ):
        """
        Initialize LoopDetector.

        Args:
            max_consecutive: Maximum consecutive calls to same tool
            max_poll_window: Time window (seconds) for poll detection. Default
                matches src/core/tools/repetition.py's live guard (600s) — local-
                inference tool calls are minutes apart, so the OpenClaw-original
                5s default could never fire and silently disabled poll detection
                for any caller that didn't override it (#641).
            max_poll_count: Maximum identical calls within poll window
            max_ping_pong_cycles: Maximum ping-pong cycles before detection
        """
        self._history: deque[ToolExecution] = deque(maxlen=20)  # Keep last 20 executions
        self._max_consecutive = max_consecutive
        self._max_poll_window = max_poll_window
        self._max_poll_count = max_poll_count
        self._max_ping_pong_cycles = max_ping_pong_cycles

        logger.info("LoopDetector initialized")

    def _hash_params(self, params: Dict[str, Any]) -> str:
        """
        Generate hash of parameters for comparison.

        Args:
            params: Tool parameters dictionary

        Returns:
            MD5 hash of sorted parameters
        """
        sorted_params = json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(sorted_params.encode()).hexdigest()

    def check_loop(self, tool_name: str, params: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if executing this tool would create a loop.

        Args:
            tool_name: Name of the tool to execute
            params: Tool parameters

        Returns:
            Tuple of (is_loop, loop_type)
            - is_loop: True if loop detected
            - loop_type: Description of detected loop type

        Example:
            >>> is_loop, loop_type = detector.check_loop("gmail_search", {...})
            >>> if is_loop:
            ...     print(f"Loop detected: {loop_type}")
        """
        param_hash = self._hash_params(params)

        # Check all loop types
        if self._detect_generic_repetition(tool_name):
            return True, (
                f"Generic repetition loop: '{tool_name}' called {self._max_consecutive}+ times consecutively. "
                f"This suggests the tool is not producing the expected results. "
                f"Try a different approach or provide your response without using tools."
            )

        if self._detect_poll_pattern(tool_name, param_hash):
            return True, (
                f"Poll pattern loop: '{tool_name}' called {self._max_poll_count}+ times "
                f"with identical parameters within {self._max_poll_window}s. "
                f"This suggests repeated checking without changes. "
                f"Consider waiting longer or using a different approach."
            )

        if self._detect_ping_pong(tool_name):
            return True, (
                f"Ping-pong loop: Alternating pattern detected between tools. "
                f"This suggests the tools are not producing actionable results. "
                f"Try combining the information you have or use a different approach."
            )

        return False, ""

    def record_execution(self, tool_name: str, params: Dict[str, Any]) -> None:
        """
        Record a tool execution for loop detection.

        Args:
            tool_name: Name of executed tool
            params: Tool parameters
        """
        execution = ToolExecution(
            tool_name=tool_name,
            params=params,
            timestamp=time.time(),
            param_hash=self._hash_params(params)
        )

        self._history.append(execution)
        logger.debug(f"[ LOOP_DETECTOR ] Recorded execution: {tool_name}")

    def _detect_generic_repetition(self, tool_name: str) -> bool:
        """
        Detect if the same tool is being called too many times consecutively.

        Args:
            tool_name: Tool name to check

        Returns:
            True if generic repetition detected
        """
        if len(self._history) < self._max_consecutive:
            return False

        # Check last N executions
        recent = list(self._history)[-self._max_consecutive:]

        # All must be the same tool
        return all(exec.tool_name == tool_name for exec in recent)

    def _detect_poll_pattern(self, tool_name: str, param_hash: str) -> bool:
        """
        Detect if the same tool with identical parameters is being polled.

        Args:
            tool_name: Tool name to check
            param_hash: Hash of tool parameters

        Returns:
            True if poll pattern detected
        """
        if len(self._history) < self._max_poll_count:
            return False

        now = time.time()

        # Find executions within the poll window with matching tool and params
        matching_executions = [
            exec for exec in self._history
            if (exec.tool_name == tool_name and
                exec.param_hash == param_hash and
                (now - exec.timestamp) <= self._max_poll_window)
        ]

        return len(matching_executions) >= self._max_poll_count

    def _detect_ping_pong(self, tool_name: str) -> bool:
        """
        Detect alternating pattern between two tools (A→B→A→B).

        Args:
            tool_name: Tool name to check

        Returns:
            True if ping-pong pattern detected
        """
        if len(self._history) < (self._max_ping_pong_cycles * 2):
            return False

        # Get recent executions
        recent_count = self._max_ping_pong_cycles * 2 + 1
        recent = list(self._history)[-recent_count:]

        # Extract tool names
        tool_sequence = [exec.tool_name for exec in recent]

        # Add the new tool to the sequence
        tool_sequence.append(tool_name)

        # Check for alternating pattern
        # Pattern: [A, B, A, B] or longer
        if len(set(tool_sequence)) != 2:
            return False  # More than 2 unique tools

        # Check if tools alternate
        for i in range(len(tool_sequence) - 1):
            if tool_sequence[i] == tool_sequence[i + 1]:
                return False  # Not alternating

        # If we get here, it's a valid alternating pattern
        # Check if we have enough cycles
        cycles = len(tool_sequence) // 2
        return cycles >= self._max_ping_pong_cycles

    def reset_turn(self) -> None:
        """Reset loop detection for a new turn."""
        self._history.clear()
        logger.debug("[ LOOP_DETECTOR ] Turn history reset")

    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get execution history for debugging.

        Returns:
            List of execution records
        """
        return [
            {
                'tool_name': exec.tool_name,
                'param_hash': exec.param_hash[:8],
                'timestamp': exec.timestamp,
                'age_seconds': time.time() - exec.timestamp
            }
            for exec in self._history
        ]

    def get_stats(self) -> Dict[str, Any]:
        """
        Get loop detector statistics.

        Returns:
            Dictionary with statistics
        """
        if not self._history:
            return {
                'total_executions': 0,
                'unique_tools': 0,
                'recent_tools': []
            }

        tool_names = [exec.tool_name for exec in self._history]
        unique_tools = set(tool_names)

        return {
            'total_executions': len(self._history),
            'unique_tools': len(unique_tools),
            'recent_tools': tool_names[-5:],  # Last 5 tools
            'max_consecutive': self._max_consecutive,
            'max_poll_count': self._max_poll_count,
            'max_ping_pong_cycles': self._max_ping_pong_cycles
        }

    def __repr__(self) -> str:
        return f"<LoopDetector(history={len(self._history)}, max_consecutive={self._max_consecutive})>"
