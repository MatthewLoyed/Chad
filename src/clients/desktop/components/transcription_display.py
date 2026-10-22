"""
Transcription Display Component
Real-time transcript display showing partial and final user transcripts.
"""

import customtkinter as ctk
from src.clients.desktop.styles import COLORS, FONTS, get_glass_frame_config


class TranscriptionDisplay(ctk.CTkFrame):
    """
    Real-time transcription display widget.
    Shows partial transcripts as user speaks, updates in real-time.
    """
    
    def __init__(self, master, width=None, height=80):
        """
        Initialize transcription display.
        
        Args:
            master: Parent widget
            width: Widget width (None = fill parent)
            height: Widget height
        """
        glass_config = get_glass_frame_config()
        # Don't pass width if None - let it fill parent
        if width is not None:
            glass_config['width'] = width
        glass_config['height'] = height
        super().__init__(master, **glass_config)
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # Label for transcript text - starts empty (status bar shows state)
        self.transcript_label = ctk.CTkLabel(
            self,
            text="",
            font=FONTS['monospace'],
            text_color=COLORS['accent_main'],
            wraplength=800,
            justify="left"
        )
        self.transcript_label.grid(row=0, column=0, sticky="w", padx=20, pady=10)
        
        self.current_text = ""
    
    def update_transcript(self, text: str, is_partial: bool = False) -> None:
        """
        Update the displayed transcript.
        
        Args:
            text: Transcript text
            is_partial: Whether this is a partial (still updating) transcript
        """
        self.current_text = text
        
        if text:
            # Add visual indicator for partial transcripts
            display_text = text
            if is_partial:
                display_text += "..."  # Visual cue that it's still updating
            
            self.transcript_label.configure(text=display_text)
        else:
            # Empty string when no transcript (status bar handles state indication)
            self.transcript_label.configure(text="")
    
    def clear(self) -> None:
        """Clear the transcript display."""
        self.current_text = ""
        self.transcript_label.configure(text="")

