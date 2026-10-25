import copy

import pytest

from src.infrastructure.services.model_recommendations import catalog, recommend


def hardware(ram=32, vram=16, free=None):
    return {"ram_gb": ram, "available_ram_gb": ram - 5, "storage_free_gb": 100,
            "gpus": [] if not vram else [{"name": "NVIDIA GeForce RTX 4070", "vram_gb": vram,
                                         "free_vram_gb": free, "shared_memory": False}]}


def test_cpu_only_remains_usable_without_fake_speed():
    result = recommend(hardware(vram=0))
    assert result["recommended_id"]
    assert not result["target_met"]
    assert all(m["mode"] == "cpu" and m["speed"]["kind"] == "unknown" for m in result["models"])


def test_context_changes_kv_and_gpu_fit():
    low = recommend(hardware(vram=8), 4096)["models"]
    high = recommend(hardware(vram=8), 32768)["models"]
    by_id = {m["id"]: m for m in high}
    assert any(m["mode"] == "gpu" and by_id[m["id"]]["mode"] == "cpu" for m in low)
    assert all(by_id[m["id"]]["kv_gb"] > m["kv_gb"] for m in low)


def test_busy_gpu_and_shared_memory_are_not_free_capacity():
    h = hardware(free=1)
    assert all(m["mode"] == "cpu" for m in recommend(h)["models"])
    h["gpus"][0].update(shared_memory=True, free_vram_gb=100)
    assert all(m["mode"] == "cpu" for m in recommend(h)["models"])


def test_multiple_gpus_are_not_summed():
    h = hardware(vram=4)
    h["gpus"].append(copy.deepcopy(h["gpus"][0]))
    result = recommend(h)
    assert all(m["mode"] == "cpu" for m in result["models"] if m["required_gb"] > 3)


@pytest.mark.parametrize("field,value", [("ram_gb", None), ("ram_gb", float('nan')), ("storage_free_gb", 0)])
def test_unknown_or_insufficient_memory_storage_explained(field, value):
    h = hardware()
    h[field] = value
    result = recommend(h)
    assert result["recommended_id"] is None
    assert all(m["reasons"] for m in result["models"])


def test_low_ram_cannot_be_fixed_by_large_gpu():
    assert not recommend(hardware(ram=4, vram=48))["recommended_id"]


def test_catalog_has_exact_artifacts_sources_and_revisions():
    for model in catalog():
        assert len(model["revision"]) == 40
        assert model["size_bytes"] > 0
        assert model["filename"].endswith('.gguf')
        assert model["source"].startswith('https://huggingface.co/')


def test_invalid_context_rejected():
    with pytest.raises(ValueError):
        recommend(hardware(), 1_000_000)
