"""
Timeout Progress Bar Component
Neon progress bar showing remaining timeout before session ends.
"""

import customtkinter as ctk
from src.clients.desktop.styles import COLORS, FONTS


class TimeoutProgressBar(ctk.CTkFrame):
    """
    Thin horizontal progress bar showing timeout countdown.
    Shrinks from 100% to 0% over timeout duration.
    """
    
    def __init__(self, master, timeout_seconds: float = 10.0, height: int = 4):
        """
        Initialize timeout progress bar.
        
        Args:
            master: Parent widget
            timeout_seconds: Total timeout duration in seconds
            height: Height of the progress bar in pixels
        """
        super().__init__(master, fg_color="transparent", height=height)
        
        self.timeout_seconds = timeout_seconds
        self.height = height
        self.is_active = False
        self.start_time = None
        self.current_progress = 1.0  # 1.0 = 100%, 0.0 = 0%
        
        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        
        # Progress bar (using CTkProgressBar with custom styling)
        self.progress_bar = ctk.CTkProgressBar(
            self,
            height=height,
            progress_color=COLORS['accent_main'],  # Neon cyan
            fg_color=COLORS['bg_secondary'],
            corner_radius=0
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(1.0)  # Start at 100%
        
        # Hide initially
        self.grid_remove()
    
    def start(self) -> None:
        """Start the progress bar countdown."""
        self.is_active = True
        self.start_time = None  # Will be set on first update
        self.current_progress = 1.0
        self.progress_bar.set(1.0)
        self.grid()  # Show the progress bar
        self.update_progress()
    
    def stop(self) -> None:
        """Stop the progress bar and hide it."""
        self.is_active = False
        self.grid_remove()  # Hide the progress bar
    
    def reset(self) -> None:
        """Reset the progress bar to 100%."""
        if self.is_active:
            self.start_time = None  # Reset timer
            self.current_progress = 1.0
            self.progress_bar.set(1.0)
    
    def update_progress(self) -> None:
        """Update progress bar based on elapsed time."""
        if not self.is_active:
            return
        
        import time
        
        # Initialize start time on first call
        if self.start_time is None:
            self.start_time = time.time()
        
        # Calculate elapsed time
        elapsed = time.time() - self.start_time
        
        # Calculate progress (1.0 = start, 0.0 = timeout reached)
        self.current_progress = max(0.0, 1.0 - (elapsed / self.timeout_seconds))
        
        # Update progress bar
        self.progress_bar.set(self.current_progress)
        
        # If timeout reached, don't schedule more updates
        if self.current_progress <= 0.0:
            self.is_active = False
            return
        
        # Schedule next update (30 FPS for smooth animation)
        self.after(33, self.update_progress)

















