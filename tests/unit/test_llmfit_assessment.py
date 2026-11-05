"""Contract boundaries: fixed artifacts, unsupported formats and graceful fallback."""
from types import SimpleNamespace
import subprocess

import pytest

from src.infrastructure.services import llmfit_assessment as llm


def test_missing_helper_keeps_setup_available(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "executable", lambda: tmp_path / "missing.exe")
    assert llm.assess({}, 8192)["engine"] == "chad"


@pytest.mark.parametrize("problem", [ValueError("bad JSON"), subprocess.TimeoutExpired("helper", 1), OSError()])
def test_failed_helper_falls_back(monkeypatch, tmp_path, problem):
    exe = tmp_path / "helper.exe"
    exe.touch()
    monkeypatch.setattr(llm, "executable", lambda: exe)
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="llmfit 1.1.20"))
    def broken(*args):
        raise problem
    monkeypatch.setattr(llm, "_run", broken)
    assert llm.assess({}, 8192)["engine"] == "chad"


def test_fixed_quant_and_context_only_and_no_bonsai_guess(monkeypatch, tmp_path):
    exe = tmp_path / "helper.exe"
    exe.touch()
    monkeypatch.setattr(llm, "executable", lambda: exe)
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="llmfit 1.1.20"))
    calls = []
    def run(exe, args, env):
        calls.append(args)
        assert "--share" not in args and "--quant" in args
        assert "LLMFIT_CUSTOM_MODELS" in env
        return {"context": 8192, "quantization": args[args.index("--quant") + 1],
                "current": {"estimated_tps": 35.5, "run_mode": "CpuOnly"}}
    monkeypatch.setattr(llm, "_run", run)
    result = llm.assess({"ram_gb": 32}, 8192)
    assert result["engine"] == "llmfit"
    assert result["models"]["qwen3.5-9b"]["tokens_per_second"] == 35.5
    assert "bonsai-27b-q1" not in result["models"]
    assert all("bonsai" not in str(args) for args in calls)


def test_different_execution_path_cannot_replace_speed(monkeypatch):
    monkeypatch.setattr(llm, "assess", lambda *a: {"engine": "llmfit", "models": {
        "example": {"run_mode": "CpuOnly", "tokens_per_second": 4, "confidence": "estimated"}}})
    result = {"hardware": {}, "context": 8192, "models": [{"id": "example", "mode": "gpu", "speed": {"kind": "unknown"}}]}
    assert llm.enrich(result)["models"][0]["speed"]["kind"] == "unknown"
