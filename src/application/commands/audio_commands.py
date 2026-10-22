"""
Audio-related commands
"""

from dataclasses import dataclass
from typing import Any, Optional
from .base_command import Command


@dataclass
class SetAudioDeviceCommand(Command):
    """Command to set the active audio input device."""

    device_index: int

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate device index is non-negative."""
        if self.device_index < 0:
            return (False, "Device index must be non-negative")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: set audio device."""
        return facade.set_audio_device(self.device_index)

    def __str__(self) -> str:
        return f"SetAudioDeviceCommand(device_index={self.device_index})"


@dataclass
class SwitchOutputDeviceCommand(Command):
    """Command to switch audio output device by ID."""

    device_id: str

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate device ID is not empty."""
        if not self.device_id or not self.device_id.strip():
            return (False, "Device ID cannot be empty")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: switch output device."""
        return facade.switch_output_device(self.device_id)

    def __str__(self) -> str:
        return f"SwitchOutputDeviceCommand(device_id={self.device_id})"


@dataclass
class SetVolumeCommand(Command):
    """Command to set system volume."""

    volume: float

    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate volume is in valid range."""
        if not (0.0 <= self.volume <= 1.0):
            return (False, f"Volume must be between 0.0 and 1.0, got {self.volume}")
        return (True, None)

    async def execute(self, facade) -> bool:
        """Execute: set volume."""
        return facade.set_volume(self.volume)

    def __str__(self) -> str:
        return f"SetVolumeCommand(volume={self.volume:.2f})"


@dataclass
class GetAudioDevicesCommand(Command):
    """Command to get available audio devices."""

    async def execute(self, facade) -> list:
        """Execute: get audio devices."""
        return facade.get_audio_devices()

    def __str__(self) -> str:
        return "GetAudioDevicesCommand()"


@dataclass
class GetVolumeCommand(Command):
    """Command to get current volume."""

    async def execute(self, facade) -> float:
        """Execute: get volume."""
        return facade.get_volume()

    def __str__(self) -> str:
        return "GetVolumeCommand()"


@dataclass
class GetAudioLevelCommand(Command):
    """Command to get current audio level."""

    async def execute(self, facade) -> float:
        """Execute: get audio level."""
        return facade.get_audio_level()

    def __str__(self) -> str:
        return "GetAudioLevelCommand()"
