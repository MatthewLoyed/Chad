"""
Daemon Protocol Definitions
Shared schemas for RPC communication between The Brain (Server) and The Hands (OS Daemon).
"""

import uuid
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

# Defined set of recognized local OS operations
class DaemonAction:
    TYPE_TEXT = "type_text"
    CAPTURE_SCREEN = "capture_screen"
    RESIZE_WINDOW = "resize_window"
    SWITCH_WINDOW = "switch_window"
    MOVE_WINDOW = "move_window"
    SET_VOLUME = "set_volume"
    SET_BRIGHTNESS = "set_brightness"
    RUN_POWERSHELL = "run_ps"
    SCROLL = "scroll"
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    MOVE = "move"
    DRAG = "drag"
    CHECK_APP_EXISTS = "check_app_exists"
    LAUNCH_APP = "launch_app"
    PING = "ping"


@dataclass
class DaemonRequest:
    """Container for command requests sent from Brain to Daemon."""
    action: str
    payload: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout: float = 5.0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DaemonRequest':
        return cls(
            action=data["action"],
            payload=data.get("payload", {}),
            request_id=data.get("request_id", str(uuid.uuid4())),
            timeout=float(data.get("timeout", 5.0))
        )


@dataclass
class DaemonResponse:
    """Container for execution results sent from Daemon back to Brain."""
    request_id: str
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DaemonResponse':
        return cls(
            request_id=data["request_id"],
            success=bool(data["success"]),
            result=data.get("result"),
            error=data.get("error")
        )
