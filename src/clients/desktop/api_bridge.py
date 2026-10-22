from src.clients.desktop.api import (
    WebViewAPIBase,
    RemoteCommandHandler,
    _dialog_queue,
    AudioAPIMixin,
    MemoryAPIMixin,
    ChatAPIMixin,
    GoogleAPIMixin,
    ConfigAPIMixin,
    SystemAPIMixin,
    KanbanAPIMixin,
    WorkflowAPIMixin,
    AutomationsAPIMixin,
    SkillsAPIMixin,
)

# Re-expose RemoteCommandHandler and _dialog_queue for backward compatibility
__all__ = ['WebViewAPI', 'RemoteCommandHandler', '_dialog_queue']


class WebViewAPI(
    AudioAPIMixin,
    MemoryAPIMixin,
    ChatAPIMixin,
    GoogleAPIMixin,
    ConfigAPIMixin,
    SystemAPIMixin,
    KanbanAPIMixin,
    WorkflowAPIMixin,
    AutomationsAPIMixin,
    SkillsAPIMixin,
    WebViewAPIBase
):
    """
    Core Python API injected into the pywebview instance to bridge JavaScript calls to Python logic.
    Now strictly decoupled in a 3-Tier Distributed architecture and modularized to satisfy file size limit policies.
    """
    def __init__(self, main_window_ref):
        super().__init__(main_window_ref)
