r"""gate_client.py -- helpers for routing local inference through lmstudio-gate.

Two jobs:
  1. ``inference_base_url(real)`` -- rewrite a local base_url to the gate's URL
     when the gate is enabled, reachable, AND actually proxying to that same
     backend (see ``_is_gateable_url``), so completion traffic funnels through
     the single cross-process priority queue. Falls back to the real URL
     (direct to the backend) whenever the gate is off, down, or pointed at a
     different backend -- nothing hard breaks, you just lose queuing.
  2. ``ensure_gate_running()`` -- idempotently start the sidecar. Called from
     Brain boot AND eval-runner startup so whichever process needs it first
     brings it up; the other just reuses it.

The gate is vendor-agnostic (issue #450) -- its upstream is whichever local
backend is currently active (LM Studio, Ollama, llama.cpp server, vLLM), not
hardcoded to LM Studio; see ``lmstudio_gate._upstream_base_url``. Only the
INFERENCE plane is routed here. Model management (load/unload/list) keeps
talking to the backend directly via the provider strategies in
``src/core/intelligence/providers/`` -- see lmstudio_gate.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from typing import Optional

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("GateClient")


def gate_enabled() -> bool:
    env = os.getenv("CHAD_LMSTUDIO_GATE_ENABLED", "").strip().lower()
    if env != "":
        return env in ("1", "true", "yes")
    return bool(ConfigManager.get("local_models.gate.enabled", True))


def gate_port() -> int:
    return int(os.getenv("CHAD_LMSTUDIO_GATE_PORT",
                         str(ConfigManager.get("local_models.gate.port", 1235))))


def gate_base_url() -> str:
    """The OpenAI-compatible base_url callers should point inference at."""
    return f"http://127.0.0.1:{gate_port()}/v1"


def _active_gate_upstream() -> str:
    """The base_url the gate sidecar is actually proxying to right now -- reuses
    lmstudio_gate's own resolution (explicit override, else the active local
    model, else the LM Studio default) so this process's routing decision always
    agrees with what the gate itself would do, whatever backend that is."""
    from src.infrastructure.llm.lmstudio_gate import _upstream_base_url
    return _upstream_base_url()


def _is_gateable_url(base_url: str) -> bool:
    """Only traffic bound for the gate's own upstream is routed through it -- any
    local backend (LM Studio, Ollama, llama.cpp/vLLM) is eligible as long as it's
    the one the gate is currently proxying (issue #450/#478). A strict match
    rather than "any local url", since redirecting a DIFFERENT backend's traffic
    into a gate pointed elsewhere would be wrong."""
    b = (base_url or "").rstrip("/")
    return bool(b) and b == _active_gate_upstream()


def gate_alive(timeout: float = 1.0) -> bool:
    try:
        url = f"http://127.0.0.1:{gate_port()}/gate/health"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def inference_base_url(real_base_url: Optional[str]) -> Optional[str]:
    """Return the gate URL when it's proxying this exact backend, else the original URL.

    Cloud adapters (base_url is None) and any base_url that doesn't match the
    gate's current upstream are returned unchanged -- this works for whichever
    local backend is active (LM Studio, Ollama, llama.cpp/vLLM), not just LM
    Studio. If the gate is enabled but not yet up, we still return the gate URL
    only when it's actually reachable -- otherwise we fall back to the real URL
    so a missing sidecar never takes inference down.
    """
    if not real_base_url:
        return real_base_url
    if not gate_enabled() or not _is_gateable_url(real_base_url):
        return real_base_url
    if not gate_alive():
        logger.debug("[ GATE ] enabled but not reachable -- inference falls back to direct.")
        return real_base_url
    return gate_base_url()


def ensure_gate_running(wait_s: float = 6.0) -> bool:
    """Start the gate sidecar if it isn't already up. Idempotent and best-effort.

    Returns True if the gate is reachable by the time we give up waiting.
    """
    if not gate_enabled():
        return False
    if gate_alive():
        return True

    try:
        creationflags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP -- outlive the launching
            # process and don't inherit its console/Ctrl-C.
            creationflags = 0x00000008 | 0x00000200
        env = dict(os.environ)
        env.setdefault("PYTHONPATH", os.getcwd())
        subprocess.Popen(
            [sys.executable, "-m", "src.infrastructure.llm.lmstudio_gate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags, env=env,
            close_fds=True,
        )
        logger.info(f"[ GATE ] Spawned gate sidecar on port {gate_port()} (upstream: {_active_gate_upstream()}).")
    except Exception as e:
        logger.warning(f"[ GATE ] Could not spawn sidecar ({e}); inference stays direct.")
        return False

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if gate_alive():
            return True
        time.sleep(0.25)
    logger.warning("[ GATE ] Sidecar did not become healthy in time; inference stays direct.")
    return False
