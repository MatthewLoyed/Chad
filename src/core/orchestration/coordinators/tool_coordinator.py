"""
Tool Coordinator - Single Responsibility for Tool Execution
Extracted from SessionManager to enable scalable, agent-centric tool execution.

This coordinator handles:
- Tool execution orchestration (batch and individual)
- Tool limit enforcement (soft/hard limits)
- Auto-escalation to Frontal Lobe for complex batches
- Interrupt detection and cancellation
- Future: Cron-triggered tools, autonomous skill execution

LANE SCOPE (#641 audit): `execute_single`/`execute_batch` below — and with them
this coordinator's LoopDetector + IdempotencyManager + policy + prerequisite
checks — are reachable ONLY from voice_session.py's legacy tool-call flow (via
TurnResponseRouter in src/core/orchestration/conversation/response_router.py).
The main LangGraph agent lane never calls them: LangGraph's ToolNode invokes
tools through ToolRegistry.execute_tool (src/infrastructure/tools/registry.py)
directly, which wraps every call with src/core/tools/repetition.py's guard
instead. Side-effecting-tool duplicate-execution protection therefore exists
on the LangGraph lane only in the form of that guard's non-progress hint, NOT
this coordinator's IdempotencyManager cache — the two lanes are not
equivalent, just parallel. Do not assume a fix here reaches the LangGraph lane,
or vice versa.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, Set
from dataclasses import dataclass

from src.shared.logger import setup_logger
from src.core.tools.base_executor import BaseToolExecutor
from src.application.events import EventBus, ToolStarted, ToolCompleted

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.infrastructure.tools.registry import ToolRegistry
from src.core.tools.idempotency_manager import IdempotencyManager
from src.core.tools.loop_detector import LoopDetector
from src.core.tools.tool_policy_manager import get_policy_manager
from src.shared.context import RequestContext

logger = setup_logger("ToolCoordinator")

# Tool Limits
MAX_TOOLS_PER_TURN = 20  # Hard limit: System refuses execution
SOFT_TOOL_LIMIT = 10     # Soft limit: Triggers "Are you stuck?" protocol

# Tool Safety Prerequisites
TOOL_PREREQUISITES = {
    "file_manager": ["propose_file_plan"],
    "execute_approved_plan": ["propose_file_plan"],
}


@dataclass
class ToolExecutionResult:
    """Result of tool execution with metadata"""
    success: bool
    result: Any
    tool_name: str
    tool_id: Optional[str] = None
    error: Optional[str] = None
    escalated: bool = False
    interrupted: bool = False
    terminal: bool = False


class ToolCoordinator:
    """
    Coordinates all tool execution for the assistant.

    Design principles:
    - Single responsibility: Tool execution only
    - Event-driven: Publishes ToolStarted/ToolCompleted events
    - Strategy-based: Uses BaseToolExecutor for execution
    - Scalable: Designed for cron jobs, autonomous skills, multi-agent
    """

    def __init__(
        self,
        tool_registry: "ToolRegistry",
        tool_executor: BaseToolExecutor,
        event_bus: EventBus,
        confirmation_handler: Optional[Callable] = None,
        interrupt_check: Optional[Callable[[], bool]] = None
    ):
        """
        Initialize ToolCoordinator.

        Args:
            tool_registry: Unified tool registry
            tool_executor: Executor strategy (LocalToolExecutor, RemoteExecutor, etc.)
            event_bus: Event bus for publishing tool events
            confirmation_handler: Callback for tool limit confirmation
            interrupt_check: Callback that returns True if execution should stop
        """
        self.tools = tool_registry
        self.executor = tool_executor
        self.event_bus = event_bus
        self.confirmation_handler = confirmation_handler
        self.interrupt_check = interrupt_check

        # Turn-level tracking
        self._tool_count_this_turn: int = 0
        self._escalation_threshold: int = SOFT_TOOL_LIMIT

        # Session-level tracking (prerequisites)
        self._tools_called_this_session: Set[str] = set()

        # Active task tracking (for cancellation)
        self._active_tool_tasks: Set[asyncio.Task] = set()

        # Deduplication (prevent duplicate tool IDs in same turn)
        self._processed_tool_ids: Set[str] = set()

        # Idempotency cache (prevent duplicate side-effecting tool executions)
        self._idempotency_manager = IdempotencyManager(ttl_seconds=28800)  # 8 hours

        # Loop detector (prevent runaway loops). This coordinator's execute_single/
        # execute_batch are reachable ONLY from voice_session.py's legacy tool-call
        # flow (#641 audit) — the LangGraph lane calls tools via
        # ToolRegistry.execute_tool directly, which wraps every call with
        # src/core/tools/repetition.py's guard instead (600s poll window, since
        # local-inference tool calls are minutes apart, not seconds). Match that
        # window here too — LoopDetector's stock 5.0s default could never fire at
        # local-inference latencies regardless of which caller constructs it.
        self._loop_detector = LoopDetector(
            max_consecutive=3,
            max_poll_window=600.0,
            max_poll_count=3,
            max_ping_pong_cycles=2
        )

        # Tool policy manager (fine-grained access control)
        self._policy_manager = get_policy_manager()
        self._policy_enabled = True  # Can be disabled for backward compatibility

        logger.info("ToolCoordinator initialized")

    def _ensure_serializable(self, data: Any) -> Any:
        """
        Helper to ensure all data in the result dictionary is JSON serializable.
        This respects the original 'stripping' logic's goal of preventing 
        Gemini API crashes with non-standard Python objects.
        """
        import datetime
        from enum import Enum
        
        if isinstance(data, dict):
            return {k: self._ensure_serializable(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._ensure_serializable(v) for v in data]
        elif isinstance(data, (datetime.datetime, datetime.date)):
            return data.isoformat()
        elif isinstance(data, Enum):
            return data.value
        return data

    def reset_turn(self) -> None:
        """Reset turn-level tracking for a new conversation turn."""
        self._tool_count_this_turn = 0
        self._processed_tool_ids.clear()
        self._loop_detector.reset_turn()
        logger.debug("[ TOOL_COORDINATOR ] Turn tracking reset")

    def reset_session(self) -> None:
        """Reset session-level tracking (prerequisites, etc.)."""
        self.reset_turn()
        self._tools_called_this_session.clear()
        if hasattr(self.executor, 'reset_session_tracking'):
            self.executor.reset_session_tracking()
        logger.info("[ TOOL_COORDINATOR ] Session tracking reset")

    def get_tool_count(self) -> int:
        """Get current tool count for this turn."""
        return self._tool_count_this_turn

    def set_escalation_threshold(self, threshold: int) -> None:
        """Update the soft limit threshold for auto-escalation."""
        self._escalation_threshold = threshold
        logger.info(f"[ TOOL_COORDINATOR ] Escalation threshold set to {threshold}")

    async def execute_single(
        self,
        tool_call: Dict[str, Any],
        active_model: Optional[Any] = None,
        context: Optional[RequestContext] = None
    ) -> ToolExecutionResult:
        """
        Execute a single tool call.

        Args:
            tool_call: Tool call dictionary with id, name, params
            active_model: Active model instance (for special handling)

        Returns:
            ToolExecutionResult with execution details
        """
        results = await self.execute_batch([tool_call], active_model, context=context)
        return results[0] if results else ToolExecutionResult(
            success=False,
            result=None,
            tool_name=tool_call.get('name', 'unknown'),
            tool_id=tool_call.get('id'),
            error="Batch execution returned empty results"
        )

    async def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        active_model: Optional[Any] = None,
        send_to_model: Optional[Callable] = None,
        context: Optional[RequestContext] = None
    ) -> List[ToolExecutionResult]:
        """
        Execute a batch of tool calls with limit enforcement and auto-escalation.

        Args:
            tool_calls: List of tool call dictionaries
            active_model: Active model instance (for persistent sessions)
            send_to_model: Callback to send tool responses to model

        Returns:
            List of ToolExecutionResult objects
        """
        if not tool_calls:
            return []

        batch_size = len(tool_calls)
        logger.info(f"[ TOOL_COORDINATOR ] Executing batch of {batch_size} tools")

        # ====================================================================
        # DEDUPLICATION - Remove tools already processed this turn
        # ====================================================================
        unique_calls = []
        for call in tool_calls:
            call_id = call.get('id', 'unknown')
            if call_id not in self._processed_tool_ids:
                self._processed_tool_ids.add(call_id)
                unique_calls.append(call)
            else:
                logger.warning(f"[ TOOL_COORDINATOR ] Duplicate tool ID detected: {call_id}")

        if not unique_calls:
            logger.info("[ TOOL_COORDINATOR ] All tools in batch already processed")
            return []

        # ====================================================================
        # IDEMPOTENCY CHECK - Return cached results for side-effecting tools
        # ====================================================================
        final_calls = []
        cached_results = []

        for call in unique_calls:
            tool_name = call.get('name', 'unknown')
            tool_params = call.get('params', {})

            # Check if tool is side-effecting (default: True for safety)
            tool_instance = self.tools.get_tool(tool_name)
            is_side_effecting = True
            if tool_instance:
                is_side_effecting = getattr(tool_instance, 'is_side_effecting', True)

            # Check idempotency cache for side-effecting tools
            if is_side_effecting:
                should_skip, cached = self._idempotency_manager.should_skip(tool_name, tool_params)

                if should_skip and cached:
                    logger.info(
                        f"[ TOOL_COORDINATOR ] Using cached result for {tool_name} "
                        f"(prevented duplicate execution)"
                    )
                    cached_results.append(ToolExecutionResult(
                        success=cached.success,
                        result=cached.result,
                        tool_name=tool_name,
                        tool_id=call.get('id'),
                        error=None if cached.success else "Cached failure"
                    ))
                    continue

            # Tool not cached or not side-effecting - will execute
            final_calls.append(call)

        # If all tools were cached, return cached results immediately
        if not final_calls:
            logger.info("[ TOOL_COORDINATOR ] All tools returned from cache")
            return cached_results

        # ====================================================================
        # LOOP DETECTION - Prevent runaway tool execution loops
        # ====================================================================
        loop_detected_calls = []
        loop_free_calls = []

        for call in final_calls:
            tool_name = call.get('name', 'unknown')
            tool_params = call.get('params', {})

            # Check for loops
            is_loop, loop_type = self._loop_detector.check_loop(tool_name, tool_params)

            if is_loop:
                logger.warning(f"[ TOOL_COORDINATOR ] Loop detected for {tool_name}: {loop_type}")
                loop_detected_calls.append((call, loop_type))
            else:
                loop_free_calls.append(call)

        # Create error results for loop-detected tools
        loop_results = [
            ToolExecutionResult(
                success=False,
                result=loop_type,
                tool_name=call.get('name', 'unknown'),
                tool_id=call.get('id'),
                error="Loop detected"
            )
            for call, loop_type in loop_detected_calls
        ]

        # If all tools triggered loop detection, return immediately
        if not loop_free_calls:
            logger.warning("[ TOOL_COORDINATOR ] All tools blocked by loop detection")
            return cached_results + loop_results

        # Update final_calls to only include loop-free calls
        final_calls = loop_free_calls

        # ====================================================================
        # POLICY CHECK - Enforce tool access policies
        # ====================================================================
        policy_blocked_calls = []
        policy_allowed_calls = []

        if self._policy_enabled:
            active_profile = self._policy_manager.get_active_profile()

            for call in final_calls:
                tool_name = call.get('name', 'unknown')

                # Check if tool is allowed under current policy
                if not self._policy_manager.is_allowed(tool_name):
                    logger.warning(
                        f"[ TOOL_COORDINATOR ] Tool '{tool_name}' blocked by policy profile '{active_profile}'"
                    )
                    policy_blocked_calls.append(call)
                else:
                    policy_allowed_calls.append(call)
        else:
            # Policy disabled - allow all tools
            policy_allowed_calls = final_calls

        # Create error results for policy-blocked tools
        policy_results = [
            ToolExecutionResult(
                success=False,
                result=f"Tool blocked by policy profile '{self._policy_manager.get_active_profile()}'",
                tool_name=call.get('name', 'unknown'),
                tool_id=call.get('id'),
                error="Policy violation"
            )
            for call in policy_blocked_calls
        ]

        # If all tools were blocked by policy, return immediately
        if not policy_allowed_calls:
            logger.warning("[ TOOL_COORDINATOR ] All tools blocked by policy")
            return cached_results + loop_results + policy_results

        # Update final_calls to only include policy-allowed calls
        final_calls = policy_allowed_calls

        # ====================================================================
        # PREREQUISITE CHECK - Ensure required tools are called first
        # ====================================================================
        prereq_blocked_calls = []
        prereq_allowed_calls = []

        for call in final_calls:
            tool_name = call.get('name', 'unknown')
            if tool_name in TOOL_PREREQUISITES:
                required_tools = TOOL_PREREQUISITES[tool_name]
                if not any(req in self._tools_called_this_session for req in required_tools):
                    error_msg = (
                        f"SAFETY BLOCK: Cannot execute '{tool_name}' without first calling one of: {required_tools}. "
                        f"You must use 'propose_file_plan' to create a plan and get user approval before modifying files."
                    )
                    logger.warning(f"[ TOOL_COORDINATOR ] Prerequisite check failed: {error_msg}")
                    prereq_blocked_calls.append((call, error_msg))
                    continue
            prereq_allowed_calls.append(call)

        # Create error results for prerequisite-blocked tools
        prereq_results = [
            ToolExecutionResult(
                success=False,
                result=error_msg,
                tool_name=call.get('name', 'unknown'),
                tool_id=call.get('id'),
                error="Prerequisite safety check failed"
            )
            for call, error_msg in prereq_blocked_calls
        ]

        # If all tools were blocked by prerequisites, return immediately
        if not prereq_allowed_calls:
            logger.warning("[ TOOL_COORDINATOR ] All tools blocked by prerequisite check")
            return cached_results + loop_results + policy_results + prereq_results

        # Update final_calls to only include prerequisite-allowed calls
        final_calls = prereq_allowed_calls

        batch_size = len(final_calls)
        self._tool_count_this_turn += batch_size

        # ====================================================================
        # HARD LIMIT - Refuse execution if over maximum
        # ====================================================================
        if self._tool_count_this_turn > MAX_TOOLS_PER_TURN:
            logger.error(
                f"[ TOOL_COORDINATOR ] Hard limit reached "
                f"({self._tool_count_this_turn} > {MAX_TOOLS_PER_TURN}). "
                f"Rejecting batch."
            )

            rejection_msg = (
                f"HARD LIMIT REACHED: Cannot execute more than {MAX_TOOLS_PER_TURN} tools in one turn. "
                f"You have already executed {self._tool_count_this_turn} tools. "
                f"Please provide your final response without using more tools."
            )

            # Create rejection results for all tools
            results = [
                ToolExecutionResult(
                    success=False,
                    result=rejection_msg,
                    tool_name=call.get('name', 'unknown'),
                    tool_id=call.get('id'),
                    error="Tool limit exceeded"
                )
                for call in final_calls
            ]

            # Combine with cached, loop, and policy results
            all_results = cached_results + loop_results + policy_results + results

            # Send rejection to model if possible
            if active_model and hasattr(active_model, 'session') and send_to_model:
                await send_to_model(final_calls, results)

            return all_results

        # ====================================================================
        # SOFT LIMIT - Check for user confirmation or auto-escalation
        # ====================================================================
        if (self._tool_count_this_turn >= self._escalation_threshold
            and self.confirmation_handler
            and self._escalation_threshold > 0):

            # Ask user for confirmation
            logger.warning(
                f"[ TOOL_COORDINATOR ] Soft limit reached "
                f"({self._tool_count_this_turn} >= {self._escalation_threshold}). "
                f"Requesting user confirmation."
            )

            should_proceed, new_total = await self.confirmation_handler(
                function_calls=final_calls,
                current_tool_count=self._tool_count_this_turn
            )

            self._tool_count_this_turn = new_total

            if not should_proceed:
                logger.info("[ TOOL_COORDINATOR ] User declined tool execution")
                results = [
                    ToolExecutionResult(
                        success=False,
                        result="User declined tool execution",
                        tool_name=call.get('name', 'unknown'),
                        tool_id=call.get('id'),
                        error="User declined"
                    )
                    for call in final_calls
                ]
                # Combine with cached, loop, and policy results
                return cached_results + loop_results + policy_results + results



        # ====================================================================
        # NORMAL EXECUTION - Execute tools in parallel
        # ====================================================================
        logger.info(f"[ TOOL_COORDINATOR ] Executing {batch_size} tools in parallel")

        # Track these tools as called in the session (for prerequisites check)
        for call in final_calls:
            self._tools_called_this_session.add(call.get('name', 'unknown'))

        # Create tasks for parallel execution
        tasks = []
        for tool_call in final_calls:
            task = asyncio.create_task(
                self.executor.execute_tool_call(tool_call, active_model, context)
            )
            self._active_tool_tasks.add(task)
            task.add_done_callback(lambda t: self._active_tool_tasks.discard(t))
            tasks.append(task)

        # Wait for all tools to complete
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert raw results to ToolExecutionResult objects
        results = []
        for i, raw_result in enumerate(raw_results):
            tool_call = final_calls[i]
            tool_name = tool_call.get('name', 'unknown')
            tool_id = tool_call.get('id')
            tool_params = tool_call.get('params', {})

            # Create result object
            if isinstance(raw_result, Exception):
                logger.error(
                    f"[ TOOL_COORDINATOR ] Tool '{tool_name}' raised exception: {raw_result}",
                    exc_info=raw_result
                )
                execution_result = ToolExecutionResult(
                    success=False,
                    result=None,
                    tool_name=tool_name,
                    tool_id=tool_id,
                    error=str(raw_result)
                )
            elif isinstance(raw_result, dict):
                # IMPROVEMENT: Preserve the full dictionary so AI can see all data keys (like 'emails')
                # We use _ensure_serializable to respect the historical need for safety.
                safe_result = self._ensure_serializable(raw_result)
                execution_result = ToolExecutionResult(
                    success=safe_result.get('success', False),
                    result=safe_result, # Store the full dict
                    tool_name=tool_name,
                    tool_id=tool_id,
                    error=safe_result.get('error'),
                    interrupted=safe_result.get('interrupted', False),
                    terminal=safe_result.get('terminal', False)
                )
                
                if not execution_result.success:
                    logger.error(f"[ TOOL_COORDINATOR ] Tool '{tool_name}' failed: {execution_result.error}")
            else:
                # Unexpected result type
                logger.warning(
                    f"[ TOOL_COORDINATOR ] Tool '{tool_name}' returned unexpected type: {type(raw_result)}"
                )
                execution_result = ToolExecutionResult(
                    success=True,
                    result=raw_result,
                    tool_name=tool_name,
                    tool_id=tool_id
                )

            results.append(execution_result)

            # Cache result if tool is side-effecting
            tool_instance = self.tools.get_tool(tool_name)
            is_side_effecting = True
            if tool_instance:
                is_side_effecting = getattr(tool_instance, 'is_side_effecting', True)

            if is_side_effecting:
                self._idempotency_manager.cache_result(
                    tool_name=tool_name,
                    params=tool_params,
                    result=execution_result.result,
                    success=execution_result.success
                )

            # Record execution in loop detector
            self._loop_detector.record_execution(tool_name, tool_params)

        # ====================================================================
        # SEND RESULTS TO MODEL - If using persistent session
        # ====================================================================
        if active_model and hasattr(active_model, 'session') and send_to_model:
            await send_to_model(final_calls, results)

        # Combine all results (cached + loop-detected + policy-blocked + prereq-blocked + executed)
        all_results = cached_results + loop_results + policy_results + prereq_results + results

        logger.info(
            f"[ TOOL_COORDINATOR ] Batch execution complete: "
            f"{len(results)} executed, {len(cached_results)} from cache, "
            f"{len(loop_results)} blocked by loop detection, "
            f"{len(policy_results)} blocked by policy, "
            f"{len(prereq_results)} blocked by prerequisites"
        )
        return all_results

    async def cancel_active_tools(self) -> None:
        """Cancel all currently executing tools."""
        if not self._active_tool_tasks:
            return

        logger.info(f"[ TOOL_COORDINATOR ] Cancelling {len(self._active_tool_tasks)} active tools")

        for task in list(self._active_tool_tasks):
            if not task.done():
                task.cancel()

        # Wait for cancellation with timeout
        if self._active_tool_tasks:
            await asyncio.wait(
                self._active_tool_tasks,
                timeout=2.0,
                return_when=asyncio.ALL_COMPLETED
            )

        self._active_tool_tasks.clear()
        logger.info("[ TOOL_COORDINATOR ] All tools cancelled")

    # ====================================================================
    # POLICY MANAGEMENT METHODS
    # ====================================================================

    def set_policy_profile(self, profile: str) -> None:
        """
        Change the active tool policy profile.

        Args:
            profile: Profile name (e.g., 'base', 'restricted', 'admin', 'work', 'personal')

        Raises:
            ValueError: If profile doesn't exist
        """
        self._policy_manager.set_active_profile(profile)
        logger.info(f"[ TOOL_COORDINATOR ] Policy profile changed to: {profile}")

    def get_policy_profile(self) -> str:
        """Get the currently active policy profile."""
        return self._policy_manager.get_active_profile()

    def list_policy_profiles(self) -> List[str]:
        """List all available policy profiles."""
        return self._policy_manager.list_profiles()

    def enable_policy(self) -> None:
        """Enable tool policy enforcement."""
        self._policy_enabled = True
        logger.info("[ TOOL_COORDINATOR ] Policy enforcement enabled")

    def disable_policy(self) -> None:
        """Disable tool policy enforcement (allows all tools)."""
        self._policy_enabled = False
        logger.warning("[ TOOL_COORDINATOR ] Policy enforcement disabled - all tools allowed")

    def is_policy_enabled(self) -> bool:
        """Check if policy enforcement is enabled."""
        return self._policy_enabled
