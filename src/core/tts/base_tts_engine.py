from abc import ABC, abstractmethod

class BaseTTSEngine(ABC):
    """
    Abstract base class for all TTS synthesis engines.
    Engines are responsible only for converting text to raw PCM bytes.
    """
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Returns raw PCM int16 audio bytes at 24000Hz"""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Checks if the engine is ready for use (keys, cooldowns, etc.)"""
        pass
