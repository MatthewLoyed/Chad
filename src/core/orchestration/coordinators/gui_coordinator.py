"""
GUI Coordinator - Bridging between SessionManager and the GUI/Overlay.

This coordinator handles notifications, overlay-specific status updates, 
and other UI-bridging logic that doesn't belong in the core orchestration.
"""

import logging
from typing import Optional

logger = logging.getLogger("GUICoordinator")

class GUICoordinator:
    """
    Handles GUI-related coordination tasks.
    """
    
    def __init__(self, overlay: Optional[object] = None):
        """
        Initialize the GUI coordinator.
        
        Args:
            overlay: The overlay manager instance (if available).
        """
        self.overlay = overlay
        logger.info("[ GUI ] : GUICoordinator initialized.")
    
    def set_overlay(self, overlay: object) -> None:
        """Update the overlay manager."""
        self.overlay = overlay
        
    def show_notification(self, title: str, message: str, icon: str = "⚠", duration: int = 10) -> None:
        """
        Show an OS-level holographic notification.
        
        Args:
            title: Notification title
            message: Notification message
            icon: Icon to display (emoji or symbol)
            duration: How long to show the notification in seconds
        """
        if hasattr(self, 'overlay') and self.overlay:
            try:
                self.overlay.show_notification(title, message, icon=icon, duration=duration)
            except Exception as e:
                logger.error(f"Failed to show notification: {e}")
        else:
            logger.warning(f"Notification requested but overlay not initialized: [{title}] {message}")
