import asyncio
import time
from google import genai
from google.genai import types
from src.core.tts.base_tts_engine import BaseTTSEngine
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("GeminiTTSEngine")

class GeminiTTSEngine(BaseTTSEngine):
    """
    Cloud-based TTS engine using Gemini 3.1 Flash.
    Features natural prosody and a playful persona.
    """
    def __init__(self):
        # 1. Load Config
        api_key = ConfigManager.get_secret("GEMINI_API_KEY")
        self._enabled = bool(api_key)
        
        # 2. Initialize Gemini Client
        if api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = None
            logger.error("GEMINI_API_KEY not found in environment. Gemini TTS restricted.")

        self._model_name = ConfigManager.get("tts.model", "gemini-3.1-flash-tts-preview")
        self._voice_name = ConfigManager.get("tts.gemini_voice", "Puck")
        
        # 3. Rate limiting / Cooldown state
        self._last_429_time = 0
        self._cooldown_duration = 60 # seconds

    def is_available(self) -> bool:
        if not self._enabled or not self._client:
            return False
        
        # Check if we are in a 429 cooldown period
        elapsed = time.time() - self._last_429_time
        if elapsed < self._cooldown_duration:
            return False
            
        return True

    async def synthesize(self, text: str) -> bytes:
        if not self.is_available():
            return b""

        try:
            # Request audio output from Gemini
            voice_config = types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice_name)
            )
            
            # Persona Prompt
            prompt = f"""You are Chad, a sharp and witty AI assistant with a confident, slightly playful personality. 
Speak naturally and expressively, like you're talking to a friend. Be concise.

{text}"""
            
            # Call Gemini TTS (wrapped in thread as it's a blocking client call)
            response = await asyncio.to_thread(
                self._client.models.generate_content,
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=voice_config)
                )
            )

            # Extract PCM audio bytes
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data:
                        return part.inline_data.data
            
            return b""

        except Exception as e:
            if "429" in str(e):
                logger.warning("Gemini TTS Rate Limited (429). Entering 60s cooldown.")
                self._last_429_time = time.time()
            else:
                logger.error(f"Gemini TTS Synthesis Failed: {e}")
            return b""
