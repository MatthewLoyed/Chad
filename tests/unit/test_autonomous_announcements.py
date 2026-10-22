import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.clients.desktop.api.audio import AudioAPIMixin
from src.remote.protocol import ChadMessage, MessageType
from src.shared.config_manager import ConfigManager


class DummyAudioAPI(AudioAPIMixin):
    def __init__(self, config_repo):
        self._config_repo = config_repo

    def main_window_ref(self):
        return None


def test_audio_api_get_and_set_autonomous_announcements(tmp_path, monkeypatch):
    """Test get and set of autonomous announcements preference."""
    mock_repo = MagicMock()
    api = DummyAudioAPI(config_repo=mock_repo)

    # 1. Default should be True if not explicitly set
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: True if key == "tts.announce_autonomous" else default)
    assert api.get_autonomous_announcements() is True

    # 2. When set to False, update_preference is called with tts.announce_autonomous
    res = api.set_autonomous_announcements(False)
    assert res == {"success": True, "enabled": False}
    mock_repo.update_preference.assert_called_with("tts.announce_autonomous", False)

    # 3. When ConfigManager returns False, get returns False
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: False if key == "tts.announce_autonomous" else default)
    assert api.get_autonomous_announcements() is False


@pytest.mark.asyncio
async def test_gui_limb_autonomous_notification_muted(monkeypatch):
    """When announce_autonomous is False, TTS speak is not called but push_thinking_event is."""
    from src.remote.gui_limb import GUIChadLimb

    mock_window = MagicMock()
    mock_window.push_thinking_event = MagicMock()
    mock_window.push_log = MagicMock()
    limb = GUIChadLimb(main_window=mock_window, server_url="ws://dummy:8788")

    # Mute autonomous announcements
    monkeypatch.setattr(
        ConfigManager,
        "get",
        lambda key, default=None: False if key == "tts.announce_autonomous" else default,
    )

    mock_speak = AsyncMock()
    mock_player = MagicMock()
    mock_player.speak = mock_speak

    with patch("src.core.tts.tts_player.get_tts_player", return_value=mock_player):
        msg = ChadMessage(
            MessageType.AUTONOMOUS_NOTIFICATION,
            {"text": "Background task finished successfully"},
        )
        await limb.handle_message(msg)
        # Give event loop a turn for any spawned tasks
        await asyncio.sleep(0.01)

        # TTS speak should NOT be called
        mock_speak.assert_not_called()
        # Visual toast still triggered
        limb.window.push_thinking_event.assert_called_once_with(
            {"type": "autonomous_notification", "text": "Background task finished successfully"}
        )


@pytest.mark.asyncio
async def test_gui_limb_autonomous_notification_unmuted(monkeypatch):
    """When announce_autonomous is True, TTS speak IS called."""
    from src.remote.gui_limb import GUIChadLimb

    mock_window = MagicMock()
    mock_window.push_thinking_event = MagicMock()
    mock_window.push_log = MagicMock()
    limb = GUIChadLimb(main_window=mock_window, server_url="ws://dummy:8788")

    # Enable autonomous announcements
    monkeypatch.setattr(
        ConfigManager,
        "get",
        lambda key, default=None: True if key == "tts.announce_autonomous" else default,
    )

    mock_speak = AsyncMock()
    mock_player = MagicMock()
    mock_player.speak = mock_speak

    with patch("src.core.tts.tts_player.get_tts_player", return_value=mock_player):
        msg = ChadMessage(
            MessageType.AUTONOMOUS_NOTIFICATION,
            {"text": "Background task finished successfully"},
        )
        await limb.handle_message(msg)
        await asyncio.sleep(0.01)

        # TTS speak SHOULD be called
        mock_speak.assert_called_once_with("Background task finished successfully")
        limb.window.push_thinking_event.assert_called_once()
