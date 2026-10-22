"""
Action History Component
Scrollable list of action cards showing command/response pairs with timestamps.
"""

import customtkinter as ctk
import webbrowser
from src.clients.desktop.styles import COLORS, FONTS, get_glass_frame_config
from datetime import datetime
from typing import Optional, List, Dict


class ActionCard(ctk.CTkFrame):
    """Individual action card showing a command/response pair with left border accent."""
    
    def __init__(self, master, timestamp: str, command: str, response: str, sources: Optional[List[Dict[str, str]]] = None, **kwargs):
        """
        Initialize action card.
        
        Args:
            master: Parent widget
            timestamp: Time string (HH:MM format)
            command: User command text
            response: Sudo's response/action text
            sources: Optional list of source dicts with 'url' and 'title' keys
        """
        # Use card config (left border accent style)
        from src.clients.desktop.styles import get_card_config
        card_config = get_card_config()
        super().__init__(master, **card_config, **kwargs)
        
        self.grid_columnconfigure(1, weight=1)  # Column 1 is content, column 0 is left border
        
        # Left border accent (3px solid cyan line)
        self.border_line = ctk.CTkFrame(
            self,
            fg_color=COLORS['border_card'],
            width=3
        )
        self.border_line.grid(row=0, column=0, sticky="ns", rowspan=10)  # Span all rows
        
        # Content frame (column 1)
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        content_frame.grid_columnconfigure(0, weight=1)
        
        # Response (action) - bold, shown first in new design
        wraplength = 260
        self.response_label = ctk.CTkLabel(
            content_frame,
            text=response,
            font=FONTS['primary_bold'],
            text_color=COLORS['text_primary'],
            wraplength=wraplength,
            justify="left",
            anchor="w"
        )
        self.response_label.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        
        # Description (command) - smaller, dimmed
        self.command_label = ctk.CTkLabel(
            content_frame,
            text=command,
            font=FONTS['small'],
            text_color=COLORS['text_secondary'],
            wraplength=wraplength,
            justify="left",
            anchor="w"
        )
        
        row = 1
        self.command_label.grid(row=row, column=0, sticky="ew", pady=(0, 5))
        row += 1
        
        # Sources section (if provided)
        self.sources_frame = None
        if sources and len(sources) > 0:
            self.sources_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
            self.sources_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            self.sources_frame.grid_columnconfigure(0, weight=1)
            
            for i, source in enumerate(sources):
                url = source.get('url', '')
                title = source.get('title', '')
                
                if url:
                    # Create clickable label for source
                    display_text = title if title else url
                    # Truncate long titles/URLs
                    if len(display_text) > 40:
                        display_text = display_text[:37] + "..."
                    
                    source_label = ctk.CTkLabel(
                        self.sources_frame,
                        text=f"🔗 {display_text}",
                        font=FONTS['small'],
                        text_color=COLORS['accent_dim'],
                        cursor="hand2",
                        wraplength=wraplength,
                        justify="left",
                        anchor="w"
                    )
                    
                    # Bind click event to open URL
                    def make_click_handler(url_to_open: str):
                        def handler(event):
                            webbrowser.open(url_to_open)
                        return handler
                    
                    source_label.bind("<Button-1>", make_click_handler(url))
                    source_label.grid(row=i, column=0, sticky="w", pady=(0, 3))
        
        # Store reference to content_frame for later use
        self.content_frame = content_frame


