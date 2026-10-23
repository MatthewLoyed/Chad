"""Recover desktop connection state when initial pushes precede React readiness."""
class StatusAPIMixin:
    def get_status(self):
        window = self.main_window_ref()
        return window.current_status_text if window else ""

    def get_raw_state(self):
        window = self.main_window_ref()
        return getattr(window, "_current_state_enum", "initializing")
