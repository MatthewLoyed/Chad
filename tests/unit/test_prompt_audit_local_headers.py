from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.prompt_audit import _split_fragments, _check_memory_architecture, run_prompt_audit


def test_local_headers_preserve_memory_policy_and_ignore_inner_headings():
    text = '### identity\nIdentity.\n### memory_policy\nPolicy.\n### when_to_save\nApproval needed.\n### supervisor\nRole.'
    fragments = _split_fragments(text, {'identity', 'memory_policy', 'supervisor'})
    assert [name for name, _ in fragments] == ['identity', 'memory_policy', 'supervisor']
    assert '### when_to_save' in fragments[1][1]
    assert not _check_memory_architecture(text, fragments, 'supervisor')
    missing = _split_fragments('### identity\nIdentity.', {'identity'})
    assert any('missing_memory_policy' in failure for failure in _check_memory_architecture('', missing, 'supervisor'))


def test_legacy_cloud_headers_remain_readable():
    assert _split_fragments('### PERSONA COMPONENT: memory_policy (Source: Cloud Master)\nPolicy.') == [('memory_policy', 'Policy.')]


@pytest.mark.asyncio
async def test_missing_local_assets_fail_audit_instead_of_using_mock_prompts(monkeypatch):
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    from src.shared.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, 'load_config', lambda: {})
    monkeypatch.setattr(PromptRegistry, 'get_instance', lambda: SimpleNamespace(warmup=AsyncMock(side_effect=OSError('missing assets'))))
    cases, results = await run_prompt_audit(False, 'unused')
    assert len(cases) == len(results) == 1
    assert not results[0].passed
    assert 'missing assets' in results[0].error
