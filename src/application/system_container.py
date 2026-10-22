"""
System Container
Defines the data carrier for fully initialized core assistant components.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Any, Dict

@dataclass
class SystemContainer:
    """
    Aggregates constructed singletons for the Assistant runtime.
    Prevents passing 10 separate arguments to high-level constructors.
    """
    event_bus: Any
    tool_registry: Any
    memory_engine: Any
    model_router: Any
    adapter: Any
    
    # Synchronization Event for MCP warmup
    mcp_ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    
    # Optional/Asynchronous fields populated during later boot phases
    session_manager: Optional[Any] = None
    mcp_registry: Optional[Any] = None
    skill_loader: Optional[Any] = None
    
    # Newly lifted software orchestrators
    text_mode_client: Optional[Any] = None
    tool_executor: Optional[Any] = None
    consolidation_manager: Optional[Any] = None
    turn_coordinator: Optional[Any] = None
    
    def __post_init__(self):
        pass
