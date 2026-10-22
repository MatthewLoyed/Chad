"""Smoke test: Brain must run Starlette lifespan startup and serve /health.

Professional pattern for ASGI apps in CI (Starlette/FastAPI docs): use TestClient as a
context manager so lifespan handlers execute — same code path uvicorn hits on boot, but
no port bind, no LM Studio, and no full RemoteFactory bootstrap.

This catches regressions like missing imports inside lifespan (PR #505: threading
NameError crashed prod Brain on every restart).
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

# CI (ubuntu-latest) skips Windows-only packages (see conftest.py). Importing the Brain
# server pulls remote_factory -> audio_handler -> sounddevice/pythoncom/comtypes.
# Inject a minimal headless stub so lifespan smoke tests run on Linux.
if sys.platform != "win32" and "src.core.audio.audio_handler" not in sys.modules:
    _audio_handler = ModuleType("src.core.audio.audio_handler")

    class _MockAudio:
        """Headless audio stub — real MockAudio lives in audio_handler.py on Windows."""

    _audio_handler.MockAudio = _MockAudio
    sys.modules["src.core.audio.audio_handler"] = _audio_handler


@pytest.fixture
def fast_boot(monkeypatch):
    """Keep the smoke test fast and offline — we only care that startup doesn't crash."""

    async def _noop_initialize(self):
        self._initialized = True
        self.initialization_ready.set()

    monkeypatch.setattr(
        "src.remote.server.ChadServer._safe_initialize",
        _noop_initialize,
    )
    monkeypatch.setattr(
        "src.infrastructure.llm.gate_client.ensure_gate_running",
        lambda: None,
    )
    monkeypatch.setattr(
        "src.remote.server.ChadServer.stop",
        AsyncMock(),
    )


def test_server_lifespan_boots_and_health_is_ok(fast_boot):
    """Lifespan must complete startup; /health is the supervisor's liveness probe."""
    from src.remote.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.text == "ok"


def test_server_lifespan_spawns_gate_on_background_thread(fast_boot):
    """Regression guard: gate spawn uses threading — must not NameError at boot."""
    from src.remote.server import app

    with patch("src.remote.server.threading.Thread") as thread_cls:
        thread_cls.return_value.start = lambda: None
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.get("/health")

    assert response.status_code == 200
    thread_cls.assert_called_once()
    assert thread_cls.call_args.kwargs.get("name") == "chad-lmstudio-gate"
