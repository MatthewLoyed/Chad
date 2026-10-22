"""
GUI Styles and Theme Configuration
Defines the color palette, fonts, and theme settings for the application.
Uses CSS variable-like approach for easy color scheme switching.
"""

# CSS Variable-like Color Scheme (matches index.html)
# These can be easily changed to switch color schemes
NEON_BLUE = '#00f3ff'
GLASS_BG = '#0d1117'  # rgba(13, 17, 23, 0.7) - approximated for CTk
BG_GRADIENT_START = '#1a2332'
BG_GRADIENT_END = '#000000'
BORDER_GLOW_COLOR = 'rgba(0, 243, 255, 0.3)'  # For reference, CTk uses hex

# Color Palette (Upgraded UI Theme)
COLORS = {
    'bg_primary': BG_GRADIENT_END,        # Deep black (Main background)
    'bg_secondary': GLASS_BG,              # Glass background (panels)
    'bg_tertiary': '#1f252e',              # Even lighter (Inputs/elements)
    'bg_glass': GLASS_BG,                  # For glassmorphism panels
    
    'accent_main': NEON_BLUE,              # Neon cyan (Primary accent)
    'accent_dim': '#007a8f',               # Dimmed Cyan (Secondary accent)
    'accent_hover': '#33e0ff',             # Hover state
    
    'text_primary': '#ffffff',             # White (Main text)
    'text_secondary': '#b3b3b3',            # Semi-transparent white (Subtitles) - hex approximation of rgba(255,255,255,0.7)
    'text_dim': '#808080',                 # More transparent (Placeholder/disabled) - hex approximation of rgba(255,255,255,0.5)
    
    'success': '#00ff00',                  # Bright Green (for logs)
    'warning': '#ffaa00',                  # Bright Orange
    'error': '#ff3366',                    # Bright Red
    
    'border': '#1a3a3f',                    # Subtle border (glassmorphism) - hex approximation of rgba(0,243,255,0.1)
    'border_glass': NEON_BLUE,              # Glassmorphism border accent
    'border_card': NEON_BLUE,               # Card left border accent
    'transparent': 'transparent'            # Transparent
}

# Fonts
FONTS = {
    'primary': ('Segoe UI', 14),
    'primary_bold': ('Segoe UI', 14, 'bold'),
    'header': ('Segoe UI', 24, 'bold'),
    'subheader': ('Segoe UI', 18),
    'monospace': ('Consolas', 14),
    'monospace_small': ('Consolas', 12),
    'small': ('Segoe UI', 12),
}

# Dimensions (matches index.html grid layout)
DIMENSIONS = {
    'window_width': 1400,          # Wider for 3-column layout
    'window_height': 900,
    'min_width': 1000,
    'min_height': 700,
    'corner_radius': 5,             # Smaller radius for cards
    'padding_small': 10,
    'padding_medium': 20,
    'padding_large': 30,
    'sidebar_width': 300,          # Left sidebar (Action History)
    'logs_width': 300,              # Right sidebar (Live Logs) - matches index.html
    'header_height': 60,            # Header height
    'footer_height': 100,           # Footer height
    'reactor_size': 150,            # Reactor circle size
    'hud_width': 650,               # Minimalist command bar width
    'hud_height': 120,              # Minimalist command bar height
}

# Animation Settings
ANIMATION = {
    'circle_pulse_speed': 0.05,
    'transition_speed': 0.2,
    'rotation_speed': 2.0,         # Rotation speed for thinking state
    'glow_intensity': 0.8,         # Glow effect intensity
}

# Glassmorphism utility functions
def get_glass_frame_config():
    """
    Get configuration for glassmorphism frame styling.
    Returns dict with fg_color and border_color for CTkFrame.
    """
    return {
        'fg_color': COLORS['bg_glass'],
        'border_width': 1,
        'border_color': COLORS['border'],
        'corner_radius': DIMENSIONS['corner_radius']
    }

def get_card_config():
    """
    Get configuration for action history cards (left border accent style).
    Returns dict with fg_color and border styling for CTkFrame.
    """
    return {
        'fg_color': '#0a1a1c',  # Subtle cyan tint - hex approximation of rgba(0, 243, 255, 0.05)
        'border_width': 0,  # No border, we'll use a left accent line
        'corner_radius': DIMENSIONS['corner_radius']
    }

