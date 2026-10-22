"""
Typed event definitions for the event bus.

These events replace the manual callback wiring throughout the application.
"""

from dataclasses import dataclass
from typing import Optional, Any, Dict, List
from .event_bus import Event


@dataclass
class SessionStarted(Event):
    """Fired when a conversation session starts."""
    session_id: Optional[str] = None


@dataclass
class SessionEnded(Event):
    """Fired when a conversation session ends."""
    session_id: Optional[str] = None


@dataclass
class ModelLoading(Event):
    """Fired when a model is being loaded."""
    model_name: str
    provider: Optional[str] = None
    delivery: Optional[str] = None


@dataclass
class StateChanged(Event):
    """Fired when the assistant state changes."""
    new_state: str
    old_state: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ToolStarted(Event):
    """Fired when a tool execution starts."""
    tool_name: str
    args: Dict[str, Any]
    correlation_id: Optional[str] = None
    display_text: Optional[str] = None
    parent_id: Optional[str] = None
    args_summary: Optional[str] = None
    server_name: Optional[str] = None
    turn_id: Optional[str] = None
    timestamp: Optional[float] = None
    delivery: Optional[str] = None


@dataclass
class ToolCompleted(Event):
    """Fired when a tool execution completes."""
    tool_name: str
    result: Any
    success: bool
    error: Optional[str] = None
    correlation_id: Optional[str] = None
    display_text: Optional[str] = None
    parent_id: Optional[str] = None
    server_name: Optional[str] = None
    turn_id: Optional[str] = None
    elapsed_ms: Optional[float] = None
    timestamp: Optional[float] = None
    delivery: Optional[str] = None
    awaiting_user: bool = False


@dataclass
class ThinkingUpdate(Event):
    """Fired when the AI is thinking (reasoning/planning)."""
    thinking_text: str
    thinking_type: Optional[str] = None  # 'reasoning', 'planning', etc.
    append: bool = True
    agent_name: Optional[str] = None
    parent_id: Optional[str] = None
    delivery: Optional[str] = None


@dataclass
class TurnStarted(Event):
    """Fired when a conversation turn starts (user input received)."""
    user_input: str
    turn_id: Optional[str] = None
    initiator: Optional[str] = None
    category: Optional[str] = None
    delivery: Optional[str] = None

@dataclass
class CronTaskTriggered(Event):
    """Fired when a cron task's time is reached."""
    task_id: str
    task_name: str
    description: str
    session_key: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class TurnCompleted(Event):
    """Fired when a conversation turn completes (AI response ready)."""
    turn_id: Optional[str] = None
    turn_context: Optional[Any] = None  # TurnContext from orchestrator
    duration: Optional[float] = None
    tool_count: int = 0
    model_response: Optional[str] = None


@dataclass
class ExecutionPlanEvent(Event):
    """Fired when a structured execution plan is generated or updated."""
    plan: List[Dict[str, Any]]
    plan_id: Optional[str] = None
    goal: Optional[str] = None
    turn_id: Optional[str] = None
    replace: bool = True
    gather_brief: Optional[str] = None


@dataclass
class ToolsMenuEvent(Event):
    """Fired when the per-turn tool menu is resolved — drives the wait-theater chip carousel."""
    tools: List[str]
    shortlist_size: Optional[int] = None
    selection_path: Optional[str] = None
    turn_id: Optional[str] = None
    delivery: Optional[str] = None
    replace: bool = True


@dataclass
class WaitNarratorEvent(Event):
    """Fired when the lightweight wait-theater narrator produces query-tailored lines."""
    lines: List[str]
    turn_id: Optional[str] = None
    delivery: Optional[str] = None


@dataclass
class AudioLevelChanged(Event):
    """Fired when audio input/output level changes."""
    level: float
    source: str  # 'input' or 'output'


@dataclass
class TranscriptionUpdate(Event):
    """Fired when speech transcription updates."""
    text: str
    is_final: bool


