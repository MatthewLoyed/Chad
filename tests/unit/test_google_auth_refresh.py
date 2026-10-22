from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.tools.workspace.auth import RefreshGoogleAuthTool


@pytest.mark.asyncio
async def test_refresh_uses_existing_credentials_without_opening_signin():
    tool = RefreshGoogleAuthTool()
    tool._manager = SimpleNamespace(authenticate=Mock(return_value=(True, '')), run_local_auth_flow=Mock())
    assert (await tool.execute(reason='Check Docs authorization'))['success']
    tool._manager.run_local_auth_flow.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [True, False])
async def test_rejected_refresh_falls_back_to_existing_signin_flow(accepted):
    tool = RefreshGoogleAuthTool()
    tool._manager = SimpleNamespace(authenticate=Mock(return_value=(False, 'invalid_grant')),
                                    run_local_auth_flow=Mock(return_value=accepted))
    assert (await tool.execute(reason='Restore Docs authorization'))['success'] is accepted
    tool._manager.run_local_auth_flow.assert_called_once()
