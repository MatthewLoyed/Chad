"""
Audio Device Manager - Smart Device Discovery and Configuration (SoundDevice version)
Automatically detects the default output device and its capabilities.
"""

import logging
import sounddevice as sd
import threading
from typing import Optional, Dict, Any
from src.shared.logger import setup_logger

logger = setup_logger("AudioDeviceManager")
logger.setLevel(logging.WARNING)

class AudioDeviceManager:
    """
    Manages audio discovery and configuration using sounddevice.
    """
    _rescan_lock = threading.Lock()
    
    def __init__(self):
        """Initialize the audio device manager."""
        self.default_device_index: Optional[int] = None
        self.device_info: Optional[Dict[str, Any]] = None
        self.device_name: str = "Unknown"
        self.native_sample_rate: int = 44100  # Default fallback
        self.native_channels: int = 2  # Default fallback
        
        # Discover and configure default device
        self._discover_default_device()
    
    def refresh(self) -> None:
        """Force a hardware re-scan and re-discover the default device."""
        with self._rescan_lock:
            try:
                sd._terminate()
                sd._initialize()
                self._discover_default_device()
            except Exception as e:
                logger.error(f"Failed to refresh audio devices: {e}")
    
    def _discover_default_device(self) -> None:
        """Discover the system's default output device and its capabilities."""
        try:
            # sd.default.device returns [input, output] indices
            default_output = sd.default.device[1]
            if default_output == -1:
                # Try to find any output device
                devices = sd.query_devices()
                for i, dev in enumerate(devices):
                    if dev['max_output_channels'] > 0:
                        default_output = i
                        break
            self._query_device(default_output)
        except Exception as e:
            logger.error(f"Failed to discover default device: {e}")
            self._query_device(None)
    
    def _query_device(self, device_index: Optional[int]) -> None:
        """Query a specific device or the default device for its capabilities."""
        try:
            if device_index is None or device_index == -1:
                # Fallback if no device found
                self.default_device_index = None
                self.device_name = "Default (fallback)"
                self.native_sample_rate = 44100
                self.native_channels = 2
                return

            self.default_device_index = device_index
            self.device_info = sd.query_devices(device_index)
            
            self.device_name = self.device_info.get('name', 'Unknown Device')
            self.native_sample_rate = int(self.device_info.get('default_samplerate', 44100))
            self.native_channels = int(self.device_info.get('max_output_channels', 2))
            
            if self.native_channels < 1:
                self.native_channels = 1
            
            logger.debug(f"Connected to audio device: {self.device_name}")

        except Exception as e:
            logger.error(f"Failed to query audio device (index={device_index}): {e}")
            self.default_device_index = device_index
            self.device_name = "Default (fallback)"
            self.native_sample_rate = 44100
            self.native_channels = 2

    def query_device(self, device_index: int) -> None:
        """Query a specific device for its capabilities."""
        self._query_device(device_index)
    
    def get_device_index(self) -> Optional[int]:
        """Get the device index."""
        return self.default_device_index
    
    def get_device_name(self) -> str:
        """Get the device name."""
        return self.device_name
    
    def get_native_sample_rate(self) -> int:
        """Get the native sample rate."""
        return self.native_sample_rate
    
    def get_native_channels(self) -> int:
        """Get the native channels."""
        return self.native_channels
    
    def needs_resampling(self, source_rate: int) -> bool:
        """Check if resampling is needed."""
        return source_rate != self.native_sample_rate
    
    def needs_upmixing(self, source_channels: int) -> bool:
        """Check if upmixing is needed."""
        return source_channels < self.native_channels
    
    def find_device_index_by_name(self, name_part: str) -> Optional[int]:
        """Find a device index by fuzzy name matching."""
        try:
            # Refresh device list
            devices = sd.query_devices()
            best_match = None
            
            for i, dev in enumerate(devices):
                # Check for output capabilities
                if dev['max_output_channels'] > 0:
                    dev_name = dev['name'].lower()
                    target = name_part.lower()
                    
                    if target in dev_name:
                        # Prefer detailed match? For now just return first valid match
                        return i
                        
            return None
        except Exception as e:
            logger.error(f"Error searching for device '{name_part}': {e}")
            return None

    def cleanup(self) -> None:
        """No specific cleanup needed for sounddevice."""
        pass