@dataclass
class ErrorOccurred(Event):
    """Fired when an error occurs."""
    error_message: str
    error_type: str
    source: Optional[str] = None
    recoverable: bool = True


@dataclass
class CaptionUpdate(Event):
    """Fired when the AI response caption updates."""
    text: str
    append: bool = False


@dataclass
class StatusUpdate(Event):
    """Fired when the status message updates.

    ``status`` is a short, user-facing phase label (e.g. "Selecting tools…",
    "Loading gemma…", "Thinking…") surfaced live in the chat working indicator
    so the pre-first-token gap doesn't look frozen. ``delivery`` lets the limb
    bridge gate it to GUI turns (autonomous turns must not leak status into the
    user's open chat). ``client_turn_id`` correlates the label to a pending
    voice turn (mirrors ``ResponseFragment.client_turn_id``) so it can be spoken
    as narration during a long escalate-to-agent wait."""
    status: str
    state: Optional[str] = None
    delivery: Optional[str] = None
    client_turn_id: Optional[str] = None


@dataclass
class ActionHistoryUpdate(Event):
    """Fired when an action is added to history."""
    action: Dict[str, Any]


@dataclass
class LogMessage(Event):
    """Fired when a log message should be displayed in UI."""
    message: str
    level: str  # 'info', 'warning', 'error', etc.


@dataclass
class ModelChanged(Event):
    """Fired when the AI model is switched."""
    old_model: Optional[str]
    new_model: str


@dataclass
class VoiceModeToggled(Event):
    """Fired when voice mode is enabled/disabled."""
    enabled: bool


@dataclass
class DeviceDetected(Event):
    """Fired when an audio device is detected."""
    device_name: str
    device_id: str


@dataclass
class DeviceSwitched(Event):
    """Fired when audio device is switched."""
    device_name: str
    device_type: str  # 'input' or 'output'


@dataclass
class ToolConfirmationRequired(Event):
    """Fired when a tool requires user confirmation."""
    tool_name: str
    args: Dict[str, Any]
    reason: str
    response_future: Any  # asyncio.Future


@dataclass
class ToolLimitConfirmationRequest(Event):
    """Fired when tool execution limit is exceeded and user confirmation is needed."""
    function_calls_count: int
    current_tool_count: int
    request_id: str  # Unique ID to match request with response


@dataclass
class ToolLimitConfirmationResponse(Event):
    """Fired when user responds to tool limit confirmation request."""
    request_id: str  # Matches the request ID
    approved: bool


@dataclass
class ConversationCleared(Event):
    """Fired when conversation history is cleared."""
    mode: str  # 'local' or 'cloud'


@dataclass
class ThinkingEvent(Event):
    """Fired when AI is thinking (processing)."""
    thinking_text: str


@dataclass
class ReasoningEvent(Event):
    """Fired when AI reasoning updates."""
    reasoning_text: str


@dataclass
class TranscriptEvent(Event):
    """Fired when transcription updates."""
    transcript: str
    is_final: bool = False


@dataclass
class GroundingMetadataEvent(Event):
    """Fired when grounding metadata is available."""
    metadata: Dict[str, Any]


@dataclass
class PlaySoundEvent(Event):
    """Fired when a sound should be played."""
    sound_name: str


@dataclass
class AudioLevelEvent(Event):
    """Fired when audio level changes."""
    level: float


@dataclass
class TurnStartEvent(Event):
    """Fired when a turn starts."""
    turn_id: Optional[str] = None


@dataclass
class TurnModeResolved(Event):
    """Fired when the turn lane is chosen (Gemma auto-router or manual UI mode)."""
    mode: str  # conversation | agent | plan
    source: str = "auto_router"  # auto_router | user_manual
    confidence: Optional[float] = None
    delivery: Optional[str] = None


