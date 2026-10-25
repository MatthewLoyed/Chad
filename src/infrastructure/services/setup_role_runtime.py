"""Check actual LM Studio load settings against the single-request bundle budget."""
from src.core.tools.failure import failure, UNAVAILABLE
from src.core.tools.limits import get as limit


async def checked_role(endpoint, model, context, operation):
    from src.core.intelligence.providers import LMStudioLifecycle
    from src.core.intelligence.model_lifecycle import ensure_resident, release
    provider = LMStudioLifecycle(endpoint)
    entries = await provider._native_models(timeout=limit("hardware_probe_timeout"))
    if entries is None:
        return failure("LM Studio could not report loaded settings. Use manual Chat setup or restore its native API before verifying this bundle.", failure_class=UNAVAILABLE)
    already_loaded = any(e.get("key") == model and e.get("loaded_instances") for e in entries)
    claimed = False
    try:
        if not already_loaded:
            claimed = await ensure_resident(endpoint, model, owner="setup-role-check",
                                           context_length=context, timeout=limit("setup_test_timeout"))
            if not claimed:
                return failure("LM Studio could not load a bundle model. Check the exact artifact and runtime, then retry or use manual Chat setup.", failure_class=UNAVAILABLE)
        entries = await provider._native_models(timeout=limit("hardware_probe_timeout"))
        instances = [i for e in entries or [] if e.get("key") == model for i in e.get("loaded_instances", [])]
        batch = instances[0].get("config", {}).get("eval_batch_size") if len(instances) == 1 else None
        valid = (len(instances) == 1 and instances[0].get("config", {}).get("context_length") == context
                 and instances[0].get("config", {}).get("parallel") == 1
                 and isinstance(batch, (int, float)) and not isinstance(batch, bool) and 0 < batch <= 512)
        if not valid:
            return failure(f"Load this model in LM Studio at {context} context tokens, parallel predictions 1 and evaluation batch size at most 512, then retry. The bundle budget cannot verify different or unknown loaded settings. Saved LM Studio defaults were not changed.", failure_class=UNAVAILABLE)
        return await operation()
    finally:
        if claimed:
            await release(endpoint, model, "setup-role-check")
