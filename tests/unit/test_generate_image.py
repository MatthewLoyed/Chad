import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.tools.visual.generate_image import GenerateImageTool, SD15_RESOLUTIONS, SDXL_RESOLUTIONS


@pytest.mark.asyncio
async def test_ensure_comfyui_running_when_already_active():
    tool = GenerateImageTool()
    mock_client = AsyncMock()
    mock_client.get.return_value = MagicMock(status_code=200)

    is_ready = await tool._ensure_comfyui_running(mock_client, "http://127.0.0.1:8188")
    assert is_ready is True
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_comfyui_running_skips_when_autostart_disabled():
    tool = GenerateImageTool()
    mock_client = AsyncMock()
    mock_client.get.return_value = MagicMock(status_code=500)

    with patch("src.tools.visual.generate_image.ConfigManager.get", return_value=False):
        is_ready = await tool._ensure_comfyui_running(mock_client, "http://127.0.0.1:8188")
        assert is_ready is False


def test_aspect_ratio_presets():
    assert SD15_RESOLUTIONS["landscape"] == (768, 512)
    assert SD15_RESOLUTIONS["portrait"] == (512, 768)
    assert SDXL_RESOLUTIONS["landscape"] == (1216, 832)
    assert SDXL_RESOLUTIONS["square"] == (1024, 1024)
