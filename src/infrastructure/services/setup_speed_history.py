"""Local timing history keyed by current server/model/settings identity."""
import hashlib
import json
import threading

import httpx

from src.core.tools.limits import get as limit
from src.shared.paths import get_project_root

_lock = threading.Lock()


def signature(options):
    from src.infrastructure.services.ollama_setup import root_url
    try:
        root = root_url(options.get("base_url", ""))
        model = options.get("model_id")
        provider = options.get("provider")
        key = options.get("api_key")
        headers = {"Authorization": "Bearer " + key} if key else {}
        with httpx.Client(timeout=limit("hardware_probe_timeout"), trust_env=False) as client:
            if provider == "ollama":
                response = client.post(root + "/api/show", headers=headers, json={"model": model})
                response.raise_for_status()
                version = client.get(root + "/api/version", headers=headers)
                version.raise_for_status()
                loaded = client.get(root + "/api/ps", headers=headers)
                loaded.raise_for_status()
                instance = next(m for m in loaded.json().get("models", []) if m.get("name") == model)
                state = [response.json(), version.json(), {k: v for k, v in instance.items() if k != "expires_at"}]
            elif provider == "lmstudio":
                response = client.get(root + "/api/v1/models", headers=headers)
                response.raise_for_status()
                state = next(m for m in response.json().get("models", []) if m.get("key") == model)
                # Effective loaded config is required, not just a catalog entry.
                if not state.get("loaded_instances"):
                    return None
            else:
                return None  # compatible APIs do not standardize effective settings
        identity = [provider, root, model, state]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    except (ValueError, TypeError, StopIteration, httpx.HTTPError):
        return None


def _path():
    return get_project_root() / "data/setup_speed_history.json"


def _read():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(options, result):
    fingerprint = signature(options)
    if not fingerprint:
        result["history_note"] = "Effective settings unavailable; this measurement is not reusable verification."
        return result
    safe = {key: result[key] for key in ("success", "kind", "metric", "stats", "note") if key in result}
    safe["settings_fingerprint"] = fingerprint
    try:
        with _lock:
            data = _read()
            data[fingerprint] = safe
            target = _path()
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".tmp")
            partial.write_text(json.dumps(data), encoding="utf-8")
            partial.replace(target)
    except OSError:
        result["history_note"] = "Measurement finished; local history could not be saved."
    return result


def previous(options):
    fingerprint = signature(options)
    with _lock:
        return _read().get(fingerprint) if fingerprint else None
