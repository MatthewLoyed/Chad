"""Hands-off, local-only acquisition acceptance. No inference or model loads.

PYTHONPATH=. python scripts/setup/accept_ollama.py --download --report <local-file>
Without --download, only an already installed exact manifest is accepted.
"""
import argparse
import json
from pathlib import Path
import threading
from time import monotonic

import httpx

from src.infrastructure.services.ollama_setup import catalog, pull, root_url
from src.core.tools.limits import get as limit


def accept(base_url, model_id, download=False):
    root = root_url(base_url)
    model = next(m for m in catalog() if m["model"] == model_id)
    started = monotonic()
    report = {"schema_version": 1, "status": "failed", "generation_requests": 0,
              "model": model_id, "expected_digest": model["digest"]}
    try:
        with httpx.Client(timeout=limit("hardware_probe_timeout"), trust_env=False) as client:
            response = client.get(root + "/api/tags")
            response.raise_for_status()
            installed = any(m.get("name") == model_id and m.get("digest") == model["digest"]
                            for m in response.json().get("models", []))
        if not installed and not download:
            return {**report, "status": "not_run", "reason": "Exact model absent; rerun with --download to authorize acquisition."}
        events = []
        def progress(**event):
            events.append({k: event.get(k) for k in ("bytes", "total")})
        options = {"base_url": base_url, "model_id": model_id}
        first = pull(options, threading.Event(), progress)
        report["acquisition"] = "reused" if installed else "downloaded"
        report["manifest_verified"] = bool(first.get("success"))
        report["progress_events"] = len(events)
        if not first.get("success"):
            return {**report, "reason": "Acquisition failed; check server, storage, network or curated tag drift."}
        reused_events = []
        second = pull(options, threading.Event(), lambda **event: reused_events.append(event))
        report["reuse_without_transfer"] = bool(second.get("success")) and not reused_events
        report["status"] = "passed" if report["reuse_without_transfer"] else "failed"
        report["elapsed_seconds"] = round(monotonic() - started, 2)
        return report
    except (httpx.HTTPError, OSError, ValueError):
        return {**report, "status": "not_run", "reason": "Local Ollama unavailable. Start its server and rerun; no inference was attempted."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", choices=[m["model"] for m in catalog()], default="qwen3.5:4b")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = accept(args.base_url, args.model, args.download)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
