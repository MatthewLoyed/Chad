"""
Sound Manager - Downloads and manages sound effects from the web.
Makes it easy to swap out sounds by updating URLs in config.
"""

import logging
import os
import requests
from pathlib import Path
from typing import Optional, Dict
from io import BytesIO
import threading
import threading
# Audio libs lazy loaded
# import sounddevice as sd
# import numpy as np
# from pydub import AudioSegment

from src.shared.logger import setup_logger

logger = setup_logger("SoundManager")
logger.setLevel(logging.WARNING)


class SoundManager:
    """Manages sound effects downloaded from the web."""
    
    def __init__(self, sounds_dir: str = "src/assets/sounds", config: Optional[Dict] = None):
        """
        Initialize sound manager.
        
        Args:
            sounds_dir: Directory to store downloaded sounds
            config: Sound configuration dictionary with URLs
        """
        self.sounds_dir = Path(sounds_dir)
        self.sounds_dir.mkdir(parents=True, exist_ok=True)
        
        # Default sound URLs (can be overridden in config)
        self.sound_urls = {
            "activation": None,
            "startup": None,
            "success": None,
            "deactivate": None,
            "thinking": None,
        }
        
        # Override with config if provided
        if config:
            config_urls = config.get("sound_urls", {})
            for key, url in config_urls.items():
                if url:
                    self.sound_urls[key] = url
        
        # Cache of loaded sound data
        self._sound_cache: Dict[str, bytes] = {}
        self.audio_output = None # Linked at runtime by SessionManager
        
        # Thinking loop state
        self._thinking = threading.Event()
        self._thinking_thread: Optional[threading.Thread] = None
        self._thinking_audio_data: Optional[bytes] = None
        self._thinking_volume = 0.15  # Subtle background volume (0.0 to 1.0)
    
    def set_audio_output(self, audio_output):
        """Set the audio output handler for direct playback."""
        self.audio_output = audio_output
        logger.info("[ AUDIO ] : SoundManager linked to AudioOutput.")
    
    def _load_sound(self, sound_name: str, source: str) -> Optional[Path]:
        """Load a sound file from URL or local path."""
        try:
            from pathlib import Path
            
            # Check if source is a local file path
            local_path = Path(source)
            if local_path.exists() and local_path.is_file():
                return local_path
            
            # Check if it's a relative path in sounds directory
            sounds_path = self.sounds_dir / source
            if sounds_path.exists() and sounds_path.is_file():
                return sounds_path
            
            # Try to find file by name in sounds directory (without extension)
            for ext in ['.wav', '.mp3', '.ogg', '.m4a', '.flac']:
                candidate = self.sounds_dir / f"{source}{ext}"
                if candidate.exists():
                    return candidate
            
            # Download from URL
            if source.startswith('http://') or source.startswith('https://'):
                file_ext = source.split('.')[-1] if '.' in source else "mp3"
                file_path = self.sounds_dir / f"{sound_name}.{file_ext}"
                
                if file_path.exists():
                    return file_path
                
                response = requests.get(source, timeout=10)
                response.raise_for_status()
                
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                
                return file_path
            
            return None
        except Exception as e:
            logger.error(f"Failed to load sound '{sound_name}' from '{source}': {e}")
            return None
    
    async def _convert_audio_to_pcm(self, file_path: Path, sample_rate: int = 44100, target_channels: int = 2) -> Optional[bytes]:
        """Convert audio file to 16-bit PCM at specified sample rate (Async)."""
        logger.debug(f"[ SOUND ] : Converting '{file_path.name}' to PCM (rate={sample_rate}, channels={target_channels})...")
        
        def _do_convert():
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(file_path))
                audio = audio.set_frame_rate(sample_rate).set_channels(target_channels).set_sample_width(2)
                return audio.raw_data
            except Exception as e:
                logger.error(f"Failed to convert audio to PCM: {e}")
                return None
        
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_convert)
    
    async def get_sound(self, sound_name: str) -> Optional[bytes]:
        """Get sound data as PCM bytes (16-bit, 44100Hz, stereo) - Async."""
        if sound_name in self._sound_cache:
            return self._sound_cache[sound_name]
        
        if sound_name not in self.sound_urls:
            return None
        
        source = self.sound_urls[sound_name]
        if not source:
            return None
        
        # Load sound (IO blocking - ideally this would also be async)
        file_path = self._load_sound(sound_name, source)
        if not file_path:
            return None
        
        logger.debug(f"[ SOUND ] : Loading sound '{sound_name}'...")
        pcm_data = await self._convert_audio_to_pcm(file_path, sample_rate=44100, target_channels=2)
        if pcm_data:
            self._sound_cache[sound_name] = pcm_data
        
        return pcm_data
    
    def _thinking_loop_thread(self):
        """Seamless loop for thinking sound."""
        try:
            if self._thinking_audio_data is None:
                import asyncio
                # Run async get_sound in the thread loop
                # This is tricky since this is a background thread
                # For now, we'll keep the thread simple but we need to handle the await
                # Actually, if we are in a thread, we can't easily await without a loop.
                # Let's use a simpler approach for the thread: use a sync wrapper if needed
                # OR better, preload the sound.
                pass
            
            # Process audio data (volume scaling)
            import numpy as np
            import sounddevice as sd
            
            audio_array = np.frombuffer(self._thinking_audio_data, dtype=np.int16).astype(np.float32)
            audio_array *= self._thinking_volume
            processed_data = audio_array.astype(np.int16).reshape(-1, 2)
            
            while self._thinking.is_set():
                # Play directly via sounddevice (non-blocking)
                sd.play(processed_data, 44100, blocking=False)
                
                # Wait for the sound to finish (with check interval for quick stop)
                duration = len(processed_data) / 44100  # Sound duration in seconds
                elapsed = 0.0
                check_interval = 0.1
                while elapsed < duration and self._thinking.is_set():
                    import time
                    time.sleep(check_interval)
                    elapsed += check_interval
                
        except Exception as e:
            logger.error(f"Error in thinking loop: {e}")
    
    def start_thinking_loop(self):
        """Start the thinking sound loop. DISABLED for stability."""
        # TEMPORARILY DISABLED: The thinking loop was causing potential conflicts
        # with the TTS audio stream via sd.play(). Re-enable once core audio is stable.
        # if self._thinking.is_set():
        #     return
        # self._thinking.set()
        # self._thinking_thread = threading.Thread(target=self._thinking_loop_thread, daemon=True)
        # self._thinking_thread.start()
        pass
    
    def stop_thinking_loop(self):
        """Stop the thinking sound loop."""
        self._thinking.clear()
        if self._thinking_thread:
            # Don't join for too long to avoid blocking UI
            # self._thinking_thread.join(timeout=0.1)
            self._thinking_thread = None
        # NOTE: Do NOT call sd.stop() here - it kills the TTS output stream!

    def play_sound(self, sound_key: str):
        """Trigger a one-shot alert sound (Sync wrapper for threads)."""
        def _play_target():
            try:
                import asyncio
                # Create a temporary loop if none exists in this thread
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                audio_bytes = loop.run_until_complete(self.get_sound(sound_key))
                if audio_bytes:
                    if self.audio_output:
                        self.audio_output.play_sound_directly(audio_bytes)
                    else:
                        import numpy as np
                        import sounddevice as sd
                        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).reshape(-1, 2)
                        sd.play(audio_array, 44100)
                        sd.wait()
            except Exception as e:
                logger.error(f"Error playing sound '{sound_key}': {e}")

        threading.Thread(target=_play_target, daemon=True).start()