@dataclass
class TurnCompleteEvent(Event):
    """Fired when a turn completes."""
    turn_id: Optional[str] = None
    response: Optional[str] = None
    # A turn can end PAUSED rather than finished: the planner proposed a plan and the
    # graph halted for approval (awaiting_approval), or a tool called pause_for_input
    # (awaiting_input). Carry it on the event so clients/evals can distinguish a pause
    # from a completion without reading last_turn_metrics (not reliably readable here).
    awaiting_approval: bool = False
    awaiting_input: bool = False
    delivery: Optional[str] = None
    client_turn_id: Optional[str] = None
    # Set when a tool short-circuited via the generic requires_approval gate
    # (src/core/orchestration/approval_store.py): {tool_name, token, decision_type,
    # prompt, options}. The Limb forwards this as a TOOL_APPROVAL_REQUEST message.
    pending_approval: Optional[dict] = None


@dataclass
class ResponseFragment(Event):
    """Fired when a new text token/fragment is generated."""
    text: str
    delivery: Optional[str] = None
    # Client-facing turn id (mirrors TurnCompleteEvent.client_turn_id) so a voice
    # turn can correlate streamed fragments back to the future it's awaiting
    # (see GUIChadLimb.run_voice_turn / epic #291 Phase 2).
    client_turn_id: Optional[str] = None

@dataclass
class TokenUsageUpdate(Event):
    """Fired when token usage metadata is available from an LLM call."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: Optional[int] = None
    context_window: Optional[int] = None
    speed: Optional[float] = None
    ttft: Optional[float] = None
    stop_reason: Optional[str] = None
    estimated_prompt_tokens: Optional[int] = None
    pre_model_breakdown: Optional[dict] = None


@dataclass
class AudioResponseReceived(Event):
    """Fired when raw AI voice audio data is received from the model."""
    audio_data: Any
    turn_id: Optional[str] = None
    mute_input: bool = False


@dataclass
class TaskStoppedByUser(Event):
    """Fired when the user stops/cancels the active task."""
    pass


@dataclass
class TurnFeedbackEvent(Event):
    """Fired when a user thumbs-up or thumbs-down an AI response."""
    session_id: str
    turn_id: str
    rating: str  # 'positive' | 'negative'


@dataclass
class MCPInstallRequested(Event):
    """Fired when Chad wants to guide the user through installing an MCP server."""
    server_id: str          # registry ID, e.g. 'io.github.user/brave-search'
    title: str              # human-readable name
    reason: Optional[str] = None  # why Chad is suggesting this


@dataclass
class CapabilitySuggestionRaised(Event):
    """Fired when Chad's self-improvement loop proposes a new tool or skill.

    Surfaced in the desktop "Suggestions" side panel (not the chat stream) so the
    panel can update live without a re-fetch.
    """
    suggestion_id: str
    kind: str                       # 'tool' | 'skill'
    title: str
    description: str = ""
    rationale: str = ""
    confidence: float = 0.0
    cluster_size: int = 1


@dataclass
class ReflectionLessonRaised(Event):
    """Fired when the heartbeat's reflection cycle surfaces an admitted lesson or an
    open question for review.

    Surfaced in the desktop "Reflection" side panel (not the chat stream), mirroring
    CapabilitySuggestionRaised. `status` is 'auto_applied' when reflection.
    auto_write_confidence_floor already wrote it — the panel shows it read-only.
    """
    lesson_id: str
    kind: str                       # 'lesson' | 'question'
    scope: str = ""                 # 'user' | 'tools' ('' for unanswered questions)
    text: str = ""
    why: str = ""                   # question's stated uncertainty
    evidence: str = ""
    confidence: float = 0.0
    status: str = "pending"


@dataclass
class DashboardEntryCreated(Event):
    """Fired when Chad appends a row to the dashboard feed (see dashboard_feed.py).

    Not yet consumed by the frontend (v1 polls via get_dashboard_feed) — published
    for parity with CapabilitySuggestionRaised so a future live-push doesn't need
    a second migration.
    """
    entry_id: str
    kind: str                       # 'thought' | 'life' | 'status' | 'discovery' | 'action'
    title: str
    body: str = ""
    metadata: Optional[dict] = None
    action: Optional[dict] = None


