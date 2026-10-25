"""Pure role-bundle planning; capacity, installed assets and readiness stay distinct."""
from src.infrastructure.services.model_recommendations import (
    recommend, catalog, _ram_budget, _gpu_budget, GIB, STORAGE_RESERVE_GB,
)

FAST = "qwen3.5-9b"
BONSAI = "bonsai-27b-q1"
SELECTOR = "qwen2.5-3b-instruct"
SMALL = "qwen3.5-4b"
SELECTOR_CONTEXT = 8192


def artifact_key(model):
    return f"{model['repo']}/{model['filename']}"


def _plan(primary, candidates, hardware, installed, selector):
    main = candidates[primary]
    secondary = FAST if primary == BONSAI and candidates[FAST]["fits"] else primary
    ids = list(dict.fromkeys([primary, secondary] + ([SELECTOR] if selector else [])))
    models = [candidates[key] for key in ids]
    new_bytes = sum(m["size_bytes"] for m in models if m["id"] not in installed)
    reasons = [reason for m in models for reason in m["reasons"] if "storage" not in reason.lower()]
    storage = hardware.get("storage_free_gb")
    if storage is None or storage < new_bytes / GIB + STORAGE_RESERVE_GB:
        reasons.append("Not enough known free space for all new model files and download headroom.")
    # Secondary swaps sequentially. Main+selector can remain resident only when
    # the same single GPU has room for both contexts and the host staging budget.
    main_set = [main] + ([candidates[SELECTOR]] if selector else [])
    ram = _ram_budget(hardware)
    peak_host = max(m["required_gb"] for m in models) + 3.0
    cohost = sum(m["weights_gb"] + 0.75 for m in main_set) + 3.0
    gpu = max(hardware.get("gpus") or [], key=_gpu_budget, default=None)
    gpu_budget = _gpu_budget(gpu) if gpu else 0
    cop = (all(m["mode"] == "gpu" for m in main_set)
           and sum(m["required_gb"] for m in main_set) <= gpu_budget
           and ram is not None and cohost <= ram)
    if ram is None or peak_host > ram:
        reasons.append("Insufficient host memory for the sequential model plan and reserved headroom.")
    return {"id": primary, "primary": primary, "secondary": secondary,
            "selector": SELECTOR if selector else None, "selector_context": SELECTOR_CONTEXT,
            "context": main["context"], "fits": not reasons, "reasons": sorted(set(reasons)),
            "new_bytes": new_bytes, "residency": "main-and-selector" if cop else "sequential",
            "peak_host_gb": round(max(peak_host, cohost) if cop else peak_host, 2),
            "runtime_budget_gb": round(max(sum(m["required_gb"] for m in main_set) if cop else 0,
                                           max(m["required_gb"] for m in models)), 2),
            "assets": [{"catalog_id": m["id"], "name": m["name"], "model_key": installed.get(m["id"], artifact_key(m)),
                        "installed": m["id"] in installed, "size_bytes": m["size_bytes"],
                        "license": m.get("license"), "source": m["source"],
                        "required_gb": m["required_gb"]} for m in models],
            "runtime_note": main.get("runtime_note", "Load and test each role before marking it ready."),
            "tools_ready": False, "runtime_verified": False}


def bundles(hardware, context=8192, installed=None):
    installed = installed or {}
    result = recommend(hardware, context)
    selector = recommend(hardware, SELECTOR_CONTEXT, [m for m in catalog() if m["id"] == SELECTOR])["models"][0]
    candidates = {m["id"]: m for m in result["models"]}
    candidates[SELECTOR] = selector
    # Artifact reuse removes download-space requirements, not runtime memory.
    for key, model in candidates.items():
        model["reasons"] = [r for r in model["reasons"] if "storage" not in r.lower()]
        model["fits"] = not model["reasons"]
    gpu = max(hardware.get("gpus") or [], key=_gpu_budget, default=None)
    eligible = bool(gpu and not gpu.get("shared_memory") and (gpu.get("vram_gb") or 0) >= 15.8)
    # 15.8 GiB tolerates Windows-reported capacity of nominal 16 GB cards.
    order = ([BONSAI] if eligible else []) + [FAST]
    plans = []
    for key in order:
        if not candidates[key]["fits"]:
            continue
        plan = _plan(key, candidates, hardware, installed, True)
        if not plan["fits"]:
            plan = _plan(key, candidates, hardware, installed, False)
        if plan["fits"]:
            plans.append(plan)
    if not plans and not candidates[SMALL]["fits"] and context > 4096:
        smaller = recommend(hardware, 4096, [m for m in catalog() if m["id"] == SMALL])["models"][0]
        smaller["reasons"] = [r for r in smaller["reasons"] if "storage" not in r.lower()]
        smaller["fits"] = not smaller["reasons"]
        candidates[SMALL] = smaller
    if not plans and candidates[SMALL]["fits"]:
        plan = _plan(SMALL, candidates, hardware, installed, True)
        if not plan["fits"]:
            plan = _plan(SMALL, candidates, hardware, installed, False)
        if plan["fits"]:
            plans.append(plan)
    result.update(bundles=plans, recommended_bundle_id=plans[0]["id"] if plans else None,
                  recommended_id=plans[0]["primary"] if plans else None,
                  models=[candidates[p["primary"]] for p in plans],
                  no_fit_note="Free memory or model-drive space, correct detected hardware, or use an existing/cloud server.")
    result["assumptions"] = "One inference host; main and selector together only when their combined budget fits, otherwise sequential switching. Secondary loads sequentially. OS/display headroom and optional CPU-embedding reserve included. Load contexts must match this plan; runtime estimates remain unverified until tested."
    return result
