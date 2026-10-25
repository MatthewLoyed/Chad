import socket
import asyncio
import httpx

import pytest


@pytest.mark.parametrize("port", [1234, 1235, 11434])
@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_unit_tests_refuse_live_model_connections(port, method):
    with socket.socket() as client:
        with pytest.raises(pytest.fail.Exception, match="live model-server"):
            getattr(client, method)(("127.0.0.1", port))


@pytest.mark.asyncio
async def test_async_http_model_connection_is_blocked():
    async with httpx.AsyncClient() as client:
        with pytest.raises(pytest.fail.Exception, match="live model-server"):
            await client.get("http://127.0.0.1:1234/api/v1/models")


def test_sync_http_model_connection_is_blocked():
    with httpx.Client() as client:
        with pytest.raises(pytest.fail.Exception, match="live model-server"):
            client.get("http://127.0.0.1:1234/api/v1/models")


@pytest.mark.asyncio
async def test_async_socket_model_connection_is_blocked():
    with pytest.raises(pytest.fail.Exception, match="live model-server"):
        await asyncio.get_running_loop().create_connection(lambda: None, "127.0.0.1", 1234)


@pytest.mark.asyncio
async def test_mock_model_transport_still_works():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get("http://127.0.0.1:1234/api/v1/models")).json() == {"models": []}
