"""Screen capture for buddy observer — delegates to Hands daemon."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from src.remote.daemon_bridge import DaemonExecutorBridge, DaemonOfflineError
from src.shared.logger import setup_logger

logger = setup_logger("BuddyCapture")


@dataclass(frozen=True)
class ScreenGrab:
    image_b64: str
    width: int
    height: int
    source: str

    @property
    def image_bytes(self) -> bytes:
        return base64.b64decode(self.image_b64)


async def capture_desktop(*, all_screens: bool = True) -> ScreenGrab:
    """Capture the desktop via the Hands daemon (no duplicate ImageGrab here)."""
    bridge = DaemonExecutorBridge()
    if not bridge.is_ready:
        raise DaemonOfflineError("Hands daemon offline — cannot capture screen for buddy.")

    payload: dict[str, Any] = {"all_screens": all_screens}
    res = await bridge.execute_primitive(
        action="capture_screen",
        payload=payload,
        timeout=12.0,
    )
    return ScreenGrab(
        image_b64=res["image_b64"],
        width=int(res["width"]),
        height=int(res["height"]),
        source=str(res.get("source", "screen")),
    )
