"""
🖱️ Click / Move / Drag Tools — mouse input primitives.
Delegates OS-level pointer injection to the Hands Daemon over RPC.

These are the "muscle" for the computer_use_agent: they execute EXACT
coordinates. Grounding (deciding *where* to click) lives in the UIA / Set-of-Mark
layer — these tools are intentionally dumb.
"""

from typing import Any, Dict, Optional

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger
from src.remote.daemon_bridge import DaemonExecutorBridge

logger = setup_logger("ClickTool")


class ClickTool(BaseTool):
    """Move the cursor to a coordinate and (optionally) click."""

    name = "click"
    description = (
        "Click the mouse at exact screen coordinates (x, y). Use click_type to pick "
        "left/right/double click, or 'move' to only reposition the cursor without clicking. "
        "If target_window is given, coordinates are relative to that window's top-left corner. "
        "Coordinates come from a Set-of-Mark / accessibility overlay — do not guess them."
    )
    category = "os"
    is_side_effecting = True

    _ACTION_MAP = {
        "left": "click",
        "right": "right_click",
        "double": "double_click",
        "move": "move",
    }

    def format_action(self, params: Dict[str, Any]) -> str:
        click_type = str(params.get("click_type") or "left").lower()
        x, y = params.get("x"), params.get("y")
        where = f"({x}, {y})" if x is not None and y is not None else "target"
        verb = {
            "left": "Clicking",
            "right": "Right-clicking",
            "double": "Double-clicking",
            "move": "Moving cursor to",
        }.get(click_type, "Clicking")
        return f"{verb} {where}"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Horizontal coordinate (pixels)."},
                    "y": {"type": "integer", "description": "Vertical coordinate (pixels)."},
                    "click_type": {
                        "type": "string",
                        "enum": ["left", "right", "double", "move"],
                        "description": "Type of click. 'move' only repositions the cursor. Default 'left'.",
                    },
                    "target_window": {
                        "type": "string",
                        "description": "Optional: window title keyword; coordinates become relative to that window.",
                    },
                },
                "required": ["x", "y"],
            },
        }

    async def execute(
        self,
        x: int,
        y: int,
        click_type: str = "left",
        target_window: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        action = self._ACTION_MAP.get(str(click_type).lower(), "click")
        logger.info(f"[ CLICK ] : Delegating {action} at ({x}, {y}) to Hands Daemon.")
        try:
            bridge = DaemonExecutorBridge()
            res = await bridge.execute_primitive(
                action=action,
                payload={"x": x, "y": y, "target_window": target_window},
                timeout=5.0,
            )
            return {"success": True, "result": res, "caption": self.format_action({"click_type": click_type, "x": x, "y": y})}
        except Exception as e:
            logger.error(f"[ CLICK ERROR ] : Remote automation failed: {e}")
            return {"success": False, "error": f"Remote click execution error: {str(e)}"}


class DragTool(BaseTool):
    """Press-drag-release the mouse from one coordinate to another."""

    name = "drag"
    description = (
        "Drag the mouse from (from_x, from_y) to (to_x, to_y) with the left button held. "
        "Use for sliders, drag-and-drop, or text selection. If target_window is given, "
        "both coordinate pairs are relative to that window's top-left corner."
    )
    category = "os"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        fx, fy = params.get("from_x"), params.get("from_y")
        tx, ty = params.get("to_x"), params.get("to_y")
        if None not in (fx, fy, tx, ty):
            return f"Dragging ({fx}, {fy}) → ({tx}, {ty})"
        return "Dragging"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "from_x": {"type": "integer", "description": "Start horizontal coordinate."},
                    "from_y": {"type": "integer", "description": "Start vertical coordinate."},
                    "to_x": {"type": "integer", "description": "End horizontal coordinate."},
                    "to_y": {"type": "integer", "description": "End vertical coordinate."},
                    "target_window": {
                        "type": "string",
                        "description": "Optional: window title keyword; coordinates become relative to that window.",
                    },
                },
                "required": ["from_x", "from_y", "to_x", "to_y"],
            },
        }

    async def execute(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        target_window: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        logger.info(f"[ DRAG ] : Delegating drag ({from_x},{from_y})->({to_x},{to_y}) to Hands Daemon.")
        try:
            bridge = DaemonExecutorBridge()
            res = await bridge.execute_primitive(
                action="drag",
                payload={
                    "from_x": from_x,
                    "from_y": from_y,
                    "to_x": to_x,
                    "to_y": to_y,
                    "target_window": target_window,
                },
                timeout=6.0,
            )
            return {
                "success": True,
                "result": res,
                "caption": self.format_action({"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}),
            }
        except Exception as e:
            logger.error(f"[ DRAG ERROR ] : Remote automation failed: {e}")
            return {"success": False, "error": f"Remote drag execution error: {str(e)}"}
