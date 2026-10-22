from typing import List, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AssistantFacade")


class AudioFacadeMixin:
    """Mixin for audio-related facade methods."""

    def get_audio_devices(self) -> List[Dict[str, Any]]:
        """
        Get list of available audio input devices.

        Returns:
            List of device dictionaries with 'index', 'name', etc.
        """
        if not self._assistant or not hasattr(self._assistant, 'audio_handler'):
            return []

        audio_handler = self._assistant.audio_handler
        if not audio_handler or not hasattr(audio_handler, 'get_audio_devices'):
            return []

        try:
            return audio_handler.get_audio_devices()
        except Exception as e:
            logger.error(f"Failed to get audio devices: {e}")
            return []

    def set_audio_device(self, device_index: int) -> bool:
        """
        Set the active audio input device.

        Args:
            device_index: Index of the device to activate

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'audio_handler'):
            return False

        audio_handler = self._assistant.audio_handler
        if not audio_handler or not hasattr(audio_handler, 'set_audio_device'):
            return False

        try:
            return audio_handler.set_audio_device(int(device_index))
        except Exception as e:
            logger.error(f"Failed to set active audio device {device_index}: {e}")
            return False

    def switch_output_device(self, device_id: str) -> bool:
        """
        Switch audio output device by ID (GUID).

        Args:
            device_id: Device GUID

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'audio_handler'):
            return False

        audio_handler = self._assistant.audio_handler
        if not audio_handler or not hasattr(audio_handler, 'switch_output_device'):
            return False

        try:
            audio_handler.switch_output_device(device_id)
            return True
        except Exception as e:
            logger.error(f"Failed to switch output device {device_id}: {e}")
            return False

    def get_volume(self) -> float:
        """
        Get current audio output volume.

        Returns:
            Volume level (0.0 - 1.0)
        """
        if not self._assistant or not hasattr(self._assistant, 'audio_handler'):
            return 0.5

        audio_handler = self._assistant.audio_handler
        if not audio_handler or not hasattr(audio_handler, 'get_volume'):
            return 0.5

        try:
            return audio_handler.get_volume()
        except Exception as e:
            logger.error(f"Failed to get volume: {e}")
            return 0.5

    def set_volume(self, level: float) -> bool:
        """
        Set audio output volume.

        Args:
            level: Volume level (0.0 - 1.0)

        Returns:
            True if successful, False otherwise
        """
        if not self._assistant or not hasattr(self._assistant, 'audio_handler'):
            return False

        audio_handler = self._assistant.audio_handler
        if not audio_handler or not hasattr(audio_handler, 'set_volume'):
            return False

        try:
            # Clamp to valid range
            vol_float = max(0.0, min(1.0, float(level)))
            audio_handler.set_volume(vol_float)
            return True
        except Exception as e:
            logger.error(f"Failed to set volume {level}: {e}")
            return False

    def get_audio_level(self) -> float:
        """
        Get current audio level (input or output depending on state).

        Returns:
            Audio level (0.0 - 1.0)
        """
        if not self._assistant or not hasattr(self._assistant, 'session_manager'):
            return 0.0

        session_manager = self._assistant.session_manager
        if not session_manager:
            return 0.0

        audio_handler = self._assistant.audio_handler
        if not audio_handler:
            return 0.0

        try:
            # If AI is speaking, return output level
            if session_manager.is_speaking:
                if hasattr(audio_handler, 'output') and audio_handler.output:
                    return float(audio_handler.output.get_output_level())

            # Otherwise return input level
            if hasattr(audio_handler, 'input') and audio_handler.input:
                return float(audio_handler.input.get_current_input_level())
        except Exception:
            pass

        return 0.0
