"""
Live Logs Component
Terminal-style log viewer for displaying filtered log messages in real-time.
"""

import customtkinter as ctk
from src.clients.desktop.styles import COLORS, FONTS, get_glass_frame_config
from typing import Optional
import logging


class LiveLogsWidget(ctk.CTkFrame):
    """
    Terminal-style log viewer widget.
    Displays filtered log messages with colored text and auto-scroll.
    """
    
    def __init__(self, master, width=350, max_lines=1000):
        """
        Initialize live logs widget.
        
        Args:
            master: Parent widget
            width: Widget width
            max_lines: Maximum number of log lines to keep (truncate oldest)
        """
        glass_config = get_glass_frame_config()
        glass_config['width'] = width
        super().__init__(master, **glass_config)
        
        self.max_lines = max_lines
        self.line_count = 0
        
        # Configure grid - row 1 (text widget) should expand
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # Title - fixed height
        self.grid_rowconfigure(1, weight=1)  # Text widget - expands
        
        # Title label (smaller, dimmed style - matches index.html)
        self.title_label = ctk.CTkLabel(
            self,
            text="LIVE LOGS",
            font=('Segoe UI', 10),  # Smaller font (0.7rem equivalent)
            text_color=COLORS['text_dim']
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 15))
        
        # Text widget with scrollbar - wrap is already set to "word" for natural wrapping
        self.text_widget = ctk.CTkTextbox(
            self,
            fg_color=COLORS['bg_primary'],
            text_color=COLORS['success'],  # Green text for logs (matches index.html)
            font=('Consolas', 12),  # Monospace font
            wrap="word"  # Word wrapping enabled
        )
        self.text_widget.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        
        # Configure text widget colors
        self.text_widget.configure(
            font=FONTS['monospace_small'],
            state="disabled"  # Make read-only
        )
    
    def add_log(self, message: str, level: str = "INFO") -> None:
        """
        Add a log message to the widget.
        
        Args:
            message: Log message text
            level: Log level (INFO, ERROR, WARNING, etc.)
        """
        # Enable text widget for editing
        self.text_widget.configure(state="normal")
        
        # Determine color based on level and message content
        color = self._get_color_for_message(message, level)
        
        # Remove ANSI color codes from message (clean for display)
        clean_message = self._strip_ansi_codes(message)
        
        # Add timestamp prefix
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {clean_message}\n"
        
        # Insert text with color tag
        self.text_widget.insert("end", formatted_message)
        
        # Apply color tag if needed
        if color != COLORS['text_primary']:
            # Find the range we just inserted
            start = f"end-{len(formatted_message)}c"
            end = "end-1c"
            # Create tag for this color if it doesn't exist
            tag_name = f"color_{color.replace('#', '')}"
            if tag_name not in self.text_widget.tag_names():
                self.text_widget.tag_config(tag_name, foreground=color)
            self.text_widget.tag_add(tag_name, start, end)
        
        # Increment line count
        self.line_count += 1
        
        # Truncate if over max_lines
        if self.line_count > self.max_lines:
            # Delete first line
            self.text_widget.delete("1.0", "2.0")
            self.line_count -= 1
        
        # Scroll to bottom
        self.text_widget.see("end")
        
        # Disable text widget (make read-only)
        self.text_widget.configure(state="disabled")
    
    def _get_color_for_message(self, message: str, level: str) -> str:
        """
        Determine text color based on message content and level.
        
        Args:
            message: Log message
            level: Log level
            
        Returns:
            Color hex code
        """
        # Check for specific prefixes (matches index.html - green for SUDO ACTION)
        if "SUDO ACTION" in message.upper() or "[SUDO ACTION]" in message or "[RAW TOOL CALL]" in message:
            return COLORS['success']  # Green (matches index.html)
        elif "[ERROR]" in message or level == "ERROR":
            return COLORS['error']  # Red
        elif "[WARNING]" in message or level == "WARNING":
            return COLORS['warning']  # Yellow
        elif "[THINKING]" in message or "[REASONING]" in message:
            return COLORS['accent_dim']  # Dim cyan
        elif "[SYSTEM]" in message:
            return COLORS['text_secondary']  # Gray
        elif "[SUCCESS]" in message:
            return COLORS['success']  # Green
        else:
            return COLORS['text_primary']  # Default white (for general logs)
    
    def _strip_ansi_codes(self, text: str) -> str:
        """
        Remove ANSI color codes from text.
        
        Args:
            text: Text potentially containing ANSI codes
            
        Returns:
            Clean text without ANSI codes
        """
        import re
        # Remove ANSI escape sequences
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)
    
    def clear(self) -> None:
        """Clear all log messages."""
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.configure(state="disabled")
        self.line_count = 0

