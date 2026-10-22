from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
from src.shared.config_manager import ConfigManager
from src.core.orchestration.context import session_id_var


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', [False, True])
async def test_stale_hint_requires_an_actual_error_not_a_url_or_page_word(monkeypatch, failed):
    original = ConfigManager.get
    monkeypatch.setattr(ConfigManager, 'get', lambda key, default=None:
        'allow_all' if key == 'tools.browser_approval_mode' else original(key, default))
    text = '### Error\nStale ref; inspect the current snapshot' if failed else '### Page\nPage URL: http://fixture.test/stale\n- paragraph: stale bread'
    server = SimpleNamespace(name='playwright', call_tool=AsyncMock(return_value=[{'type': 'text', 'text': text}]))
    token = session_id_var.set('stale-hint-test')
    try:
        result = await MCPExecutor().execute({'name': 'browser_snapshot', 'mcp_server': server}, {})
        output = str(result)
        assert ('HINT: this ref is stale' in output) is failed
        assert result['success'] is not failed
    finally:
        session_id_var.reset(token)
