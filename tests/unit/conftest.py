import sys
import socket
import asyncio
import httpx

import pytest

from src.shared.config_manager import ConfigManager
from unittest.mock import MagicMock


@pytest.fixture(scope="session", autouse=True)
def _block_live_model_servers():
    """Unit tests must not reach installed model servers, even through a worker thread.

    Use a pytest outcome (BaseException), so application fallback handlers cannot
    swallow a blocked request and silently turn a supposed unit test into inference.
    Mock HTTP transports in unit tests; live model acceptance belongs outside unit/.
    """
    patcher = pytest.MonkeyPatch()
    patcher.setenv("LANGCHAIN_TRACING_V2", "false")
    patcher.setenv("LANGSMITH_TRACING", "false")
    for method in ("connect", "connect_ex"):
        original = getattr(socket.socket, method)

        def guarded(sock, address, _original=original):
            if isinstance(address, tuple) and len(address) > 1 and address[1] in {1234, 1235, 11434}:
                pytest.fail("Unit test attempted a live model-server connection; mock the transport.")
            return _original(sock, address)

        patcher.setattr(socket.socket, method, guarded)

    # Windows Proactor networking can use ConnectEx without socket.connect.
    # Block the actual HTTP transports too; MockTransport remains available.
    original_send = httpx.HTTPTransport.handle_request
    original_async_send = httpx.AsyncHTTPTransport.handle_async_request

    def guarded_send(transport, request):
        if request.url.port in {1234, 1235, 11434}:
            pytest.fail("Unit test attempted a live model-server connection; mock the transport.")
        return original_send(transport, request)

    async def guarded_async_send(transport, request):
        if request.url.port in {1234, 1235, 11434}:
            pytest.fail("Unit test attempted a live model-server connection; mock the transport.")
        return await original_async_send(transport, request)

    patcher.setattr(httpx.HTTPTransport, "handle_request", guarded_send)
    patcher.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async_send)
    original_connection = asyncio.BaseEventLoop.create_connection

    async def guarded_connection(loop, protocol_factory, host=None, port=None, **kwargs):
        if port in {1234, 1235, 11434}:
            pytest.fail("Unit test attempted a live model-server connection; mock the transport.")
        return await original_connection(loop, protocol_factory, host, port, **kwargs)

    patcher.setattr(asyncio.BaseEventLoop, "create_connection", guarded_connection)
    yield
    # Keep the guard through process exit: delayed model-warm callbacks can run
    # after pytest's fixture teardown. Live acceptance runs in a separate process.


@pytest.fixture(autouse=True)
def _suppress_background_model_warming(monkeypatch):
    # Turn completion schedules real loads after its test has finished and config
    # mocks have been restored. Unit tests exercise warm_models directly with mocks.
    monkeypatch.setattr("src.core.intelligence.model_lifecycle.schedule_warm", MagicMock())


@pytest.fixture(autouse=True)
def _reset_config_manager_state():
    """Reset the ConfigManager singleton before and after every unit test.

    ConfigManager holds its config as mutable class attrs (_config, _prefs,
    _profiles, _loaded), so any test that loads a custom/tmp-path config (or
    mocks .get()) without tearing down leaks that state into whichever test
    runs next — which test that is depends on collection order, so the same
    leak can pass locally and fail in CI (or vice versa) purely because
    Windows-only files are skipped there, shifting execution order (#216).
    Forcing a fresh real load_config() here for every test removes that
    order-dependency at the root instead of chasing individual leak sources.
    """
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False
    yield
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False


# Chad is a Windows desktop app — several packages are declared Windows-only
# in requirements.txt (`; sys_platform == 'win32'`): pywebview, sounddevice,
# pycaw, comtypes, etc. Two test files transitively import those
# at module load time (test_app_controller.py -> app_controller.py -> webview;
# test_security_hardening.py -> src.remote.server -> remote_factory ->
# audio_handler -> sounddevice), so they can only be collected on Windows.
# Skip them on CI's ubuntu-latest runner instead of failing collection for
# the whole suite (#216).
if sys.platform != "win32":
    collect_ignore_glob = [
        "test_app_controller.py",
        "test_security_hardening.py",
    ]
