"""Single-flight positive availability cache; lifecycle manager remains authoritative."""
import logging
import threading
import time
from src.core.intelligence.model_lifecycle import ensure_resident_sync, current_priority
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("LLMToolSelector")
_DEFAULT_LOAD_TIMEOUT = 180.0
_lock = threading.RLock()
_entries = {}


def ensure_available(client):
    ttl = max(0, float(ConfigManager.get("tools.selector_availability_ttl_s", 0) or 0))
    if not ttl:
        return refresh(client)
    key = (client._base_url, client._model_id, client._strict_model, client._context_length)
    with _lock:
        if getattr(client, "_availability_seen", False) and not client._resolved_model_id:
            _entries.pop(key, None)
        entry = _entries.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            client._availability_seen = True
            client._resolved_model_id = entry[1]
            client._unavailable_reason = None
            return True
        ok = refresh(client)
        if ok:
            client._availability_seen = True
            _entries[key] = (time.monotonic(), client._resolved_model_id)
        else:
            _entries.pop(key, None)
        return ok


def refresh(client):
    if not client._is_server_reachable():
        client._unavailable_reason = "lm_studio_unreachable"
        return False

    # Read once here (same thread/task as the caller) — an autonomous/heartbeat turn's
    # claim queues behind an active interactive turn instead of evicting it out from
    # under a live conversation. See model_lifecycle.current_priority().
    priority = current_priority()

    loaded_ids = client._list_loaded_models()
    matched = client._match_model_id(client._model_id, loaded_ids)
    if matched:
        ensure_resident_sync(client._base_url, matched, owner=client._OWNER, priority=priority)
        client._resolved_model_id = matched
        client._unavailable_reason = None
        return True

    # Non-strict (default): reuse whatever is already loaded rather than evicting the user's
    # active chat model. This is convenient but NON-DETERMINISTIC — different turns can run on
    # different models, which shows up as tool-selection variance. Strict mode pins the
    # configured selector model: load it first, only reuse a different model if the load fails.
    if loaded_ids and not client._strict_model:
        client._resolved_model_id = loaded_ids[0]
        ensure_resident_sync(client._base_url, client._resolved_model_id, owner=client._OWNER, priority=priority)
        client._unavailable_reason = None
        logger.info(
            f"[ LLM_SELECTOR ] Selector model '{client._model_id}' not loaded; "
            f"reusing active LM Studio model '{client._resolved_model_id}'."
        )
        return True

    logger.info(
        f"[ LLM_SELECTOR ] Model '{client._model_id}' not loaded — "
        f"requesting load from LM Studio..."
    )
    if ensure_resident_sync(
        client._base_url,
        client._model_id,
        owner=client._OWNER,
        priority=priority,
        timeout=_DEFAULT_LOAD_TIMEOUT,
        context_length=client._context_length,
    ):
        client._resolved_model_id = client._model_id
        client._unavailable_reason = None
        logger.info(f"[ LLM_SELECTOR ] Model '{client._model_id}' loaded and ready.")
        return True

    # Last resort even in strict mode: reuse a loaded model rather than disabling the
    # selector entirely (which would force the RAG-only fallback or abort the turn).
    loaded_ids = client._list_loaded_models()
    if loaded_ids:
        client._resolved_model_id = loaded_ids[0]
        ensure_resident_sync(client._base_url, client._resolved_model_id, owner=client._OWNER, priority=priority)
        client._unavailable_reason = None
        logger.warning(
            f"[ LLM_SELECTOR ] Could not load '{client._model_id}'; "
            f"reusing loaded model '{client._resolved_model_id}' as last resort."
        )
        return True

    client._unavailable_reason = f"load_failed:{client._model_id}"
    logger.warning(f"[ LLM_SELECTOR ] Failed to load '{client._model_id}' in LM Studio.")
    return False



def invalidate_failed(client):
    if client.last_select_meta.get("status") != "failed":
        return
    with _lock:
        key = (client._base_url, client._model_id, client._strict_model, client._context_length)
        _entries.pop(key, None)
    client._server_reachable = None
    client._loaded_models_cache = None


def finish_selection(client):
    invalidate_failed(client)
    client._maybe_unload_after_use()
