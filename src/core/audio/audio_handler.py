"""
Audio Handler - SoundDevice (WASAPI) Kernel
Manages microphone input and speaker output with high-performance callbacks.
"""

import asyncio
import collections
import sounddevice as sd
import numpy as np
import re
import threading
import queue
import time
from typing import Optional, AsyncGenerator
import logging

from src.shared.exceptions import AudioError
from src.shared.logger import setup_logger
from src.core.audio.audio_device_manager import AudioDeviceManager
from src.shared.config_manager import ConfigManager
from src.infrastructure.repositories import ConfigRepository
from src.core.audio.audio_monitor import AudioDeviceListener

logger = setup_logger("AudioHandler")
# logger.setLevel(logging.WARNING)  # Allow default logging level (usually INFO/DEBUG) for better visibility


def _resolve_default_input_device() -> Optional[int]:
    """Return an explicit input device index, or None to let PortAudio decide.

    ``sd.default.device`` reports -1 for input when Windows has no default
    recording device selected. Passing that through makes every InputStream
    open fail with "Invalid device" even though working microphones are
    enumerated, so fall back to the first device that has input channels.
    """
    try:
        default_input = sd.default.device[0]
    except Exception:
        return None

    if isinstance(default_input, int) and default_input >= 0:
        return None  # A real default exists; let PortAudio use it.

    try:
        devices = sd.query_devices()
    except Exception as e:
        logger.error(f"[ AUDIO ] : Could not enumerate input devices: {e}")
        return None

    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) > 0:
            logger.warning(
                "[ AUDIO ] : No default recording device set in Windows; "
                f"using '{device.get('name')}' (index {index}). "
                "Set a default input device in Sound settings to pick a specific mic."
            )
            return index

    logger.error("[ AUDIO ] : No input-capable audio devices found.")
    return None


