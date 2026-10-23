"""Pure, offline candidate ranking. Memory fit and speed evidence stay separate."""
from __future__ import annotations

import json
import math
from pathlib import Path

GIB = 1024 ** 3
# Approximate desktop reference bandwidth, GB/s. These are not measured token
# rates. Unknown/mobile variants intentionally receive no speed prediction.
BANDWIDTH = {"rtx 5090": 1792, "rtx 4090": 1008, "rtx 3090": 936,
             "rtx 4080": 717, "rtx 4070": 504, "rtx 4060": 272,
             "rx 9070 xt": 640, "rx 7900 xtx": 960, "rx 7800 xt": 624}
OS_RESERVE_GB = 4.0
OS_RESERVE_FRACTION = 0.15
VRAM_RESERVE_GB = 1.0
VRAM_RESERVE_FRACTION = 0.15
EMBEDDING_RAM_GB = 3.0
RUNTIME_GB = 0.75
STORAGE_RESERVE_GB = 2.0
TARGET_TPS = 40


def catalog() -> list[dict]:
    # Source assets live beside the installed code, not in a user's CHAD_HOME.
    path = Path(__file__).resolve().parents[3] / "config" / "hardware_models.json"
    return json.loads(path.read_text(encoding="utf-8"))["models"]


def number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def _ram_budget(hardware: dict) -> float | None:
    total = number(hardware.get("ram_gb"))
    available = number(hardware.get("available_ram_gb"))
    if total is None:
        return None
    usable = max(0, total - max(OS_RESERVE_GB, total * OS_RESERVE_FRACTION))
    return min(usable, available) if available is not None else usable


def _gpu_budget(gpu: dict) -> float:
    total = number(gpu.get("vram_gb"))
    if total is None or gpu.get("shared_memory"):
        return 0
    budget = max(0, total - max(VRAM_RESERVE_GB, total * VRAM_RESERVE_FRACTION))
    free = number(gpu.get("free_vram_gb"))
    return min(budget, free) if free is not None else budget


def _speed(model: dict, gpu: dict | None, weights: float) -> dict:
    name = str((gpu or {}).get("name", "")).lower()
    bandwidth = None if "laptop" in name or "mobile" in name else next(
        (bw for key, bw in sorted(BANDWIDTH.items(), key=lambda item: -len(item[0])) if name.endswith(key)), None)
    if bandwidth is None:
        return {"kind": "unknown", "low": None, "high": None,
                "note": "Benchmark required; no calibrated estimate for this hardware or CPU offload."}
    # Deliberately broad envelope: single-request, short-context decode only.
    # Does not claim to model prefill, reasoning time or long-context attention.
    active_ratio = model.get("active_parameters_b", model["parameters_b"]) / model["parameters_b"]
    active_weights = weights * active_ratio
    return {"kind": "estimated", "low": round(bandwidth / active_weights * 0.25),
            "high": round(bandwidth / active_weights * 0.5),
            "note": "Bandwidth heuristic, short-context generation; validate locally. Not a benchmark."}


def recommend(hardware: dict, context: int = 8192, models: list[dict] | None = None) -> dict:
    if isinstance(context, bool) or context not in (4096, 8192, 16384, 32768):
        raise ValueError("Choose 4096, 8192, 16384 or 32768 context tokens.")
    ram = _ram_budget(hardware)
    storage = number(hardware.get("storage_free_gb"))
    # Individual GPUs are evaluated separately. There is no implicit pooling.
    gpu = max(hardware.get("gpus") or [], key=_gpu_budget, default=None)
    gpu_budget = _gpu_budget(gpu) if gpu else 0
    candidates = []
    for model in models if models is not None else catalog():
        weights = model["size_bytes"] / GIB
        # FP16 K+V bound. Hybrid attention uses this conservative full-attention
        # bound until the installed engine provides an estimate for the artifact.
        kv = 2 * model["layers"] * model["kv_heads"] * model["head_dim"] * 2 * context / GIB
        required = weights + kv + RUNTIME_GB
        mode = "gpu" if required <= gpu_budget else "cpu"
        # Reserve host space for mapped weights as well as CPU embeddings. A
        # fully offloaded model can still need RAM while loading or staging.
        ram_needed = EMBEDDING_RAM_GB + (weights + RUNTIME_GB if mode == "gpu" else required)
        reasons = []
        if ram is None:
            reasons.append("System RAM is unknown; enter it manually to assess fit.")
        elif ram_needed > ram:
            reasons.append("Insufficient available RAM after OS and embedding headroom.")
        if storage is None:
            reasons.append("Model storage is unknown; check the destination before downloading.")
        elif storage < weights + STORAGE_RESERVE_GB:
            reasons.append("Insufficient free model storage including download headroom.")
        if context > model["max_context"]:
            reasons.append("Requested context exceeds the catalog's validated context.")
        speed = _speed(model, gpu if mode == "gpu" else None, weights)
        target = not reasons and speed["low"] is not None and speed["low"] >= TARGET_TPS
        candidates.append({**model, "context": context, "weights_gb": round(weights, 2),
                           "kv_gb": round(kv, 2), "required_gb": round(required, 2),
                           "ram_needed_gb": round(ram_needed, 2), "mode": mode,
                           "gpu_name": gpu["name"] if mode == "gpu" else None,
                           "fits": not reasons, "reasons": reasons, "speed": speed,
                           "target_likely": target,
                           "settings": {"context_length": context,
                                        "gpu_offload": "max" if mode == "gpu" else "off",
                                        "quantization": model["quantization"],
                                        "temperature": 0.7, "top_p": 0.8,
                                        "thinking": "off for interactive speed; enable deliberately for reasoning",
                                        "advanced": "Keep engine defaults for Flash Attention and KV precision until supported settings are verified."}})
    candidates.sort(key=lambda m: (m["fits"], m["target_likely"], m["capability_rank"]), reverse=True)
    return {"success": True, "hardware": hardware, "context": context,
            "target": "40–50 generated tokens/sec or faster", "models": candidates,
            "recommended_id": next((m["id"] for m in candidates if m["fits"]), None),
            "target_met": any(m["target_likely"] for m in candidates),
            "assumptions": "Single request; one resident chat model shared by auxiliary roles. FP16 KV bound, runtime buffers, OS/display headroom and 3 GiB CPU embeddings reserve. No automatic multi-GPU pooling."}
