"""
Custom Exception Types
Defines specific exceptions for better error handling throughout the assistant.
"""


class VoiceAssistantError(Exception):
    """Base exception for all voice assistant errors."""
    pass


class ModelError(VoiceAssistantError):
    """Error related to AI model operations."""
    pass


class ToolError(VoiceAssistantError):
    """Error related to tool execution."""
    pass


class AudioError(VoiceAssistantError):
    """Error related to audio input/output."""
    pass


class ConfigError(VoiceAssistantError):
    """Error related to configuration."""
    pass


class RegistryError(VoiceAssistantError):
    """Error related to tool registry operations."""
    pass


class ToolSelectionError(RegistryError):
    """LLM tool selector failed and fail-fast mode aborted the turn (no RAG fallback)."""
    pass


class WakeWordError(VoiceAssistantError):
    """Error related to wake word detection."""
    pass