class AudioInput:
    """Manages microphone input stream using sounddevice."""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        input_device_index: Optional[int] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.input_device_index = input_device_index
        self.loop = loop
        
        self.device_sample_rate = sample_rate # Actual rate being recorded
        self.stream: Optional[sd.InputStream] = None
        self.is_active = False
        self._is_muted = False  # Mute flag for session (interrupt monitor still receives audio)
        
        # Performance tuning
        self.blocksize = 512 # Force faster updates to the VAD
        self.resample_strategy = "None"
        
        # We'll initialize the queue in start() which is called on the main thread
        self.audio_queue = None
        self.input_queue = None
        
        self.current_input_level = 0.0
        
        # Broadcast Pattern: Initialize subscribers early so read_chunks can be called before start()
        self.subscribers: set[asyncio.Queue] = set()
        
    def get_current_input_level(self) -> float:
        """Return the current RMS level of the input audio."""
        return self.current_input_level

    def _input_callback(self, indata, frames, time, status):
        """Callback for sounddevice input stream."""
        if status:
            logger.debug(f"[ AUDIO ] : Input Stream Status: {status}")
            
        # Calculate Input Level (Throttled for Performance - every 3rd frame)
        try:
            # RMS calculation (simplified for speed)
            # Use raw indata to avoid resampling overhead for level meter
            self._callback_counter += 1
            if self._callback_counter % 3 == 0:
                rms = np.sqrt(np.mean(indata.astype(np.float32) ** 2))
                self.current_input_level = float(rms / 32768.0)
        except:
            self.current_input_level = 0.0
        
        if self.is_active and hasattr(self, 'subscribers'):
            # Convert numpy array to bytes with optimized resampling
            if self.device_sample_rate != self.sample_rate:
                data_int16 = indata.flatten()
                
                # OPTIMIZED: Use slicing for integer multiples
                if self.device_sample_rate % self.sample_rate == 0:
                    step = self.device_sample_rate // self.sample_rate
                    resampled = data_int16[::step]
                else:
                    # Fallback to interpolation
                    duration = len(data_int16) / self.device_sample_rate
                    num_samples = int(duration * self.sample_rate)
                    resampled = np.interp(
                        np.linspace(0, duration, num_samples),
                        np.linspace(0, duration, len(data_int16)),
                        data_int16
                    ).astype(np.int16)
                
                data = resampled.tobytes()
            else:
                data = indata.tobytes()

            # Thread-safe put into asyncio.Queues
            try:
                # Snapshot the set to avoid modification during iteration
                active_subscribers = list(self.subscribers)
                for q in active_subscribers:
                    try:
                        self.loop.call_soon_threadsafe(q.put_nowait, data)
                    except asyncio.QueueFull:
                        pass 
                    except Exception as e:
                        logger.error(f"Queue broadcast error: {e}")
            except Exception as e:
                logger.error(f"Input callback error: {e}")

    def start(self) -> None:
        """Start the audio input stream."""
        if self.stream is not None:
            return
        
        # Ensure we have the main event loop
        if not self.loop:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.get_event_loop()
        
        # Initialize queue on the current (main) thread's loop
        # No legacy queue init needed
        # if not self.audio_queue:
        #     self.audio_queue = asyncio.Queue()
        
        # Broadcast Pattern: Support multiple listeners (subscribers)
        # We replace the single audio_queue with a set of queues
        # Broadcast Pattern: Support multiple listeners (subscribers)
        # We replace the single audio_queue with a set of queues
        if not hasattr(self, 'subscribers'):
            self.subscribers: set[asyncio.Queue] = set()
        
        # We'll initialize the main queue logic in start()
        self.input_queue = None # Legacy support, though read_chunks is preferred
        
        self.current_input_level = 0.0
        
        self._callback_counter = 0 # Debug heartbeat
        
        # Windows can be left with no default recording device (sd.default.device
        # reports -1 for input), which fails every stream open below even when
        # usable microphones exist. Pick one explicitly rather than giving up.
        if self.input_device_index is None:
            self.input_device_index = _resolve_default_input_device()

        # Retry loop for InputStream (Handles OS device state transitions)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # First attempt: 16000 Hz (Gemini's required rate)
                self.stream = sd.InputStream(
                    device=self.input_device_index,
                    channels=self.channels,
                    samplerate=self.sample_rate,
                    blocksize=self.blocksize,
                    latency='low',
                    dtype='int16',
                    callback=self._input_callback
                )
                self.stream.start()
                self.is_active = True
                self.resample_strategy = "Native (No Resampling)"
                
                # latency can be a float or tuple depending on stream type and platform
                try:
                    if isinstance(self.stream.latency, (list, tuple)):
                        latency = self.stream.latency[0] * 1000
                    else:
                        latency = self.stream.latency * 1000
                except:
                    latency = 0
                    
                logger.debug(f"[ AUDIO ] : Low-Latency Mode Active. Input Blocksize: {self.blocksize} | Output Latency: Low")
                logger.debug(f"[ AUDIO ] : Optimized Resampling Strategy: {self.resample_strategy}")
                logger.debug(f"[ AUDIO ] : Input Latency: {latency:.2f}ms")
                return # SUCCESS
                
            except Exception as e:
                if "Invalid device" in str(e) or "-9996" in str(e):
                    if attempt < max_retries - 1:
                        logger.warning(f"[ AUDIO ] : Input device not ready (Error -9996). Retrying in 500ms... (Attempt {attempt+1}/{max_retries})")
                        time.sleep(0.5)
                        continue
                
                # If it's a sample rate error, we handle it below. 
                # Otherwise, if it's not a device error, break and go to fallback.
                if "Invalid sample rate" not in str(e):
                    logger.error(f"[ AUDIO ] : Failed to start input: {e}")
                    break
                    
                # If we get here, it's likely a sample rate error, fall through to native rate check
                break
            
        # --- Fallback Logic (if initial 16000Hz or device state fails) ---
        try:
            # If we get here, initial start failed (likely sample rate or perma-device error)
            # Try default device sample rate and resample
            device_info = sd.query_devices(self.input_device_index, 'input')
            native_rate = int(device_info['default_samplerate'])
            
            self.device_sample_rate = native_rate
            
            # Determine strategy for logging
            if native_rate % self.sample_rate == 0:
                self.resample_strategy = f"Integer Decimation ({native_rate}Hz -> 16kHz)"
            else:
                self.resample_strategy = f"Numpy Interpolation ({native_rate}Hz -> 16kHz)"
                
            self.stream = sd.InputStream(
                device=self.input_device_index,
                channels=self.channels,
                samplerate=native_rate,
                blocksize=self.blocksize,
                latency='low',
                dtype='int16',
                callback=self._input_callback
            )
            self.stream.start()
            self.is_active = True
            
            logger.debug(f"[ AUDIO ] : Low-Latency Mode Active. Input Blocksize: {self.blocksize} | Output Latency: Low")
            logger.debug(f"[ AUDIO ] : Optimized Resampling Strategy: {self.resample_strategy}")
            return
        except Exception as e:
            logger.error(f"Failed to start audio input fallback: {e}")
            # Fallback to default device OR Mono fix
            if self.input_device_index is not None or self.channels > 1:
                # Cleanup potentially broken stream
                if self.stream:
                    try:
                        self.stream.close()
                    except: pass
                    self.stream = None
                    
                should_retry = False
                if self.input_device_index is not None:
                    logger.info("Retrying with default input device...")
                    self.input_device_index = None
                    should_retry = True
                    
                if self.channels > 1:
                    logger.info("Retrying with Mono (1 channel)...")
                    self.channels = 1
                    should_retry = True
                    
                if should_retry:
                    self.start()
                    return
            
            # If we get here, truly failed
            self.stream = None
            raise AudioError(f"Failed to start audio input: {e}")

    async def read_chunks(self) -> AsyncGenerator[bytes, None]:
        """
        Read audio chunks from the input stream.
        Creates a unique queue for this consumer and subscribes to the broadcast.
        """
        if not self.loop:
            self.loop = asyncio.get_running_loop()
            
        # Create a personal queue for this consumer
        my_queue = asyncio.Queue()
        self.subscribers.add(my_queue)
        
        try:
            # Wait for stream to become active (in case read_chunks is called before start completes)
            wait_count = 0
            while not self.is_active and wait_count < 50:  # Max 5 seconds
                await asyncio.sleep(0.1)
                wait_count += 1
            
            if not self.is_active:
                logger.warning("read_chunks: Audio stream not active after 5s wait")
                return
                
            while self.is_active:
                try:
                    # Pull from the asyncio queue
                    chunk = await asyncio.wait_for(my_queue.get(), timeout=1.0)
                    yield chunk
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error reading audio chunk: {e}")
                    break
        finally:
            # Clean cleanup: remove subscriber
            if my_queue in self.subscribers:
                self.subscribers.remove(my_queue)
    
    def get_audio_level(self, audio_data: bytes) -> float:
        """Calculate RMS audio level for VAD."""
        try:
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
            return rms / 32768.0
        except Exception as e:
            logger.error(f"Error calculating audio level: {e}")
            return 0.0
    
    def clear_queue(self) -> None:
        """Clear all active audio input queues."""
        cleared_count = 0
        for q in list(self.subscribers):
            try:
                while not q.empty():
                    q.get_nowait()
                    cleared_count += 1
            except Exception:
                pass
                
        if cleared_count > 0:
            logger.debug(f"Cleared {cleared_count} stale chunks from {len(self.subscribers)} input queues.")
    
    def stop(self) -> None:
        """Stop the audio input stream."""
        self.is_active = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping audio input: {e}")
            finally:
                self.stream = None
    
    def cleanup(self) -> None:
        """Clean up audio resources."""
        self.stop()

    def mute_input(self):
        """Mute audio input (for session/Gemini - interrupt monitor still receives audio)."""
        self._is_muted = True

    def unmute_input(self):
        """Unmute audio input."""
        self._is_muted = False
    
    @property
    def is_muted(self) -> bool:
        """Check if input is muted (for session use, not for interrupt monitor)."""
        return getattr(self, '_is_muted', False)

