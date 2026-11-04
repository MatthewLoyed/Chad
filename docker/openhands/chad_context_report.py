"""Report coding context usage without logging prompts or responses."""
import json
from urllib.request import urlopen


def context_reporter(base_url, model, metrics):
    limit = None
    try:
        endpoint = base_url.rstrip("/").removesuffix("/v1") + "/api/v0/models"
        with urlopen(endpoint, timeout=3) as response:
            models = json.load(response)["data"]
        limit = next((entry.get("loaded_context_length") for entry in models
                      if entry.get("id") == model.removeprefix("openai/")
                      and entry.get("state") == "loaded"), None)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    print(f"CHAD_CONTEXT_LIMIT loaded_tokens={limit}", flush=True)
    seen = set()

    def report(event=None):
        if event is not None and "Condens" in type(event).__name__:
            print(f"CHAD_CONTEXT_EVENT kind={type(event).__name__}", flush=True)
        for lane, llm in metrics.items():
            for usage in llm.metrics.token_usages:
                key = (lane, usage.response_id)
                if key in seen:
                    continue
                seen.add(key)
                print(f"CHAD_CONTEXT_REQUEST lane={lane} input={usage.prompt_tokens} "
                      f"output={usage.completion_tokens} cached={usage.cache_read_tokens} "
                      f"loaded_limit={limit}", flush=True)

    return report