class ActionHistoryWidget(ctk.CTkFrame):
    """
    Scrollable action history panel.
    Displays list of action cards with command/response pairs.
    """
    
    def __init__(self, master, width=300, max_entries=30):
        """
        Initialize action history widget.
        
        Args:
            master: Parent widget
            width: Widget width
            max_entries: Maximum number of entries to keep (truncate oldest)
        """
        glass_config = get_glass_frame_config()
        glass_config['width'] = width
        super().__init__(master, **glass_config)
        
        self.max_entries = max_entries
        self.entries = []  # List of ActionCard widgets
        
        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # Title label (smaller, dimmed style)
        self.title_label = ctk.CTkLabel(
            self,
            text="ACTION HISTORY",
            font=('Segoe UI', 10),  # Smaller font (0.7rem equivalent)
            text_color=COLORS['text_dim']
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 15))
        
        # Scrollable frame for cards - expands to fill available space
        self.scrollable_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent"
        )
        self.scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.scrollable_frame.grid_columnconfigure(0, weight=1)
    
    def add_entry(self, command: str, response: str, sources: Optional[List[Dict[str, str]]] = None) -> None:
        """
        Add a new action entry.
        
        Args:
            command: User command text
            response: Sudo's response/action text
            sources: Optional list of source dicts with 'url' and 'title' keys
        """
        # Get current time
        timestamp = datetime.now().strftime("%H:%M")
        
        # Create card
        card = ActionCard(
            self.scrollable_frame,
            timestamp=timestamp,
            command=command,
            response=response,
            sources=sources
        )
        
        # Add to entries list
        self.entries.append(card)
        
        # Grid the card (pack from top)
        row = len(self.entries) - 1
        card.grid(row=row, column=0, sticky="ew", pady=(0, 15))  # More spacing between cards
        
        # Truncate if over max_entries
        if len(self.entries) > self.max_entries:
            # Remove oldest card
            oldest = self.entries.pop(0)
            oldest.destroy()
            
            # Re-grid remaining cards
            for i, entry in enumerate(self.entries):
                entry.grid(row=i, column=0, sticky="ew", pady=(0, 15))
        
        # Scroll to bottom (newest)
        self.scrollable_frame.update()
        self.scrollable_frame._parent_canvas.yview_moveto(1.0)
    
    def update_most_recent_entry_sources(self, sources: List[Dict[str, str]]) -> None:
        """
        Update the most recent entry with sources.
        
        Args:
            sources: List of source dicts with 'url' and 'title' keys
        """
        if not self.entries or not sources:
            return
        
        # Get the most recent entry (last in list)
        most_recent_card = self.entries[-1]
        
        # Check if card already has sources_frame attribute
        sources_frame = getattr(most_recent_card, 'sources_frame', None)
        
        # If sources frame doesn't exist, create it
        if not sources_frame:
            # Find content frame (column 1)
            content_frame = getattr(most_recent_card, 'content_frame', None)
            if not content_frame:
                # Find content frame by searching children
                for child in most_recent_card.winfo_children():
                    if isinstance(child, ctk.CTkFrame) and child.grid_info().get('column') == 1:
                        content_frame = child
                        most_recent_card.content_frame = content_frame
                        break
            
            if content_frame:
                # Find the last row in content_frame
                last_row = len([w for w in content_frame.grid_slaves() if w.grid_info().get('row') is not None])
                sources_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
                sources_frame.grid(row=last_row, column=0, sticky="ew", pady=(0, 10))
                sources_frame.grid_columnconfigure(0, weight=1)
                # Store reference in card
                most_recent_card.sources_frame = sources_frame
        
        # Clear existing sources and add new ones
        for widget in sources_frame.winfo_children():
            widget.destroy()
        
        # Add sources
        wraplength = 260
        for i, source in enumerate(sources):
            url = source.get('url', '')
            title = source.get('title', '')
            
            if url:
                display_text = title if title else url
                if len(display_text) > 40:
                    display_text = display_text[:37] + "..."
                
                source_label = ctk.CTkLabel(
                    sources_frame,
                    text=f"🔗 {display_text}",
                    font=FONTS['small'],
                    text_color=COLORS['accent_dim'],
                    cursor="hand2",
                    wraplength=wraplength,
                    justify="left",
                    anchor="w"
                )
                
                def make_click_handler(url_to_open: str):
                    def handler(event):
                        webbrowser.open(url_to_open)
                    return handler
                
                source_label.bind("<Button-1>", make_click_handler(url))
                source_label.grid(row=i, column=0, sticky="w", pady=(0, 3))
    
    def clear(self) -> None:
        """Clear all entries."""
        for entry in self.entries:
            entry.destroy()
        self.entries.clear()