class AudioOutput:
    """Manages speaker output stream using sounddevice with high-efficiency queue."""
    
    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        output_device_index: Optional[int] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        self.loop = loop
        self.on_playback_complete = None  # Async callback for Push architecture
        self.gemini_rate = 24000 # Gemini standard
        self.source_sample_rate = sample_rate
        self.source_channels = channels
        
        self.device_manager = AudioDeviceManager()
        if output_device_index is not None:
            self.device_manager.query_device(output_device_index)
        
        self.output_device_index = self.device_manager.get_device_index()
        self.device_sample_rate = self.device_manager.get_native_sample_rate()
        self.device_channels = self.device_manager.get_native_channels()
        
        self.stream: Optional[sd.OutputStream] = None
        self.is_active = False
        
        # Queue-based buffering (industry standard for streaming TTS)
        # Chunks are stored in queue, residual handles frame boundary mismatches
        self._chunk_queue = queue.Queue()  # Unbounded queue - never drops audio
        self._residual = np.array([], dtype=np.int16)  # Leftover samples from previous callback
        self._buffer_lock = threading.Lock()
        
        self.current_output_level = 0.0
        self.volume = 1.0
        self._effective_volume = 1.0
        
        # Internal state for event detection
        self._was_playing = False  # Track previous state to detect completion transition
        
    def set_volume(self, volume: float) -> None:
        """Set volume multiplier (0.0 to 1.0)."""
        self.volume = max(0.0, min(1.0, volume))
        # Use an exponential curve (x^2) for more natural volume perception
        # Linear sliders feel like they do nothing until the very end
        self._effective_volume = self.volume ** 2
        logger.debug(f"[ AUDIO ] : Master Volume set to {int(self.volume * 100)}% (Effective: {int(self._effective_volume * 100)}%)")
        
    def get_volume(self) -> float:
        """Get current volume multiplier."""
        return self.volume
        
    def get_output_level(self) -> float:
        """Return the current RMS level of the audio being played."""
        return self.current_output_level

    @property
    def is_playing(self) -> bool:
        """Check if audio is currently playing (queue or residual has data)."""
        # Check queue first (most likely to have data)
        if not self._chunk_queue.empty():
            return True
            
        # Check residual buffer (thread-safe)
        with self._buffer_lock:
            return len(self._residual) > 0

    def _output_callback(self, outdata, frames, time, status):
        """Callback for sounddevice output stream - queue-based chunk consumption."""
        # Reset level if no audio processed this frame (will be overwritten if audio exists)
        self.current_output_level = 0.0
        
        if status:
            pass
            # if 'underflow' not in str(status).lower():
            #     logger.debug(f"[ AUDIO ] : Output Stream Status: {status}")
        
        channels = self.device_channels
        needed = frames * channels
        
        try:
            # Collect samples from residual + queue until we have enough
            samples_list = []
            collected = 0
            
            with self._buffer_lock:
                # First, use any residual samples from previous callback
                if len(self._residual) > 0:
                    samples_list.append(self._residual)
                    collected += len(self._residual)
                    self._residual = np.array([], dtype=np.int16)
            
            # Pull chunks from queue until we have enough
            while collected < needed:
                try:
                    chunk = self._chunk_queue.get_nowait()
                    samples_list.append(chunk)
                    collected += len(chunk)
                except queue.Empty:
                    break
            
            # 1. State Transition Detection (Push Architecture)
            # MUST happen before early return to detect the transition to idle
            currently_has_data = collected > 0 or not self._chunk_queue.empty()
            
            if self._was_playing and not currently_has_data:
                if self.on_playback_complete and self.loop:
                    try:
                        self.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self.on_playback_complete())
                        )
                        logger.debug("[ AUDIO ] : Playback finished signal dispatched.")
                    except Exception as e:
                        logger.error(f"Error dispatching playback_complete: {e}")
            
            self._was_playing = currently_has_data

            if collected == 0:
                # No audio available - output silence
                outdata.fill(0)
                self.current_output_level = 0.0
                return
            
            # Concatenate all collected samples
            all_samples = np.concatenate(samples_list) if len(samples_list) > 1 else samples_list[0]
            
            if len(all_samples) >= needed:
                # We have enough - use exactly what we need
                outdata[:] = all_samples[:needed].reshape(frames, channels)
                # Save leftover for next callback
                if len(all_samples) > needed:
                    with self._buffer_lock:
                        self._residual = all_samples[needed:]
            else:
                # Partial fill - use what we have, zero-pad the rest
                partial_frames = len(all_samples) // channels
                if partial_frames > 0:
                    usable = partial_frames * channels
                    outdata[:partial_frames] = all_samples[:usable].reshape(partial_frames, channels)
                outdata[partial_frames:] = 0
                
            # 2. Calculate RMS for visualization
            try:
                rms = np.sqrt(np.mean(outdata.astype(np.float32) ** 2))
                self.current_output_level = float(rms / 32768.0)
            except Exception:
                self.current_output_level = 0.0

        except Exception as e:
            logger.error(f"Error in audio output callback: {e}")
            outdata.fill(0)
            self.current_output_level = 0.0

    def start(self) -> None:
        """Start the audio output stream."""
        if self.stream is not None:
            return
        
        try:
            self.stream = sd.OutputStream(
                device=self.output_device_index,
                channels=self.device_channels,
                samplerate=self.device_sample_rate,
                blocksize=2048, # Fixed blocksize to prevent buffer underruns (glitching/slurring)
                latency=0.1, # Increased safety buffer (100ms) to ensure smooth playback
                dtype='int16',
                callback=self._output_callback
            )
            self.stream.start()
            self.is_active = True
            logger.debug(f"[ AUDIO ] : Output Stream initialized ({self.device_channels} channels, {self.device_sample_rate}Hz). Latency: 0.1s")
            logger.debug(f"[ AUDIO ] : Resampling Active: 24000Hz -> {self.device_sample_rate}Hz.")
            logger.debug(f"[ AUDIO ] : System Sounds Online.")
            
        except Exception as e:
            logger.error(f"Failed to start audio output: {e}")
            raise AudioError(f"Failed to start audio output: {e}")

    def _resample_numpy(self, data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        """Resample audio using optimized numpy interpolation."""
        if from_rate == to_rate:
            return data
            
        # Fast decimation if possible
        if from_rate % to_rate == 0:
            return data[::from_rate // to_rate]
            
        duration = len(data) / from_rate
        num_samples = int(duration * to_rate)
        return np.interp(
            np.linspace(0, duration, num_samples),
            np.linspace(0, duration, len(data)),
            data
        ).astype(np.int16)

    def _upmix_numpy(self, data: np.ndarray, to_channels: int) -> np.ndarray:
        """Upmix mono to multiple channels."""
        # data is mono (num_samples,)
        if to_channels == 1:
            return data[:, np.newaxis]
        return np.tile(data[:, np.newaxis], (1, to_channels))

    def inject_silence(self, duration_sec: float = 0.5) -> None:
        """Inject silence to flush the buffer and prevent cutoff."""
        if not self.is_active:
            return
            
        num_samples = int(self.device_sample_rate * duration_sec)
        silence = np.zeros(num_samples, dtype='int16')
        
        # Upmix silence
        if self.device_channels > 1:
            silence = np.tile(silence[:, np.newaxis], (1, self.device_channels))
        else:
            silence = silence[:, np.newaxis] # Ensure 2D for consistency in flatten
            
        self._chunk_queue.put(silence.flatten())

    def play_chunk_immediately(self, audio_data: any) -> None:
        """Process and push audio data to accumulation buffer for playback."""
        if not self.is_active:
            return
            
        # 1. Type normalization (no unnecessary copying if already ndarray)
        if isinstance(audio_data, bytes):
            audio_data = np.frombuffer(audio_data, dtype='int16')
        elif not isinstance(audio_data, np.ndarray):
            audio_data = np.array(audio_data, dtype='int16')
            
        # 2. Optimized Resampling (Linear Interpolation)
        # Gemini outputs 24kHz, hardware is typically 48kHz (ratio = 2.0)
        ratio = self.device_sample_rate / self.gemini_rate
        if ratio != 1.0:
            new_length = int(len(audio_data) * ratio)
            # Fix off-by-one: Use len-1 to stay within bounds [0, N-1]
            audio_data = np.interp(
                np.linspace(0, len(audio_data) - 1, new_length),
                np.arange(len(audio_data)),
                audio_data
            ).astype(np.int16)
            
        # 3. Upmixing/Channel Mapping
        if audio_data.ndim == 1:
            # Mono to hardware channels
            audio_data = self._upmix_numpy(audio_data, self.device_channels)
        elif audio_data.shape[1] != self.device_channels:
            # Optimized stereo-to-multichannel tiling
            if audio_data.shape[1] == 2 and self.device_channels >= 2:
                repeats = self.device_channels // 2
                audio_data = np.tile(audio_data, (1, repeats))
                if self.device_channels % 2 != 0:
                    audio_data = np.column_stack([audio_data, audio_data[:, 0]])
            else:
                # Fallback upmix
                audio_data = self._upmix_numpy(audio_data[:, 0], self.device_channels)
            
        # 4. Apply Master Volume (at the end of the chain for max fidelity)
        if self._effective_volume < 0.99 or self._effective_volume > 1.01:
            audio_data = (audio_data.astype(np.float32) * self._effective_volume).astype(np.int16)

        # 5. Push to queue (thread-safe, never drops audio)
        flat_data = audio_data.flatten()
        self._chunk_queue.put(flat_data)

    def play_file(self, audio_data: bytes) -> None:
        """Play audio file content with resampling."""
        self.play_chunk_immediately(audio_data)

    async def play(self, audio_data: bytes) -> None:
        """Play audio data (legacy async API)."""
        self.play_chunk_immediately(audio_data)

    def stop(self) -> None:
        """Stop the audio output stream."""
        self.is_active = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping audio output: {e}")
            finally:
                self.stream = None
    
    def cleanup(self) -> None:
        """Clean up audio resources."""
        self.stop()

    def play_sound_directly(self, data: bytes, source_rate: int = 44100) -> None:
        """
        Play raw PCM bytes directly using sounddevice (bypasses TTS buffer).
        This prevents mixing issues between system sounds and TTS audio.
        
        Args:
            data: Raw PCM bytes (16-bit, stereo interleaved expected)
            source_rate: Sample rate of the input audio (default 44100 for system sounds)
        """
        try:
            if isinstance(data, bytes):
                audio_data = np.frombuffer(data, dtype='int16')
            elif isinstance(data, np.ndarray):
                audio_data = data.flatten() if data.ndim > 1 else data
            else:
                return
            
            # Reshape to stereo if interleaved
            if len(audio_data) % 2 == 0:
                audio_data = audio_data.reshape(-1, 2)
            
            # Play directly via sounddevice (handles device routing automatically)
            # Apply Master Volume
            if self._effective_volume < 0.99 or self._effective_volume > 1.01:
                audio_data = (audio_data.astype(np.float32) * self._effective_volume).astype(np.int16)
                
            # Uses blocking=False to not interfere with TTS stream
            # Specify the device to ensure it plays through the same hardware as Gemini
            sd.play(audio_data, source_rate, blocking=False, device=self.output_device_index)
        except Exception as e:
            logger.debug(f"Failed to play sound directly: {e}")
    
    def clear_queue(self) -> None:
        """Clear the audio buffer (for interruption handling)."""
        # Clear the queue
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break
        # Clear residual buffer
        with self._buffer_lock:
            self._residual = np.array([], dtype=np.int16)


class AudioHandler:
    """Combined audio input/output manager using SoundDevice (WASAPI)."""
    
    def __init__(self, config: dict, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.config = config
        self.loop = loop
        self._config_repo = ConfigRepository()  # For preference mutations
        
        # Discover WASAPI host API
        self.wasapi_index = None
        try:
            for i, hostapi in enumerate(sd.query_hostapis()):
                if 'WASAPI' in hostapi['name']:
                    self.wasapi_index = i
                    break
        except Exception as e:
            logger.debug(f"[ AUDIO ] : WASAPI discovery failed: {e}")
            pass
            
        logger.debug("[ AUDIO ] : Initializing AudioHandler...")
            
        input_sample_rate = config.get('input_sample_rate', 16000)
        output_sample_rate = config.get('output_sample_rate', 44100)
        
        input_device_index = self._auto_select_device(config)
        output_device_index = config.get('output_device_index')
        
        logger.debug("[ AUDIO ] : Creating AudioInput...")
        self.input = AudioInput(
            sample_rate=input_sample_rate,
            channels=1,
            input_device_index=input_device_index,
            loop=self.loop
        )
        
        logger.debug("[ AUDIO ] : Creating AudioOutput...")
        self.output = AudioOutput(
            sample_rate=config.get('output_sample_rate', 24000), # Source rate from Gemini
            channels=1,
            output_device_index=output_device_index,
            loop=self.loop
        )
        logger.debug("[ AUDIO ] : Audio components created.")
        
        # Initialize volume from config
        if 'volume' in config:
            try:
                self.output.set_volume(float(config['volume']))
                # logger.info(f"[ AUDIO ] : Restored volume level to {config['volume']}")
            except Exception:
                pass

        # Push Architecture Event Listener
        self.device_listener = AudioDeviceListener(self._on_device_event)
        self.preferred_device_id = config.get('preferred_device_id') # New: Track by GUID
        # Fuzzy hardware lock for Bluetooth mics (AirPods often appear after boot).
        self.preferred_device_name = config.get('preferred_device_name')
        self._preferred_retry_handles: list = []

        # Device Cache & UI Callback
        try:
            self.cached_devices = sd.query_devices()
        except Exception:
            self.cached_devices = []
        self.ui_callback = None

    def set_ui_callback(self, callback):
        """Set callback for UI notifications (e.g. device detected)."""
        self.ui_callback = callback


    def _on_device_event(self, event_type: str, device_id: str):
        """Handle audio device events from the background thread."""
        logger.debug(f"[ AUDIO HANDLER ] : Device Event: {event_type} -> {device_id}")
        
        if event_type == "switch" or event_type == "switch_output":
            # Default OUTPUT device changed (OS Level) -> Hot Swap Output
            logger.info(f"[ AUDIO HANDLER ] : OS Default OUTPUT Device Changed. Swapping Audio Output...")
            
            if self.loop:
                self.loop.call_soon_threadsafe(lambda: self._hot_swap_output(device_id))
            else:
                self._hot_swap_output(device_id)
                
        elif event_type == "switch_input":
            # Default INPUT device changed (OS Level) -> Hot Swap Input
            logger.info(f"[ AUDIO HANDLER ] : OS Default INPUT Device Changed. Swapping Audio Input...")
            
            if self.loop:
                self.loop.call_soon_threadsafe(lambda: self._hot_swap_input(device_id))
            else:
                self._hot_swap_input(device_id)
        elif event_type in ("added", "active"):
            # Bluetooth (AirPods) often shows up as added/active *after* Chad started,
            # without always firing a default-input switch. Rebind preferred mic / toast.
            if self.loop:
                self.loop.call_soon_threadsafe(
                    lambda: self._handle_device_appeared(device_id, event_type)
                )
            else:
                self._handle_device_appeared(device_id, event_type)

    def _handle_device_appeared(self, device_id: str, event_type: str) -> None:
        """Re-scan after a device appears; rebind preferred input if it just showed up."""
        try:
            new_devices = sd.query_devices()
            old_names = {d.get("name") for d in (self.cached_devices or [])}
            added_devs = [
                d for d in new_devices
                if d.get("name") not in old_names and d.get("max_output_channels", 0) > 0
            ]
            self.cached_devices = new_devices

            if added_devs and self.ui_callback and event_type == "added":
                for dev in added_devs:
                    logger.info(f"[ AUDIO HANDLER ] : New Output Device Detected: {dev['name']}")
                    if self.loop:
                        self.loop.call_soon_threadsafe(
                            self.ui_callback, "detected", dev["name"], device_id
                        )

            # Prefer a configured hardware lock (Settings → Audio) when it appears.
            if self._rebind_preferred_input_if_available(reason=event_type):
                return

            # No preferred lock: if Windows default capture already moved to the new
            # device but we missed switch_input (common with BT), follow it.
            self._sync_input_to_os_default_if_drifted()
        except Exception as e:
            logger.error(f"Error handling appeared device: {e}")

    @staticmethod
    def _preferred_lock_name(device_name: str) -> str:
        """Store a brand-level lock so Hands-Free can win over Stereo later.

        Persisting the full "AirPods Pro Stereo" / "Headset (...Hands-Free...)"
        label prevents matching the real capture endpoint on reconnect.
        """
        name = (device_name or "").strip()
        # Common BT product token (AirPods / AirPods Pro / AirPods Pro 2).
        airpods = re.search(r"airpods?(?:\s*pro)?(?:\s*\d+)?", name, flags=re.IGNORECASE)
        if airpods:
            token = airpods.group(0)
            return re.sub(r"\s+", " ", token).strip().title().replace("Airpods", "AirPods")

        cleaned = re.sub(
            r"\((?:[^)]*(?:stereo|hands-?free|headset|ag\s+audio)[^)]*)\)",
            "",
            name,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(stereo|hands-?free(?:\s+ag\s+audio)?|headset|speakers?|microphone)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -()")
        return cleaned or name

    @staticmethod
    def _normalize_device_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())

    @staticmethod
    def _mic_endpoint_rank(name: str) -> int:
        """Prefer Hands-Free / Headset capture endpoints over Stereo-named ones."""
        lowered = (name or "").lower()
        if "hands-free" in lowered or "handsfree" in lowered or "headset" in lowered:
            return 0
        if "stereo" in lowered:
            return 2
        return 1

    def _find_preferred_input_index(self, preferred_name: str) -> Optional[int]:
        """Return the best WASAPI input index fuzzy-matching ``preferred_name``."""
        if not preferred_name:
            return None
        norm_preferred = self._normalize_device_name(preferred_name)
        if not norm_preferred:
            return None
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.debug(f"[ AUDIO ] : query_devices failed during preferred lookup: {e}")
            return None

        candidates = []
        for i, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) <= 0:
                continue
            if self.wasapi_index is not None and dev.get("hostapi") != self.wasapi_index:
                continue
            system_name = dev.get("name") or ""
            if norm_preferred in self._normalize_device_name(system_name):
                candidates.append((self._mic_endpoint_rank(system_name), i, system_name))

        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        _rank, idx, name = candidates[0]
        logger.info(f"[ AUDIO ] : Preferred input match: '{name}' (Index {idx})")
        return idx

    def _current_input_name(self) -> Optional[str]:
        try:
            idx = self.input.input_device_index
            if idx is None:
                info = sd.query_devices(kind="input")
                return info.get("name")
            return sd.query_devices(idx).get("name")
        except Exception:
            return None

    def _rebind_preferred_input_if_available(self, reason: str = "") -> bool:
        preferred = self.preferred_device_name or self.config.get("preferred_device_name")
        if not preferred:
            return False
        idx = self._find_preferred_input_index(preferred)
        if idx is None:
            return False
        if idx == self.input.input_device_index:
            return False
        current = self._current_input_name() or "?"
        logger.info(
            f"[ AUDIO HANDLER ] : Preferred mic available ({reason or 'scan'}) "
            f"— rebinding from '{current}' to index {idx}"
        )
        self._hot_swap_input(force_index=idx)
        return True

    def _sync_input_to_os_default_if_drifted(self) -> None:
        """If OS default capture moved and we aren't hardware-locked, follow it."""
        if self.preferred_device_name or self.config.get("preferred_device_name"):
            return
        try:
            default_info = sd.query_devices(kind="input")
            default_name = default_info.get("name")
            current_name = self._current_input_name()
            if not default_name or not current_name:
                return
            if self._normalize_device_name(default_name) == self._normalize_device_name(current_name):
                return
            logger.info(
                f"[ AUDIO HANDLER ] : OS default input drifted "
                f"('{current_name}' -> '{default_name}'); rebinding."
            )
            self._hot_swap_input()
        except Exception as e:
            logger.debug(f"[ AUDIO ] : default-drift sync skipped: {e}")

    def schedule_preferred_input_retries(self) -> None:
        """Retry preferred-mic bind after start — AirPods often enumerate late."""
        preferred = self.preferred_device_name or self.config.get("preferred_device_name")
        if not preferred or not self.loop:
            return
        # Cancel any prior schedule (re-start).
        for handle in self._preferred_retry_handles:
            try:
                handle.cancel()
            except Exception:
                pass
        self._preferred_retry_handles = []
        for delay in (1.0, 2.5, 5.0):
            handle = self.loop.call_later(
                delay,
                lambda d=delay: self._rebind_preferred_input_if_available(reason=f"retry@{d}s"),
            )
            self._preferred_retry_handles.append(handle)
        logger.info(
            f"[ AUDIO ] : Scheduled preferred-mic retries for '{preferred}' "
            f"(Bluetooth devices often appear after process start)."
        )


    def _hot_swap_output(self, device_id: str = None, force_device_name: str = None):
        """Restart audio output on the (new) default device or specific target."""
        try:
            # 1. Stop current stream
            self.output.stop()
            
            # 2. Determine target device index
            if force_device_name:
                # User specifically requested this device
                idx = self.output.device_manager.find_device_index_by_name(force_device_name)
                if idx is not None:
                     self.output.output_device_index = idx
                     logger.info(f"[ AUDIO HANDLER ] : Switching to user-selected device: {force_device_name} (Index {idx})")
                else:
                     logger.warning(f"[ AUDIO HANDLER ] : Could not find index for {force_device_name}, falling back to default.")
                     self.output.output_device_index = None
            else:
                # OS Default Switch
                self.output.output_device_index = None # Force Default
            
            # 3. CRITICAL: Re-discover the new device's capabilities
            # Force a re-scan of hardware to see the current default
            self.output.device_manager.refresh()
            
            if self.output.output_device_index is not None:
                self.output.device_manager.query_device(self.output.output_device_index)
                
            # Update output stream parameters from re-discovered device
            self.output.device_sample_rate = self.output.device_manager.get_native_sample_rate()
            self.output.device_channels = self.output.device_manager.get_native_channels()
            
            logger.info(f"[ AUDIO HANDLER ] : New device caps: {self.output.device_channels}ch @ {self.output.device_sample_rate}Hz")
            
            # 4. Restart
            self.output.start()
            logger.info(f"[ AUDIO HANDLER ] : Audio Output successfully switched.")
            
            # 5. Play confirmation sound through the NEW device
            if hasattr(self, 'on_output_changed') and self.on_output_changed:
                try:
                    self.on_output_changed()
                except Exception as sound_err:
                    logger.debug(f"Could not play switch confirmation: {sound_err}")
            
            # Notify UI
            if self.ui_callback and self.loop:
                 dev_name = self.output.device_manager.get_device_name()
                 self.loop.call_soon_threadsafe(self.ui_callback, "switched", dev_name, None)
            
        except Exception as e:
            logger.error(f"Failed to hot-swap audio device: {e}")

    def _hot_swap_input(self, device_id: str = None, force_index: Optional[int] = None):
        """Restart audio input on preferred mic, forced index, or OS default."""
        try:
            # 1. Stop current input stream
            self.input.stop()

            # 2. Prefer hardware lock / explicit index over blind OS default.
            preferred = self.preferred_device_name or self.config.get("preferred_device_name")
            target_index = force_index
            if target_index is None and preferred:
                target_index = self._find_preferred_input_index(preferred)
            self.input.input_device_index = target_index  # None => PortAudio default
            
            # 3. Small delay to allow Windows to finalize the device swap
            # Sounddevice sometimes caches the old 'default' if queried too fast
            time.sleep(0.8)
            
            # 4. Force re-scan of devices to ensure sounddevice sees the new default
            # We can use the existing output's device_manager to do the refresh
            if hasattr(self.output, 'device_manager'):
                self.output.device_manager.refresh()
            else:
                # Fallback if somehow not present
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass

            # 5. Restart input stream
            self.input.start()
            logger.info(
                f"[ AUDIO HANDLER ] : Audio Input switched "
                f"(index={self.input.input_device_index!r})."
            )

            # Persist so Settings "active" matches the live stream.
            try:
                idx = self.input.input_device_index
                if idx is not None:
                    self.config["input_device_index"] = idx
                    self._config_repo.update_preference("audio.input_device_index", idx)
                    info = sd.query_devices(idx)
                    raw = "".join(c for c in (info.get("name") or "") if c.isascii())
                    if raw and not (self.preferred_device_name or self.config.get("preferred_device_name")):
                        lock = self._preferred_lock_name(raw)
                        self.preferred_device_name = lock
                        self.config["preferred_device_name"] = lock
                        self._config_repo.update_preference("audio.preferred_device_name", lock)
            except Exception as persist_err:
                logger.debug(f"[ AUDIO ] : could not persist input index after swap: {persist_err}")
            
            # 6. Notify UI
            if self.ui_callback and self.loop:
                try:
                    if self.input.input_device_index is not None:
                        dev_name = sd.query_devices(self.input.input_device_index).get("name")
                    else:
                        dev_name = sd.query_devices(kind="input").get("name")
                    self.loop.call_soon_threadsafe(self.ui_callback, "switched_input", dev_name, None)
                except Exception as e:
                    logger.debug(f"Could not notify UI of input switch: {e}")
            
        except Exception as e:
            logger.error(f"Failed to hot-swap audio input device: {e}")

    def switch_output_device(self, device_id: str):
        """Public method to switch device (e.g. from UI)."""
        # Note: device_id is the GUID from the toast. ID lookup is hard, so we assume 
        # the user just wants to switch to whatever device initiated the toast.
        # But wait, we don't have the NAME stored for the GUID here.
        # However, _hot_swap_output(device_id) currently ignores device_id. This is a gap.
        # For MVP: Rescan devices and match the preferred name or just set to Default if the OS switched it.
        # Actually, if the user clicks "Connect", they likely made it the default or expect us to.
        # If we invoke _hot_swap_output, it goes to default. 
        # If the user plugged in headphones, Windows usually makes them default. 
        # So "Connect" effectively just acknowledges it and ensures we are using it.
        self._hot_swap_output(device_id)


    def _auto_select_device(self, config: dict) -> Optional[int]:
        """Fuzzy logic for device selection over sd.query_devices()."""
        logger.debug("[ AUDIO ] : Auto-selecting input device...")
        preferred_name = config.get('preferred_device_name')
        if preferred_name:
            idx = self._find_preferred_input_index(preferred_name)
            if idx is not None:
                return idx
            logger.info(
                f"[ AUDIO ] : Preferred device '{preferred_name}' not found yet "
                f"(common for AirPods at process start). Will retry / rebind on appear."
            )

        # 2. Validate cached index from config
        cached_index = config.get('input_device_index')
        if cached_index is not None:
            try:
                # Check if this index exists and is actually an input device
                dev_info = sd.query_devices(cached_index)
                if dev_info['max_input_channels'] > 0:
                    logger.info(f"[ AUDIO ] : Using cached input device index {cached_index}: '{dev_info['name']}'")
                    return cached_index
                else:
                    logger.warning(f"[ AUDIO ] : Cached device index {cached_index} ('{dev_info['name']}') is not an input device. Ignoring.")
            except Exception as e:
                logger.warning(f"[ AUDIO ] : Cached device index {cached_index} is invalid: {e}")

        # 3. Fallback to System Default
        try:
            default_input_index = sd.default.device[0]
            if default_input_index is not None and default_input_index >= 0:
                dev_info = sd.query_devices(default_input_index)
                logger.info(f"[ AUDIO ] : Falling back to System Default Input: '{dev_info['name']}' (Index {default_input_index})")
                return default_input_index
            else:
                logger.warning("[ AUDIO ] : No valid system default input device found.")
                return None
        except Exception as e:
            logger.error(f"[ AUDIO ] : Failed to get system default input: {e}")
            return None

    def get_audio_devices(self) -> list:
        """Matches legacy API for UI."""
        return self.get_available_devices()

    def get_available_devices(self) -> list:
        """Returns clean list of WASAPI input devices."""
        devices = []
        try:
            current_active = self.input.input_device_index
            if current_active is None:
                # If None, use sounddevice's internal default index for comparison
                try:
                    current_active = sd.default.device[0]
                except Exception:
                    current_active = -1

            all_devices = sd.query_devices()
            for i, dev in enumerate(all_devices):
                if dev['max_input_channels'] > 0:
                    if self.wasapi_index is not None and dev['hostapi'] != self.wasapi_index:
                        continue
                    devices.append({
                        "index": i,
                        "name": dev['name'],
                        "active": i == current_active
                    })
        except Exception as e:
            logger.error(f"Error getting audio devices: {e}")
        return devices

    def set_audio_device(self, index: int) -> bool:
        """Hot-swap audio input device."""
        try:
            logger.debug(f"[ SYSTEM ] : Switching audio input to device index {index}")
            info = sd.query_devices(index)
            raw_name = info.get('name', 'Unknown')
            clean_name = "".join(char for char in raw_name if char.isascii())
            lock_name = self._preferred_lock_name(clean_name)

            # Re-resolve to Hands-Free if the user clicked a Stereo sibling.
            better = self._find_preferred_input_index(lock_name)
            if better is not None:
                index = better
                info = sd.query_devices(index)
                clean_name = "".join(
                    char for char in info.get("name", clean_name) if char.isascii()
                )
                lock_name = self._preferred_lock_name(clean_name)

            self.input.stop()
            self.input.input_device_index = index
            self.input.start()

            self.preferred_device_name = lock_name
            self.config["preferred_device_name"] = lock_name
            self.config["input_device_index"] = index
            self._config_repo.update_preference("audio.input_device_index", index)
            self._config_repo.update_preference("audio.preferred_device_name", lock_name)
            
            logger.info(
                f"[ AUDIO ] : Fuzzy hardware lock '{lock_name}' "
                f"(opened '{clean_name}' index {index})."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set audio device: {e}")
            return False

    def set_volume(self, volume: float) -> None:
        """Set output volume."""
        self.output.set_volume(volume)
        
    def get_volume(self) -> float:
        """Get output volume."""
        return self.output.get_volume()

    async def start_input(self) -> None:
        """Start only the audio input stream (Async)."""
        import asyncio
        await asyncio.get_event_loop().run_in_executor(None, self.input.start)
        self.schedule_preferred_input_retries()

    async def start_output(self) -> None:
        """Start only the audio output stream and device listener (Async)."""
        import asyncio
        await asyncio.get_event_loop().run_in_executor(None, self.output.start)
        self.device_listener.start()
        # Retries may already be scheduled from start_input; ensure once either way.
        self.schedule_preferred_input_retries()

    async def start(self) -> None:
        """Start both input and output streams (Async)."""
        await self.start_input()
        await self.start_output()

    async def stop(self) -> None:
        """Stop both input and output streams (Async)."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.input.stop)
        await loop.run_in_executor(None, self.output.stop)
        self.device_listener.stop()

    async def cleanup(self) -> None:
        """Clean up all audio resources (Async)."""
        await self.stop()
        self.input.cleanup()
        self.output.cleanup()


# ==========================================
# Runtime Simulation Mocks
# ==========================================

class MockInput:
    """Simulates AudioInput for headless environments."""
    def __init__(self): 
        self.is_active = False
    
    def start(self, *args, **kwargs): pass
    def stop(self, *args, **kwargs): pass
    def mute_input(self, *args, **kwargs): pass
    def unmute_input(self, *args, **kwargs): pass
    def clear_queue(self, *args, **kwargs): pass


class MockOutput:
    """Simulates AudioOutput for headless environments."""
    def __init__(self): 
        self.is_playing = False
        
    def start(self, *args, **kwargs): pass
    def stop(self, *args, **kwargs): pass
    async def play(self, *args, **kwargs): pass
    def clear_queue(self, *args, **kwargs): pass
    def play_sound_directly(self, *args, **kwargs): pass


class MockAudio:
    """Simulates AudioHandler container for headless environments."""
    def __init__(self):
        self.input = MockInput()
        self.output = MockOutput()
