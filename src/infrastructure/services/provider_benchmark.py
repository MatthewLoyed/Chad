"""Opt-in generation measurements with provider-specific metric semantics."""
import math
from time import perf_counter

import httpx

from src.core.tools.failure import failure, BAD_INPUT, CAPABILITY_GAP, UNAVAILABLE
from src.core.tools.limits import get as limit


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def benchmark(options):
    from src.infrastructure.services.ollama_setup import root_url
    provider = options.get("provider")
    base_url = str(options.get("base_url", ""))
    model = str(options.get("model_id", ""))
    if not model or provider not in {"lmstudio", "ollama", "custom"}:
        return failure("Choose a supported model connection before measuring speed.", failure_class=BAD_INPUT)
    # This desktop operation is local only. Cloud benchmarks require separate
    # explicit billing/topology support rather than guessing hardware remotely.
    try:
        root = root_url(base_url)
    except ValueError:
        return failure("Measure this model on its inference computer. This desktop speed test supports local servers only.", failure_class=BAD_INPUT)
    key = str(options.get("api_key", ""))
    if provider == "lmstudio":
        from src.infrastructure.services.lmstudio_assessment import benchmark as lm_benchmark
        result = lm_benchmark(base_url, key, model, confirmed=True)
        if result.get("success"):
            if not _positive(result.get("stats", {}).get("tokens_per_second")):
                return failure("The server reported invalid timing. Speed remains unknown.", failure_class=CAPABILITY_GAP)
            result["metric"] = "provider_decode"
        return result
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        with httpx.Client(timeout=limit("model_assessment_timeout"), trust_env=False) as client:
            started = perf_counter()
            if provider == "ollama":
                response = client.post(root + "/api/generate", headers=headers, json={
                    "model": model, "prompt": "Describe a fictional garden in several short sentences.",
                    "stream": False, "options": {"num_predict": int(limit("model_benchmark_tokens"))}})
            else:
                response = client.post(base_url.rstrip("/") + "/chat/completions", headers=headers, json={
                    "model": model, "messages": [{"role": "user", "content": "Describe a fictional garden in several short sentences."}],
                    "stream": False, "max_tokens": int(limit("model_benchmark_tokens"))})
            elapsed = perf_counter() - started
            response.raise_for_status()
            data = response.json()
        if provider == "ollama":
            count, duration = data.get("eval_count"), data.get("eval_duration")
            if not data.get("done") or not _positive(count) or not _positive(duration):
                return failure("Ollama did not report valid generation timing. Speed remains unknown.", failure_class=CAPABILITY_GAP)
            speed, metric = count / (duration / 1e9), "provider_decode"
        else:
            count = (data.get("usage") or {}).get("completion_tokens")
            if not _positive(count) or not _positive(elapsed):
                return failure("This server did not report token usage. Speed remains unknown.", failure_class=CAPABILITY_GAP)
            speed, metric = count / elapsed, "request_throughput"
        return {"success": True, "kind": "measured", "metric": metric,
                "stats": {"tokens_per_second": speed},
                "note": ("Provider-reported generation speed; server defaults used. Not a tool or settings verification."
                         if metric == "provider_decode" else "Tokens divided by full request time, including loading and prompt processing. This is not decode speed."),
                "settings_verified": False}
    except (ValueError, TypeError, httpx.HTTPError):
        return failure("Speed measurement could not finish. Check model load state, server access and runtime, then retry.", failure_class=UNAVAILABLE)
