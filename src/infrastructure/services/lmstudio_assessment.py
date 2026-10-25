"""Read-only load estimates and explicitly requested local speed measurements."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src.core.tools.failure import failure, BAD_INPUT, CAPABILITY_GAP, UNAVAILABLE
from src.core.tools.limits import get as limit


def local_url(base_url: str) -> str:
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.username or url.password:
        raise ValueError("Hardware validation and benchmarking require a local LM Studio address.")
    if url.query or url.fragment or url.path.rstrip("/") not in {"", "/v1"}:
        raise ValueError("Use LM Studio's local server address, optionally ending in /v1.")
    return f"{url.scheme}://{url.netloc}"


def find_cli() -> str | None:
    cli = shutil.which("lms")
    bundled = Path.home() / ".lmstudio" / "bin" / "lms.exe"
    return cli or (str(bundled) if bundled.is_file() else None)


def _cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=limit("model_assessment_timeout"),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def estimate(model_id: str, context: int, gpu_offload: str) -> dict:
    """No load, unload, daemon start or persistent settings edits are allowed."""
    if not model_id or model_id.startswith("-") or context not in (4096, 8192, 16384, 32768) or gpu_offload not in {"max", "off"}:
        return failure("Invalid model settings for memory estimation.", failure_class=BAD_INPUT)
    cli = find_cli()
    if not cli:
        return failure("The lms CLI was not found; use LM Studio's load dialog to validate memory.", failure_class=CAPABILITY_GAP)
    try:
        help_result = _cli([cli, "load", "--help"])
        if not all(flag in help_result.stdout for flag in ("--estimate-only", "--context-length", "--gpu")):
            return failure("This installed lms version does not advertise the required estimate flags.", failure_class=CAPABILITY_GAP)
        models = _cli([cli, "ls", "--json"])
        installed = json.loads(models.stdout)
        if model_id not in {m.get("modelKey") for m in installed if m.get("type") == "llm"}:
            return failure("Select an exact downloaded model key from LM Studio before estimating.", failure_class=BAD_INPUT)
        result = _cli([cli, "load", model_id, "--estimate-only", "--context-length", str(context), "--gpu", gpu_offload])
        if result.returncode:
            return failure("LM Studio could not estimate these settings. Check the model's runtime in LM Studio.", failure_class=UNAVAILABLE)
        return {"success": True, "kind": "provider_estimate", "model_id": model_id,
                "context": context, "gpu_offload": gpu_offload, "detail": (result.stdout + result.stderr).strip(),
                "note": "Memory estimate only; this did not load the model or change saved defaults."}
    except (OSError, ValueError, subprocess.SubprocessError):
        return failure("LM Studio memory estimation was unavailable or timed out.", failure_class=UNAVAILABLE)


def benchmark(base_url: str, api_key: str, model_id: str, confirmed: bool = False) -> dict:
    """One bounded native API request; caller must explain possible JIT loading."""
    if confirmed is not True:
        return failure("Confirm the benchmark first; it may load a model and change residency.", failure_class=BAD_INPUT)
    try:
        root = local_url(base_url)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with httpx.Client(timeout=limit("model_assessment_timeout"), headers=headers, trust_env=False) as client:
            response = client.get(root + "/api/v1/models")
            response.raise_for_status()
            model = next((m for m in response.json().get("models", [])
                          if m.get("key") == model_id and m.get("type") == "llm"), None)
            if not model:
                return failure("Select a downloaded model exposed by this LM Studio server.", failure_class=BAD_INPUT)
            reasoning = ((model.get("capabilities") or {}).get("reasoning") or {}).get("allowed_options", [])
            payload = {"model": model_id, "input": "Explain step by step how a fictional assistant should choose a calendar tool, check its arguments, read the tool result, and report an appointment. Include several examples. /no_think",
                       "max_output_tokens": int(limit("model_benchmark_tokens")), "temperature": 0,
                       "store": False, "stream": False}
            if "off" in reasoning:
                payload["reasoning"] = "off"
            response = client.post(root + "/api/v1/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        stats = data.get("stats") or {}
        if not isinstance(stats.get("tokens_per_second"), (int, float)):
            return failure("This LM Studio version did not report generation timing; speed remains unknown.", failure_class=CAPABILITY_GAP)
        return {"success": True, "kind": "measured", "model_id": model_id, "stats": stats,
                "reasoning": "off" if "off" in reasoning else "model default",
                "note": "One short tool-workflow text sample, not tool-calling certification or a long-context benchmark. Loaded context and quantization are controlled by LM Studio."}
    except (ValueError, httpx.HTTPError):
        return failure("Local LM Studio benchmark unavailable. Check server address, authentication and runtime; no automatic retry was attempted.", failure_class=UNAVAILABLE)
