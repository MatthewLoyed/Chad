"""Bounded, read-only llmfit evidence for Chad's exact curated artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from src.core.tools.limits import get as limit
from src.infrastructure.services.model_recommendations import catalog, number

ROOT = Path(__file__).resolve().parents[3]


def executable():
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    return base / ".tools/llmfit/llmfit.exe"


def _run(exe, args, env):
    result = subprocess.run([str(exe), *args, "--no-cluster", "--no-dashboard"],
                            env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=limit("model_assessment_timeout"),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("llmfit did not finish assessment")
    return json.loads(result.stdout)


def entries():
    # No guessed conversion of Bonsai Q1_0 to conventional Q2/Q4 quantization.
    return [{"name": "chad/" + m["id"], "provider": "chad-curated",
             "parameter_count": str(m["parameters_b"]) + "B",
             "parameters_raw": int(m["parameters_b"] * 1e9),
             "min_ram_gb": m["size_bytes"] / 1e9, "recommended_ram_gb": m["size_bytes"] / 1e9 * 2,
             "min_vram_gb": m["size_bytes"] / 1e9, "quantization": m["quantization"],
             "context_length": m["max_context"], "use_case": "Chat",
             "num_hidden_layers": m["layers"], "num_key_value_heads": m["kv_heads"],
             "head_dim": m["head_dim"], "format": "gguf"}
            for m in catalog() if not m.get("speed_unverified")]


def assess(hardware, context):
    exe = executable()
    fallback = {"engine": "chad", "version": None, "models": {},
                "note": "llmfit unavailable; using Chad's conservative assessment."}
    if not exe.is_file():
        return fallback
    try:
        # Isolate caches/configuration from the user's own llmfit installation.
        with tempfile.TemporaryDirectory(prefix="chad-assessment-") as temp:
            env = {**os.environ, "LLMFIT_CUSTOM_MODELS": str(Path(temp) / "models.json"),
                   "XDG_DATA_HOME": temp, "XDG_CONFIG_HOME": temp, "APPDATA": temp,
                   "LLMFIT_BENCH_STORE": str(Path(temp) / "benchmarks")}
            Path(env["LLMFIT_CUSTOM_MODELS"]).write_text(json.dumps(entries()), encoding="utf-8")
            version = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                                     timeout=limit("hardware_probe_timeout"),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            pinned = json.loads((ROOT / "config/llmfit.json").read_text())["version"]
            if version.returncode or version.stdout.strip() != "llmfit " + pinned:
                return fallback
            args = []
            ram = number(hardware.get("ram_gb"))
            if ram:
                args += ["--ram", f"{ram}GiB"]
            models = {}
            for model in entries():
                # plan pins quantization and context; fit auto-selects a different
                # quantization even for a curated file, so cannot certify it.
                result = _run(exe, ["plan", model["name"], "--json", "--context", str(context),
                                    "--quant", model["quantization"], *args], env)
                if not isinstance(result, dict) or result.get("quantization") != model["quantization"] or result.get("context") != context:
                    continue
                current = result.get("current") or {}
                speed = number(current.get("estimated_tps"))
                if speed:
                    models[model["name"].removeprefix("chad/")] = {
                        "tokens_per_second": speed, "confidence": "estimated",
                        "run_mode": current.get("run_mode")}
            return {"engine": "llmfit", "version": pinned, "models": models,
                    "note": "llmfit performance evidence; Chad validates exact files and combined role budgets separately."}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return fallback


def enrich(result):
    evidence = assess(result["hardware"], result["context"])
    for candidate in result["models"]:
        data = evidence["models"].get(candidate["id"])
        same_mode = data and ((candidate["mode"] == "cpu" and data["run_mode"] == "CpuOnly")
                              or (candidate["mode"] == "gpu" and data["run_mode"] == "Gpu"))
        if same_mode and not candidate.get("speed_unverified"):
            candidate["speed"] = {"kind": "estimated", "low": round(data["tokens_per_second"]),
                                  "high": round(data["tokens_per_second"]), "source": "llmfit",
                                  "confidence": data["confidence"],
                                  "note": "llmfit estimate for one request; validate this artifact and loaded settings locally."}
    result["assessment_engine"] = {k: v for k, v in evidence.items() if k != "models"}
    return result
