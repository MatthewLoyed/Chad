"""Explicit acquisition of curated Ollama manifests; no implicit generation."""
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

from src.core.tools.failure import failure, BAD_INPUT, UNAVAILABLE, BLOCKED
from src.core.tools.limits import get as limit

ROOT = Path(__file__).resolve().parents[3]


def catalog():
    return json.loads((ROOT / "config/ollama_models.json").read_text())["models"]


def root_url(base_url):
    url = urlsplit(base_url)
    if (url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username or url.password or url.query or url.fragment or url.path.rstrip("/") not in {"", "/v1"}):
        raise ValueError("Use a local model server address")
    return f"{url.scheme}://{url.netloc}"


def assess():
    from src.infrastructure.services.hardware_assessment import assess_hardware
    from src.infrastructure.services.model_recommendations import catalog as chat_catalog, recommend
    hardware = assess_hardware()
    # Native Ollama manifests include projector bytes and are distinct from the
    # LM Studio GGUFs. Evaluate the complete native download conservatively.
    models = [{**next(m for m in chat_catalog() if m["id"] == tag["id"]), **tag} for tag in catalog()]
    result = recommend(hardware, 8192, models)
    for model in result["models"]:
        model["settings_note"] = "Suggested context 8192; keep Ollama's sampling defaults. MTP/draft behavior is server-managed and unverified."
    return result


def pull(options, cancelled, update):
    model = next((m for m in catalog() if m["model"] == options.get("model_id")), None)
    if not model:
        return failure("Choose a curated Ollama model before downloading.", failure_class=BAD_INPUT)
    try:
        root = root_url(options.get("base_url", ""))
        headers = {"Authorization": "Bearer " + options["api_key"]} if options.get("api_key") else {}
        with httpx.Client(timeout=limit("setup_network_timeout"), trust_env=False) as client:
            # Check upstream identity before asking the daemon to pull a mutable tag.
            tag = model["model"].split(":", 1)
            manifest = client.get(f"https://registry.ollama.ai/v2/library/{tag[0]}/manifests/{tag[1]}",
                                  headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
            manifest.raise_for_status()
            if "sha256:" + hashlib.sha256(manifest.content).hexdigest() != model["digest"]:
                return failure("The Ollama registry tag changed. Update Chad's curated manifest or download and assess the model manually.", failure_class=BLOCKED)
            deadline = time.monotonic() + limit("setup_download_timeout")
            with client.stream("POST", root + "/api/pull", headers=headers,
                               json={"model": model["model"], "stream": True}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancelled.is_set() or time.monotonic() > deadline:
                        return failure("Pull monitoring paused. Ollama owns the transfer; check it there before retrying.", failure_class=BLOCKED)
                    event = json.loads(line) if line else {}
                    if event.get("error"):
                        return failure("Ollama could not pull this model. Check server storage and network, then retry.", failure_class=UNAVAILABLE)
                    update(note="Ollama is downloading and verifying the selected model.",
                           bytes=event.get("completed", 0), total=event.get("total"))
            tags = client.get(root + "/api/tags", headers=headers)
            tags.raise_for_status()
            installed = next((m for m in tags.json().get("models", []) if m.get("name") == model["model"]), {})
            if installed.get("digest") != model["digest"]:
                return failure("Ollama did not report the expected model manifest. Inspect the model before using it.", failure_class=BLOCKED)
        return {"success": True, "note": "Ollama manifest verified. Find available models and send a short test message before saving."}
    except (ValueError, OSError, httpx.HTTPError):
        return failure("Ollama download could not complete. Check server access, free storage and network, then retry.", failure_class=UNAVAILABLE)
