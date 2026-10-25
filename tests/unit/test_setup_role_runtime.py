from unittest.mock import AsyncMock
import pytest
from src.infrastructure.services.setup_role_runtime import checked_role
from src.core.intelligence.providers import LMStudioLifecycle


@pytest.mark.asyncio
async def test_loaded_settings_must_match_budget_without_reloading_or_changing_defaults(monkeypatch):
    config = {'context_length': 8192, 'parallel': 4, 'eval_batch_size': 512}
    native = AsyncMock(side_effect=lambda **kwargs: [{'key': 'main', 'loaded_instances': [{'id': 'main', 'config': config}]}])
    monkeypatch.setattr(LMStudioLifecycle, '_native_models', native)
    ensure = AsyncMock(side_effect=AssertionError('Already loaded model must not reload'))
    release = AsyncMock(side_effect=AssertionError('External loaded model must not unload'))
    monkeypatch.setattr('src.core.intelligence.model_lifecycle.ensure_resident', ensure)
    monkeypatch.setattr('src.core.intelligence.model_lifecycle.release', release)
    operation = AsyncMock(return_value={'success': True})
    assert not (await checked_role('http://localhost:1234/v1', 'main', 8192, operation))['success']
    operation.assert_not_called()
    config['parallel'] = 1
    config['eval_batch_size'] = None
    assert not (await checked_role('http://localhost:1234/v1', 'main', 8192, operation))['success']
    config['eval_batch_size'] = 512
    assert (await checked_role('http://localhost:1234/v1', 'main', 8192, operation))['success']
    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_inventory_never_triggers_a_blind_model_load(monkeypatch):
    monkeypatch.setattr(LMStudioLifecycle, '_native_models', AsyncMock(return_value=None))
    ensure = AsyncMock()
    monkeypatch.setattr('src.core.intelligence.model_lifecycle.ensure_resident', ensure)
    assert not (await checked_role('http://localhost:1234/v1', 'main', 8192, AsyncMock()))['success']
    ensure.assert_not_called()
