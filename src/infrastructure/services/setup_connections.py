"""Local saved model connections; credentials stay in the preferences overlay."""
import hashlib


def identity(model):
    return hashlib.sha256((model.get("provider", "") + "\n" + model.get("base_url", "").rstrip("/")
                           + "\n" + model.get("name", "")).encode()).hexdigest()


def remember(preferences, model):
    local = preferences.setdefault("local_models", {})
    connections = local.setdefault("connections", {})
    previous = local.get("active_model")
    if isinstance(previous, dict) and previous.get("name") and previous.get("base_url"):
        connections[identity(previous)] = dict(previous)
    saved = connections.get(identity(model), {})
    # Retain per-model settings while replacing explicitly supplied credentials.
    local["connections"][identity(model)] = {**saved, **model}
    return {**saved, **model}
