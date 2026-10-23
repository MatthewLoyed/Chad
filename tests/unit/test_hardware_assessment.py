"""Hardware probes must degrade honestly without GPU tools or complete drivers."""
import json

from src.infrastructure.services import hardware_assessment as hw


def test_nvidia_mib_conversion_and_malformed_rows(monkeypatch):
    monkeypatch.setattr(hw, "_run", lambda args: 'Example GPU, 24576, 16384\nbroken\nGPU, N/A, N/A')
    assert hw._nvidia() == [{"name": "Example GPU", "vendor": "nvidia", "vram_gb": 24,
                             "free_vram_gb": 16, "source": "nvidia-smi", "shared_memory": False}]


def test_windows_does_not_use_truncated_adapter_ram(monkeypatch):
    monkeypatch.setattr(hw, "_run", lambda args: json.dumps([
        {"Name": "Example Radeon RX 9000", "AdapterCompatibility": "AMD"},
        {"Name": "Integrated GPU", "AdapterCompatibility": "Intel"},
    ]))
    monkeypatch.setattr(hw, "_windows_gpu_memory", lambda: {"example radeon rx 9000": 24, "integrated gpu": 8})
    result = hw._windows_gpus()
    assert result[0]["vram_gb"] == 24
    assert result[1]["vram_gb"] is None
    assert result[1]["shared_memory"]


def test_no_gpu_still_returns_ram_and_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(hw, "_nvidia", lambda: [])
    monkeypatch.setattr(hw, "_windows_gpus", lambda: [])
    result = hw.assess_hardware(str(tmp_path / "future-models"))
    assert result["gpus"] == []
    assert result["scope"] == "local_process_host"
    assert result["storage_free_gb"] > 0
    assert any("CPU-only" in warning for warning in result["warnings"])


def test_probe_failure_is_bounded_and_nonfatal(monkeypatch):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] > 0
        assert kwargs.get("shell") is not True
        raise hw.subprocess.TimeoutExpired("probe", 1)
    monkeypatch.setattr(hw.subprocess, "run", fail)
    assert hw._run(["missing"]) == ""
