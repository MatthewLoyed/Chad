"""
Domain Models for AI Assistant

Core business entities that represent the domain logic.
These models are framework-agnostic and focus on business rules.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum



class TaskStatus(Enum):
    """Status of an agent task"""
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class TaskTrigger(Enum):
    """What triggered a task"""
    USER = "user"           # User-initiated
    MANUAL = "manual"       # Manually triggered (one-time)
    CRON = "cron"          # Scheduled task (cron expression)
    AT = "at"              # One-time task at specific datetime (ISO 8601)
    EVERY = "every"        # Interval-based task (e.g., "30m", "2h", "1d")
    HEARTBEAT = "heartbeat"  # Periodic health check
    EVENT = "event"        # Event-driven trigger
    AUTONOMOUS = "autonomous"  # AI-initiated



@dataclass
class ToolExecution:
    """
    Represents the execution of a tool.

    Attributes:
        tool_name: Name of the tool
        args: Arguments passed to the tool
        result: Result returned by the tool
        success: Whether execution succeeded
        duration: Execution time in seconds
        timestamp: When execution started
        trigger_source: What triggered this execution (user, cron, etc.)
        error: Error message if failed
        metadata: Additional metadata (category, prerequisites, etc.)
    """
    tool_name: str
    args: Dict[str, Any]
    result: Any
    success: bool
    duration: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    trigger_source: TaskTrigger = TaskTrigger.USER
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'tool_name': self.tool_name,
            'args': self.args,
            'result': self.result,
            'success': self.success,
            'duration': self.duration,
            'timestamp': self.timestamp.isoformat(),
            'trigger_source': self.trigger_source.value,
            'error': self.error,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ToolExecution':
        """Create from dictionary"""
        return cls(
            tool_name=data['tool_name'],
            args=data['args'],
            result=data['result'],
            success=data['success'],
            duration=data.get('duration', 0.0),
            timestamp=datetime.fromisoformat(data['timestamp']),
            trigger_source=TaskTrigger(data.get('trigger_source', 'user')),
            error=data.get('error'),
            metadata=data.get('metadata', {})
        )



@dataclass
class AgentTask:
    """
    Represents a scheduled or autonomous task for the agent.

    This model supports future features like:
    - Cron jobs (scheduled tasks)
    - Heartbeat monitoring (periodic checks)
    - Autonomous skills (AI-initiated actions)

    Attributes:
        task_id: Unique identifier for this task
        name: Human-readable task name
        description: What the task does
        trigger: What triggers this task (cron, heartbeat, event, etc.)
        schedule: Cron expression or schedule string (if applicable)
        status: Current status of the task
        tool_executions: Tools executed by this task
        created_at: When the task was created
        last_run: When the task last ran
        next_run: When the task will run next (if scheduled)
        metadata: Additional metadata (priority, retry policy, etc.)
    """
    task_id: str
    name: str
    description: str
    trigger: TaskTrigger
    status: TaskStatus = TaskStatus.SCHEDULED
    schedule: Optional[str] = None
    tool_executions: List[ToolExecution] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def mark_running(self) -> None:
        """Mark task as currently running"""
        self.status = TaskStatus.RUNNING
        self.last_run = datetime.now()

    def mark_completed(self) -> None:
        """Mark task as completed"""
        self.status = TaskStatus.COMPLETED

    def mark_failed(self, error: str) -> None:
        """Mark task as failed"""
        self.status = TaskStatus.FAILED
        self.metadata['error'] = error

    def add_tool_execution(self, execution: ToolExecution) -> None:
        """Add a tool execution to this task"""
        self.tool_executions.append(execution)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'task_id': self.task_id,
            'name': self.name,
            'description': self.description,
            'trigger': self.trigger.value,
            'status': self.status.value,
            'schedule': self.schedule,
            'tool_executions': [te.to_dict() for te in self.tool_executions],
            'created_at': self.created_at.isoformat(),
            'last_run': self.last_run.isoformat() if self.last_run else None,
            'next_run': self.next_run.isoformat() if self.next_run else None,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentTask':
        """Create from dictionary"""
        return cls(
            task_id=data['task_id'],
            name=data['name'],
            description=data['description'],
            trigger=TaskTrigger(data['trigger']),
            status=TaskStatus(data['status']),
            schedule=data.get('schedule'),
            tool_executions=[ToolExecution.from_dict(te) for te in data.get('tool_executions', [])],
            created_at=datetime.fromisoformat(data['created_at']),
            last_run=datetime.fromisoformat(data['last_run']) if data.get('last_run') else None,
            next_run=datetime.fromisoformat(data['next_run']) if data.get('next_run') else None,
            metadata=data.get('metadata', {})
        )
