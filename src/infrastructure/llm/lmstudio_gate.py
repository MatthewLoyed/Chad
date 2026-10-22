r"""lmstudio_gate.py -- cross-process priority queue in front of the active local backend.

A standalone, OpenAI-compatible reverse proxy that serializes INFERENCE requests
through a single priority-ordered worker. The Brain server and the (separate)
eval-runner process both hit the one local model slot; without a shared queue
they collide and thrash it (issue #478). The in-process model-lifecycle
coordinator (src/core/intelligence/model_lifecycle.py) can't help here because
it lives inside ONE process -- a queue that spans processes has to live outside
both of them, which is what this sidecar is.

The module keeps its "lmstudio_gate" name for continuity (it was built for LM
Studio contention), but the contention problem it solves is identical for any
single-instance local backend -- Ollama, llama.cpp server, vLLM (issue #450). Its
upstream is resolved dynamically from whichever backend is currently active
(see ``_upstream_base_url``), not hardcoded to LM Studio.

What gets queued (the "data plane"):
    POST /v1/chat/completions, /v1/completions, /v1/embeddings

What passes straight through, UNqueued (the "management plane"):
    GET /v1/models, per-backend residency calls (LM Studio's native
    POST /api/v1/models/(load|unload), Ollama's POST /api/generate with
    keep_alive), and anything else. These are cheap liveness / VRAM-control
    calls that model_lifecycle / the provider strategies (#450) poll and that
    must never block behind a heavy generation.

Priority (lower value served first):
    X-Chad-Priority header  ->  interactive(0) | background(10) | autonomous(20) | eval(20)
    Fallback: a `priority` field in the JSON body (stock OpenAI clients can set
    it; stock servers ignore it). A higher-priority request slots ahead of any
    WAITING request but never preempts the one already running -- and FIFO holds
    within a tier.

Run:  python -m src.infrastructure.llm.lmstudio_gate
"""

from __future__ import annotations

import asyncio
import heapq
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("LMStudioGate")

# Priority tiers -- lower is served sooner. Mirrors the model-lifecycle priority
# vocabulary (interactive vs background) plus the eval/autonomous background callers.
_TIERS: Dict[str, int] = {
    "interactive": 0,
    "background": 10,
    "autonomous": 20,
    "eval": 20,
}
_DEFAULT_TIER = _TIERS["interactive"]

# Endpoints whose bodies are real inference work and must be serialized.
_INFERENCE_PATHS = {"/v1/chat/completions", "/v1/completions", "/v1/embeddings"}

# Hop-by-hop headers we must not blindly copy from upstream back to the client.
_STRIP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding", "connection",
    "keep-alive",
}


def _cfg(key: str, default: Any) -> Any:
    return ConfigManager.get(f"local_models.gate.{key}", default)


def _upstream_base_url() -> str:
    """Resolve the local backend this sidecar proxies to.

    Any OpenAI-compatible local endpoint works here, not just LM Studio (#450) --
    this queue exists to solve cross-process contention on a single local model
    slot, a problem every local backend (LM Studio, Ollama, llama.cpp server,
    vLLM) has equally. Resolution order:
      1. An explicit ``local_models.gate.upstream_base_url`` override.
      2. The user's currently ACTIVE local model (``local_models.active_model``
         -- set by the setup wizard's backend chooser, #499, or the Settings
         model picker), so the gate automatically follows whatever backend is
         actually in use.
      3. The legacy LM Studio provider default, for back-compat with configs
         that never set an active_model explicitly.
    """
    explicit = _cfg("upstream_base_url", None)
    if explicit:
        return str(explicit).rstrip("/")
    active = ConfigManager.get_local_model_config()
    if active and active.get("base_url"):
        return str(active["base_url"]).rstrip("/")
    provider = ConfigManager.get("local_models.providers.lmstudio.base_url",
                                 "http://localhost:1234/v1")
    return str(provider).rstrip("/")


def _resolve_tier(header_value: Optional[str], body: Optional[Dict[str, Any]]) -> int:
    p = (header_value or "").strip().lower()
    if p in _TIERS:
        return _TIERS[p]
    if body is not None:
        bp = body.get("priority")
        if isinstance(bp, str) and bp.lower() in _TIERS:
            return _TIERS[bp.lower()]
        if bp is True:      # llm-queue-style boolean "jump the queue"
            return _TIERS["interactive"]
        if bp is False:
            return _TIERS["background"]
    return _DEFAULT_TIER


class PriorityGate:
    """A priority-ordered admission gate with a fixed number of concurrent slots.

    Waiters are ordered by ``(tier, arrival_seq)``: the lowest tier (highest
    priority) that has been waiting longest is admitted the instant a slot frees.
    A running request is never preempted -- we only ever admit the front waiter
    when ``active < max_concurrent``. Cancellation (a client that disconnects
    while still queued) removes the waiter cleanly so it can't wedge the front.
    """

    def __init__(self, max_concurrent: int = 1):
        self._max = max(1, int(max_concurrent))
        self._active = 0
        self._heap: List[Tuple[int, int]] = []
        self._seq = 0
        self._cv = asyncio.Condition()

    @asynccontextmanager
    async def slot(self, tier: int):
        async with self._cv:
            self._seq += 1
            ticket = (tier, self._seq)
            heapq.heappush(self._heap, ticket)
            try:
                while not (self._active < self._max and self._heap and self._heap[0] == ticket):
                    await self._cv.wait()
            except asyncio.CancelledError:
                # Left the queue before being admitted -- drop our ticket and let
                # the next waiter re-evaluate the front.
                try:
                    self._heap.remove(ticket)
                    heapq.heapify(self._heap)
                except ValueError:
                    pass
                self._cv.notify_all()
                raise
            heapq.heappop(self._heap)
            self._active += 1
        try:
            yield
        finally:
            async with self._cv:
                self._active = max(0, self._active - 1)
                self._cv.notify_all()

    @property
    def stats(self) -> Dict[str, int]:
        return {"active": self._active, "waiting": len(self._heap), "max": self._max}


