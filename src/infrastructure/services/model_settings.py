"""Explain setting ownership without mutating a model server or user overrides."""
import hashlib
import json

from src.infrastructure.services.model_recommendations import catalog


def profile(model_id, context=8192, configured=None, effective=None, provider="lmstudio"):
    model = next((m for m in catalog() if model_id in {
        m["id"], f"{m['repo']}/{m['filename']}", *m.get("installed_aliases", [])}), None)
    configured = dict(configured or {})
    effective = dict(effective or {})
    suggested = {}
    if model:
        suggested = {**model.get("settings", {}), "context_length": min(context, model["max_context"]),
                     "parallel": 1, "eval_batch_size": 512,
                     "thinking": "off", "mtp": "off", "speculative_decoding": "off"}
    # Fingerprints include effective/configured settings so a changed runtime or
    # user override cannot keep a stale readiness/benchmark claim.
    identity = {"artifact": model_id, "revision": model.get("revision") if model else None,
                "provider": provider, "suggested": suggested,
                "configured": configured, "effective": effective, "profile_version": 1}
    return {**identity, "suggested": suggested, "source": model.get("source") if model else None,
            "status": "suggested" if model else "server-managed",
            "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
            "note": ("Chad-curated suggestions for the exact artifact. Apply load settings in your model app, then test. "
                     "MTP/speculative decoding stays off until a compatible runtime and draft configuration are verified."
                     if model else "Keep your server's settings. Context, sampling, thinking and MTP have not been verified for this model. "
                     "Consult the model publisher and serving runtime; Chad does not overwrite these settings.")}


def suggested_settings(candidate):
    result = profile(candidate["id"], candidate["context"])
    candidate["settings_profile"] = result
    candidate["settings"].update(mtp="off", speculative_decoding="off", parallel=1, eval_batch_size=512)
    return candidate
