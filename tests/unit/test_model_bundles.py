import copy
from src.infrastructure.services.model_bundles import bundles, FAST, BONSAI, SELECTOR, SMALL


def hardware(vram=16, ram=32, free=None, storage=100):
    return {"ram_gb": ram, "available_ram_gb": ram - 5, "storage_free_gb": storage,
            "gpus": [{"name": "Example GPU", "vram_gb": vram, "free_vram_gb": free, "shared_memory": False}] if vram else []}


def test_16gb_prefers_bonsai_with_separate_fast_and_selector_roles():
    result = bundles(hardware())
    primary = result['bundles'][0]
    assert primary['primary'] == BONSAI and primary['secondary'] == FAST and primary['selector'] == SELECTOR
    assert len(result['models']) <= 2
    assert primary['residency'] == 'main-and-selector'
    assert primary['runtime_verified'] is False and primary['tools_ready'] is False
    assert 'runtime' in primary['runtime_note']
    assert result['models'][0]['speed']['kind'] == 'unknown'


def test_smaller_card_gets_qwen9b_and_secondary_alias_without_duplicate_assets():
    plan = bundles(hardware(vram=12))['bundles'][0]
    assert plan['primary'] == FAST and plan['secondary'] == FAST
    assert [a['catalog_id'] for a in plan['assets']] == [FAST, SELECTOR]


def test_reuses_installed_artifacts_when_storage_cannot_hold_new_bundle():
    h = hardware(storage=2.1)
    assert not bundles(h)['bundles']
    result = bundles(h, installed={FAST: 'existing-9b', BONSAI: 'existing-bonsai', SELECTOR: 'existing-selector'})
    plan = result['bundles'][0]
    assert plan['new_bytes'] == 0 and all(a['installed'] for a in plan['assets'])


def test_low_memory_gets_small_fallback_and_never_fake_gpu_or_speed():
    result = bundles(hardware(vram=0, ram=12))
    assert result['recommended_id'] == SMALL
    assert not result['target_met']
    assert all(m['mode'] == 'cpu' for m in result['models'])


def test_unknown_and_busy_gpu_do_not_override_host_memory_or_allow_pooling():
    assert not bundles(hardware(vram=48, ram=4))['bundles']
    h = hardware(vram=8)
    h['gpus'].append(copy.deepcopy(h['gpus'][0]))
    assert all(p['primary'] != BONSAI for p in bundles(h)['bundles'])
    assert bundles(hardware(free=1))['bundles'][0]['residency'] == 'sequential'


def test_more_context_changes_bundle_budget_and_residency():
    low = bundles(hardware(), 4096)['bundles'][0]
    high = bundles(hardware(), 32768)['bundles'][0]
    assert high['runtime_budget_gb'] > low['runtime_budget_gb']
    assert sum(a['required_gb'] for a in high['assets']) > sum(a['required_gb'] for a in low['assets'])
