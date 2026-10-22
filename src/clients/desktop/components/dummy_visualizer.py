class DummyVisualizer:
    """
    Dummy visualizer object for compatibility when webview is used or actual visualizer is disabled.
    """
    def __init__(self, main_window_ref):
        self.main_window = main_window_ref
    
    def set_state(self, state):
        pass  # No-op for HTML-based UI
    
    def set_audio_level(self, level):
        # Store audio level in main window for JavaScript API access
        # Convert to native Python float to ensure JSON serializability
        window = self.main_window()
        if window:
            window.current_audio_level = float(level) if level is not None else 0.0
    
    def destroy(self):
        pass  # No-op