_gate: Optional[PriorityGate] = None
_client: Optional[httpx.AsyncClient] = None


def _get_gate() -> PriorityGate:
    global _gate
    if _gate is None:
        _gate = PriorityGate(max_concurrent=int(_cfg("max_concurrent", 1)))
    return _gate


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # read=None: a local generation can legitimately run for minutes; the caller
        # (litellm / the eval harness) enforces its own wall-clock timeout, and the
        # gate enforces queue_max_age separately. We only bound connect.
        _client = httpx.AsyncClient(
            base_url=_upstream_base_url().rsplit("/v1", 1)[0] or _upstream_base_url(),
            timeout=httpx.Timeout(None, connect=10.0),
        )
    return _client


def _upstream_url(path: str) -> str:
    return _upstream_base_url().rsplit("/v1", 1)[0].rstrip("/") + path


def _forward_headers(request: Request) -> Dict[str, str]:
    skip = {"host", "content-length", "x-chad-priority", "connection"}
    return {k: v for k, v in request.headers.items() if k.lower() not in skip}


def _response_headers(upstream: httpx.Response) -> Dict[str, str]:
    return {k: v for k, v in upstream.headers.items()
            if k.lower() not in _STRIP_RESPONSE_HEADERS}


async def _passthrough(request: Request) -> Response:
    """Proxy a management/status call straight to LM Studio -- never queued."""
    client = _get_client()
    body = await request.body()
    url = _upstream_url(request.url.path)
    try:
        upstream = await client.request(
            request.method, url, params=request.query_params,
            headers=_forward_headers(request), content=body or None,
        )
    except httpx.HTTPError as e:
        return JSONResponse({"error": f"gate upstream error: {e}"}, status_code=502)
    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=_response_headers(upstream),
                    media_type=upstream.headers.get("content-type"))


async def _inference(request: Request) -> Response:
    """Queue an inference request by priority, then proxy it to LM Studio."""
    raw = await request.body()
    parsed: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(raw) if raw else None
    except (json.JSONDecodeError, ValueError):
        parsed = None

    tier = _resolve_tier(request.headers.get("x-chad-priority"),
                         parsed if isinstance(parsed, dict) else None)
    wants_stream = bool(isinstance(parsed, dict) and parsed.get("stream"))
    max_age = float(_cfg("queue_max_age_s", 300) or 0)

    gate = _get_gate()
    url = _upstream_url(request.url.path)
    fwd_headers = _forward_headers(request)

    try:
        slot_cm = gate.slot(tier)
        if max_age > 0:
            await asyncio.wait_for(slot_cm.__aenter__(), timeout=max_age)
        else:
            await slot_cm.__aenter__()
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": f"lmstudio-gate: request waited >{max_age}s in the priority "
                      f"queue and was shed (tier={tier}, {gate.stats})"},
            status_code=503,
        )

    client = _get_client()

    if not wants_stream:
        try:
            upstream = await client.request("POST", url, headers=fwd_headers, content=raw or None)
            return Response(content=upstream.content, status_code=upstream.status_code,
                            headers=_response_headers(upstream),
                            media_type=upstream.headers.get("content-type"))
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"gate upstream error: {e}"}, status_code=502)
        finally:
            await slot_cm.__aexit__(None, None, None)

    # Streaming (SSE): hold the slot until the stream fully drains or the client drops.
    try:
        req = client.build_request("POST", url, headers=fwd_headers, content=raw or None)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await slot_cm.__aexit__(None, None, None)
        return JSONResponse({"error": f"gate upstream error: {e}"}, status_code=502)

    async def _body_stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await slot_cm.__aexit__(None, None, None)

    return StreamingResponse(
        _body_stream(), status_code=upstream.status_code,
        headers=_response_headers(upstream),
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


async def _health(_request: Request) -> Response:
    return JSONResponse({"status": "ok", "upstream": _upstream_base_url(),
                         **_get_gate().stats})


def build_app() -> Starlette:
    routes = [Route("/gate/health", _health, methods=["GET"])]
    routes += [Route(p, _inference, methods=["POST"]) for p in sorted(_INFERENCE_PATHS)]
    routes.append(Route("/{path:path}", _passthrough,
                        methods=["GET", "POST", "PUT", "DELETE", "PATCH"]))
    return Starlette(routes=routes)


app = build_app()


def main() -> None:
    import uvicorn
    port = int(os.getenv("CHAD_LMSTUDIO_GATE_PORT", str(_cfg("port", 1235))))
    logger.info(f"[ GATE ] lmstudio-gate listening on 127.0.0.1:{port} -> {_upstream_base_url()} "
                f"(max_concurrent={_cfg('max_concurrent', 1)})")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    ConfigManager.load_config()
    main()
