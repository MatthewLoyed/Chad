"""
Remote Communication Protocol for Chad
Defines the messaging structure and types for Client-Server (Brain-Limb) interaction.
"""

import json
import uuid
import time
from enum import Enum
from typing import Any, Dict, Optional, Union

class MessageType(Enum):
    # Handshake & Maintenance
    AUTH = "auth"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    SYSTEM_ERROR = "system_error"
    
    # Core Logic
    USER_INPUT = "user_input"
    AI_RESPONSE = "ai_response"
    AI_THOUGHT = "ai_thought"
    
    # Tool RPC (Remote Procedure Call)
    TOOL_REQUEST = "tool_request"
    TOOL_RESPONSE = "tool_response"
    AUTONOMOUS_NOTIFICATION = "autonomous_notification"
    
    # Configuration Sync
    SET_MODEL_CONFIG = "set_model_config"
    
    # Telemetry
    TOKEN_USAGE = "token_usage"

    # Planning
    EXECUTION_PLAN = "execution_plan"

    # Voice mode: Limb tells the Brain it's listening for a command (drives the buddy)
    VOICE_STATE = "voice_state"

    # Fast-track: Limb invokes an allowlisted tool directly, skipping the LLM/planner.
    FAST_TRACK_INVOKE = "fast_track_invoke"

    # Workflows: Limb triggers a saved multi-step tool sequence, skipping the LLM/planner.
    WORKFLOW_RUN = "workflow_run"

    # Generic human-in-the-loop tool approval (src/core/orchestration/approval_store.py).
    # Brain -> Limb: a requires_approval tool is pausing for a decision.
    TOOL_APPROVAL_REQUEST = "tool_approval_request"
    # Limb -> Brain: the user resolved a pending approval (accept/decline/choice).
    TOOL_APPROVAL_RESPONSE = "tool_approval_response"

    # Brain -> Limb: resolved turn lane after router (or manual) routing
    TURN_MODE = "turn_mode"

    # Limb -> Brain: user selected a UI turn mode (auto/agent/chat/plan) — drives aux model preload
    SET_UI_TURN_MODE = "set_ui_turn_mode"

    # Brain -> Limb: a new capability suggestion was raised by the self-improvement
    # loop (src/core/intelligence/capability_gaps.py). Rendered in the side panel,
    # never the chat stream.
    CAPABILITY_SUGGESTION = "capability_suggestion"

class ChadMessage:
    """
    Standard message structure for all WebSocket communication.
    """
    def __init__(
        self, 
        msg_type: Union[MessageType, str], 
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        auth_token: Optional[str] = None
    ):
        self.id = correlation_id or str(uuid.uuid4())[:8]
        self.type = msg_type if isinstance(msg_type, str) else msg_type.value
        self.payload = payload or {}
        self.auth_token = auth_token
        self.timestamp = time.time()

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps({
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "auth_token": self.auth_token,
            "timestamp": self.timestamp
        })

    @classmethod
    def from_json(cls, json_str: str) -> 'ChadMessage':
        """Deserialize message from JSON string."""
        data = json.loads(json_str)
        return cls(
            msg_type=data.get("type"),
            payload=data.get("payload"),
            correlation_id=data.get("id"),
            auth_token=data.get("auth_token")
        )

    def __repr__(self):
        return f"<ChadMessage(id={self.id}, type={self.type})>"


class _StarletteWSAdapter:
    """Makes a Starlette WebSocket look like a websockets connection for ChadServer.handler."""
    __slots__ = ("_ws", "_client", "_closed")

    def __init__(self, ws: "WebSocket"):
        self._ws = ws
        self._client = ws.scope.get("client") or ("unknown", 0)
        self._closed = False

    @property
    def remote_address(self):
        return self._client

    async def send(self, message: str):
        if self._closed:
            return
        await self._ws.send_text(message)

    async def close(self):
        self._closed = True
        await self._ws.close()

    def __aiter__(self):
        return self._receive_iter()

    async def _receive_iter(self):
        while not self._closed:
            data = await self._ws.receive()
            if data["type"] == "websocket.disconnect":
                break
            if data["type"] == "websocket.receive":
                text = data.get("text")
                if text is not None:
                    yield text
                else:
                    b = data.get("bytes")
                    if b is not None:
                        yield b.decode("utf-8", errors="replace")
