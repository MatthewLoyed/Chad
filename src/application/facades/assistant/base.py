from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class AssistantFacadeBase:
    """Base class for AssistantFacade providing shared attributes and constructor."""
    def __init__(self, assistant, is_uplink: bool = False, uplink=None, text_mode_client=None):
        """
        Initialize facade.

        Args:
            assistant: VoiceAssistant instance to wrap
            is_uplink: Whether this facade is acting as a Limb to a remote Brain
            uplink: ChadLimb instance for remote Brain connectivity (optional)
            text_mode_client: TextModeClient for local brain orchestration (optional)
        """
        self._assistant = assistant
        self._is_uplink = is_uplink
        self._uplink = uplink or (getattr(assistant, 'limb', None) if hasattr(assistant, 'limb') else None)
        self._text_mode_client = text_mode_client or (getattr(assistant, 'text_mode_client', None) if hasattr(assistant, 'text_mode_client') else None)
