"""Separate explicitly addressed API models from local-engine request options."""


def uses_engine_options(base_url):
    if not base_url:
        return False
    from src.core.intelligence.providers import resolve_provider_name
    return resolve_provider_name(base_url) in {"lmstudio", "ollama"}


def hosted_model_name(model_name, base_url):
    """The provider prefix belongs to LiteLLM; keep the endpoint's raw ID intact."""
    if base_url and not uses_engine_options(base_url):
        return f"openai/{model_name}"
    return None


def normalize_model_name(model_name, base_url=None):
    if not model_name:
        return model_name
    hosted = hosted_model_name(model_name, base_url)
    if hosted:
        return hosted
    if "gemini" in model_name.lower() and "/" not in model_name:
        return f"gemini/{model_name}"
    if base_url:
        known = ("ollama/", "openai/", "lm_studio/", "huggingface/", "vllm/", "gemini/")
        if not model_name.startswith(known):
            prefix = "ollama/" if "ollama" in base_url.lower() else "openai/"
            return prefix + model_name
    return model_name


def deepseek_thinking_options(base_url, enabled):
    """Official DeepSeek API switch; never infer its wire protocol from model names."""
    from urllib.parse import urlsplit
    if base_url and urlsplit(base_url).hostname == "api.deepseek.com" and isinstance(enabled, bool):
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}
    return {}
