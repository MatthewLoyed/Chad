"""Explicit, pollable setup jobs. No installer or model server is started here."""
from __future__ import annotations

import copy
import hashlib
import threading
import time
from pathlib import Path

import httpx

from src.core.tools.failure import failure, BAD_INPUT, BLOCKED, UNAVAILABLE
from src.core.tools.limits import get as limit
from src.infrastructure.services.model_recommendations import catalog

_lock = threading.Lock()
_state = {"running": False, "done": False, "bytes": 0, "total": None, "result": None}
_cancel = threading.Event()
EMBEDDING_ID = "nomic-ai/nomic-embed-text-v2-moe"


def _verified(path: Path, model: dict) -> bool:
    if path.stat().st_size != model["size_bytes"]:
        return False
    if model.get("sha256"):
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest() == model["sha256"]
    return True


def progress() -> dict:
    with _lock:
        return copy.deepcopy(_state)


def _update(**values) -> None:
    with _lock:
        _state.update(values)


def cancel() -> dict:
    _cancel.set()
    return {"success": True, "note": "Cancellation requested. An active network read may take until its timeout to settle."}


def start(operation: str, options: dict, confirmed: bool = False) -> dict:
    if confirmed is not True:
        return failure("Explicit confirmation is required before downloads or benchmark generation.", failure_class=BAD_INPUT)
    if operation not in {"download", "embedding", "estimate", "benchmark"} or not isinstance(options, dict):
        return failure("Unknown setup operation.", failure_class=BAD_INPUT)
    with _lock:
        if _state["running"]:
            return failure("A setup operation is already running. Wait or cancel it first.", failure_class=BLOCKED)
        _cancel.clear()
        _state.update(running=True, done=False, bytes=0, total=None, result=None, operation=operation, note="")
    threading.Thread(target=_execute, args=(operation, dict(options)), daemon=True,
                     name="hardware-setup-job").start()
    return {"success": True}


def _execute(operation: str, options: dict) -> None:
    try:
        if operation == "download":
            result = _download(options)
        elif operation == "embedding":
            result = _embedding()
        else:
            from src.infrastructure.services import lmstudio_assessment as lm

            result = (lm.estimate(options.get("model_id", ""), int(options.get("context", 8192)),
                                  options.get("gpu_offload", "max")) if operation == "estimate" else
                      lm.benchmark(options.get("base_url", ""), options.get("api_key", ""),
                                   options.get("model_id", ""), confirmed=True))
    except Exception:
        # Don't expose request URLs, credentials, local paths or HTTP response
        # bodies in generic job failures. Retry is always an explicit action.
        result = failure("Setup operation could not complete. Check network, free space and model app, then retry. Partial chat downloads can resume.", failure_class=UNAVAILABLE)
    _update(running=False, done=True, result=result)


def _download(options: dict) -> dict:
    model = next((m for m in catalog() if m["id"] == options.get("catalog_id")), None)
    if model is None:
        return failure("Choose a catalog model before downloading.", failure_class=BAD_INPUT)
    root = Path(options.get("storage_path") or Path.home() / ".lmstudio" / "models").expanduser()
    target = root / model["repo"] / model["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = model["size_bytes"]
    _update(total=expected)
    if target.exists():
        if _verified(target, model):
            return {"success": True, "note": "This artifact is already downloaded. Refresh the model list in LM Studio."}
        return failure("An existing model file has an unexpected size. Choose another directory or inspect it in LM Studio.", failure_class=BAD_INPUT)
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected:
        return failure("The partial model is larger than its catalog artifact. Inspect it before retrying.", failure_class=BAD_INPUT)
    if offset == expected:
        if not _verified(partial, model):
            return failure("The completed partial file failed its checksum. Inspect it or choose another folder before retrying.", failure_class=BAD_INPUT)
        partial.replace(target)
        return {"success": True, "note": "Completed partial download recovered. Refresh LM Studio's model list."}
    import shutil

    if shutil.disk_usage(target.parent).free < expected - offset:
        return failure("Not enough free space at the selected model directory. Free space or choose another drive.", failure_class=BAD_INPUT)
    url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{model['filename']}"
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    deadline = time.monotonic() + limit("setup_download_timeout")
    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                      timeout=limit("setup_network_timeout")) as response:
        response.raise_for_status()
        if response.status_code == 206:
            if not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
                raise ValueError("Unexpected range response")
        else:
            offset = 0
        _update(bytes=offset)
        with partial.open("ab" if offset else "wb") as output:
            for chunk in response.iter_bytes():
                if _cancel.is_set() or time.monotonic() > deadline:
                    return failure("Download paused. Retry to resume the partial file.", failure_class=BLOCKED)
                if offset + len(chunk) > expected:
                    raise ValueError("Response exceeds catalog artifact size")
                output.write(chunk)
                offset += len(chunk)
                _update(bytes=offset)
    if offset != expected or not _verified(partial, model):
        raise ValueError("Artifact size mismatch")
    partial.replace(target)
    return {"success": True, "note": "Download complete. Refresh LM Studio's model list, load the model, and check Chad's connection. Saved LM Studio settings were not changed."}


def _embedding() -> dict:
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class Progress(tqdm):
        def update(self, n=1):
            if _cancel.is_set():
                raise RuntimeError("Download cancelled")
            result = super().update(n)
            _update(note=f"Embedding assets: {self.n}/{self.total or '?'} files processed. Completed cache files are reused on retry.")
            return result

    snapshot_download(EMBEDDING_ID, allow_patterns=["*.json", "*.py", "*.safetensors", "*.txt", "*.model"],
                      tqdm_class=Progress, max_workers=1, etag_timeout=limit("setup_network_timeout"))
    # Nomic's config references custom model code in a separate repository.
    # Cache that code too, so local_files_only works on a genuinely cold install.
    snapshot_download("nomic-ai/nomic-bert-2048", allow_patterns=["*.py", "config.json"],
                      tqdm_class=Progress, max_workers=1, etag_timeout=limit("setup_network_timeout"))
    return {"success": True, "note": "Embedding assets are cached. Memory and semantic tool search can use them offline; no separate LM Studio embedding model is needed."}
