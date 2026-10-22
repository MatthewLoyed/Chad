"""
AssistantFacade - Clean API boundary for GUI access to VoiceAssistant

This facade prevents the GUI from directly accessing internal assistant components.
All GUI interactions should go through this facade's public API.

Design Goals:
- Hide implementation details (audio_handler, memory internals)
- Provide command-oriented API (not property access)
- Enable future refactoring without breaking GUI
- Support testing and mocking
"""

from src.application.facades.assistant import (
    AssistantFacadeBase,
    AudioFacadeMixin,
    MemoryFacadeMixin,
    ConfigFacadeMixin,
    SessionFacadeMixin,
    ModelsFacadeMixin,
    WorkspaceFacadeMixin
)


class AssistantFacade(
    AudioFacadeMixin,
    MemoryFacadeMixin,
    ConfigFacadeMixin,
    SessionFacadeMixin,
    ModelsFacadeMixin,
    WorkspaceFacadeMixin,
    AssistantFacadeBase
):
    """
    Facade for VoiceAssistant - provides clean API for GUI access.

    This is the ONLY interface the GUI should use to interact with the assistant.
    Direct access to assistant.audio_handler, assistant.memory, etc. is prohibited.
    """
    def __init__(self, assistant, is_uplink: bool = False, uplink=None, text_mode_client=None):
        super().__init__(assistant, is_uplink=is_uplink, uplink=uplink, text_mode_client=text_mode_client)
