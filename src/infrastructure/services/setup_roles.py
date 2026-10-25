"""Validate selected role routes without executing tools, then save them atomically."""
import asyncio
import json
import secrets
import threading
from time import monotonic

from src.core.tools.failure import failure, BAD_INPUT, UNAVAILABLE
from src.core.tools.limits import get as limit
from src.infrastructure.services.setup_validation import test_model

_lock = threading.Lock()
_last_check = {}


async def _test_selector(endpoint, key, model):
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    adapter = LiteLLMAdapter(model_name=model, base_url=endpoint, api_key=key or None)
    response = await asyncio.wait_for(adapter.generate([
        {"role": "system", "content": "Select a tool name from the supplied fictional catalog. Return only a JSON array of names. Never call or execute a tool."},
        {"role": "user", "content": 'Request: count the letters in a fictional word. Catalog: demo_count_letters (counts letters), demo_get_weather (weather).'}],
        tools=None, temperature=0, max_tokens=int(limit("setup_test_tokens")), thinking_enabled=False,
    ), timeout=limit("setup_test_timeout"))
    try:
        return response.finish_reason != "error" and not response.tool_calls and json.loads(response.text or "") == ["demo_count_letters"]
    except (ValueError, TypeError):
        return False


async def validate_roles(base_url, api_key, model_id, roles, confirmed=False):
    if confirmed is not True or not isinstance(roles, dict):
        return failure("Confirm the model-role test first; models may load or be evicted.", failure_class=BAD_INPUT)
    context = roles.get("context")
    budget = roles.get("runtime_budget_gb")
    import math
    if (not isinstance(context, int) or isinstance(context, bool) or not 1024 <= context <= 131072
            or not isinstance(budget, (int, float)) or isinstance(budget, bool)
            or not math.isfinite(budget) or budget <= 0
            or roles.get("residency") not in {"main-and-selector", "sequential"}
            or not isinstance(roles.get("secondary"), str)
            or (roles.get("selector") is not None and not isinstance(roles["selector"], str))):
        return failure("Rescan and choose a compatible model bundle before testing roles.", failure_class=BAD_INPUT)
    from src.infrastructure.services.setup_role_runtime import checked_role
    main = await checked_role(base_url, model_id, roles["context"],
                              lambda: test_model(base_url, api_key, model_id, True))
    if not main.get("success"):
        return main
    errors, checked = [], {"primary": True}
    secondary = roles.get("secondary") or model_id
    if secondary != model_id:
        result = await checked_role(base_url, secondary, roles["context"],
                                    lambda: test_model(base_url, api_key, secondary, True))
        checked["secondary"] = bool(result.get("success"))
        if not checked["secondary"]:
            errors.append(result.get("detail") or "The secondary model could not answer. Check its runtime and load state, or continue with Chat only.")
    else:
        checked["secondary"] = True
    selector = roles.get("selector")
    if selector:
        try:
            async def selector_check():
                return {"success": await _test_selector(base_url, api_key, selector)}
            result = await checked_role(base_url, selector, 8192, selector_check)
            checked["selector"] = result.get("success", False)
        except Exception:
            checked["selector"] = False
        if not checked["selector"]:
            errors.append("Tool-selection check failed. Load Qwen Instruct at 8192 context, parallel predictions 1 and evaluation batch size at most 512, then retry, or continue with Chat only.")
    token = secrets.token_urlsafe()
    with _lock:
        _last_check.clear()
        _last_check.update(token=token, endpoint=base_url, key=api_key, primary=model_id,
                           roles=dict(roles), time=monotonic(), ready=not errors)
    return {"success": not errors, "reply": main["reply"], "checks": checked,
            "detail": " ".join(errors), "validation_token": token,
            "note": "Role routing checked with harmless text. Main-model tool execution and images remain unverified."}


def role_updates(roles, model_id, endpoint, key):
    from src.infrastructure.services.model_bundles import SELECTOR_CONTEXT
    base = {"provider": "lmstudio", "base_url": endpoint, "api_key": key,
            "supports_tools": False, "tools_verified": False}
    secondary = {**base, "name": roles.get("secondary") or model_id, "context_length": roles["context"]}
    selector = roles.get("selector")
    sequential = roles.get("residency") != "main-and-selector"
    estimates = {str(k): float(v) for k, v in roles.get("estimates", {}).items()}
    return {"setup": {"share_main_model": True, "selector_policy": "dedicated" if selector else "main",
                      "secondary_policy": "main" if secondary["name"] == model_id else "dedicated"},
            "tools": {"selector_model": {**base, "name": selector, "context_length": SELECTOR_CONTEXT} if selector else None,
                      "llm_selector_model": selector or model_id,
                      "llm_selector_context_length": SELECTOR_CONTEXT,
                      "llm_selector_unload_after_select": sequential},
            "local_models": {"secondary_model": secondary,
                             "active_model": {"context_length": roles["context"]},
                             "supervisor": {"context_length": roles["context"]},
                             "lifecycle": {"model_size_estimates_gb": estimates, "pinned_models": [],
                                           "vram_budget_gb": roles["runtime_budget_gb"]}},
            "model_roles": {"context_length": roles["context"], "residency": roles["residency"],
                            "chat_ready": True, "selector_checked": bool(selector),
                            "main_tools_verified": False}}


def save_checked_bundle(endpoint, key, primary, token, chat_only=False):
    from src.infrastructure.services.setup_wizard import save_provider_choice
    with _lock:
        checked = dict(_last_check)
    if (not token or checked.get("token") != token or checked.get("endpoint") != endpoint
            or checked.get("key") != key or checked.get("primary") != primary
            or monotonic() - checked.get("time", 0) > limit("setup_validation_ttl")):
        return failure("Model-role verification expired or the connection changed. Test again before saving.", failure_class=BAD_INPUT)
    if not checked["ready"] and not chat_only:
        return failure("Retry the failed role checks, or explicitly choose Chat only.", failure_class=UNAVAILABLE)
    roles = checked["roles"] if not chat_only else {**checked["roles"], "secondary": primary, "selector": None, "estimates": {}}
    return save_provider_choice("lmstudio", endpoint, key, primary, False,
                                extra_updates=role_updates(roles, primary, endpoint, key))
