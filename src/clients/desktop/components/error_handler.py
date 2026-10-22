"""
Error Handler Component
Displays clear, actionable error messages to the user.
"""

import customtkinter as ctk
from src.clients.desktop.styles import COLORS, FONTS

class ErrorDialog(ctk.CTkToplevel):
    """
    Modal dialog for critical errors.
    """
    def __init__(self, master, title, message, action_text="Retry", action_command=None):
        super().__init__(master)
        
        self.title("System Error")
        self.geometry("500x300")
        self.resizable(False, False)
        
        # Make modal
        self.transient(master)
        self.grab_set()
        
        self.configure(fg_color=COLORS['bg_secondary'])
        
        # Icon
        self.icon_label = ctk.CTkLabel(
            self,
            text="⚠️",
            font=("Segoe UI", 48),
            text_color=COLORS['error']
        )
        self.icon_label.pack(pady=(30, 10))
        
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=FONTS['header'],
            text_color=COLORS['error']
        )
        self.title_label.pack(pady=5)
        
        # Message
        self.msg_label = ctk.CTkLabel(
            self,
            text=message,
            font=FONTS['primary'],
            text_color=COLORS['text_primary'],
            wraplength=400
        )
        self.msg_label.pack(pady=10)
        
        # Buttons
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(pady=30)
        
        if action_command:
            self.action_btn = ctk.CTkButton(
                self.btn_frame,
                text=action_text,
                command=lambda: [action_command(), self.destroy()],
                fg_color=COLORS['error'],
                hover_color='#ff6688',
                width=120
            )
            self.action_btn.pack(side="left", padx=10)
            
        self.close_btn = ctk.CTkButton(
            self.btn_frame,
            text="Close",
            command=self.destroy,
            fg_color=COLORS['bg_tertiary'],
            hover_color=COLORS['accent_dim'],
            width=100
        )
        self.close_btn.pack(side="left", padx=10)
        
        # Center on screen
        self.center_window()
        
    def center_window(self):
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')

class ErrorOverlay:
    """
    Non-blocking inline error notification (toast).
    """
    def __init__(self, master):
        self.master = master
        self.frame = None
        
    def show(self, message, duration=5000):
        """Show error toast at bottom of screen."""
        if self.frame:
            self.frame.destroy()
            
        self.frame = ctk.CTkFrame(
            self.master,
            fg_color=COLORS['warning'],
            corner_radius=20
        )
        # Place relative to bottom center
        self.frame.place(relx=0.5, rely=0.85, anchor="center")
        
        label = ctk.CTkLabel(
            self.frame,
            text=f"⚠️ {message}",
            text_color='#000000',
            font=FONTS['primary_bold'],
            padx=20,
            pady=10
        )
        label.pack()
        
        # Auto hide
        self.master.after(duration, self.hide)
        
    def hide(self):
        if self.frame:
            self.frame.destroy()
            self.frame = None









