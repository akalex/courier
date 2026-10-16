import asyncio
import json

import pytest

from courier.webhook_server import WebhookServer


async def _request(server: WebhookServer, request: bytes) -> bytes:
    await server.start()
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
async def test_webhook_server_dispatches_valid_event():
    received = []

    async def handler(payload):
        received.append(payload)

    server = WebhookServer(host="127.0.0.1", port=0, handler=handler)
    body = json.dumps({"event": "message"}).encode()
    try:
        response = await _request(
            server,
            b"POST /webhook HTTP/1.1\r\nHost: localhost\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode() + body,
        )
    finally:
        await server.stop()

    assert response.startswith(b"HTTP/1.1 200")
    assert received == [{"event": "message"}]


@pytest.mark.asyncio
async def test_webhook_server_returns_500_when_delivery_callback_fails():
    async def handler(payload):
        raise RuntimeError("delivery failed")

    server = WebhookServer(host="127.0.0.1", port=0, handler=handler)
    body = b'{"event":"message"}'
    try:
        response = await _request(
            server,
            b"POST /webhook HTTP/1.1\r\nHost: localhost\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode() + body,
        )
    finally:
        await server.stop()

    assert response.startswith(b"HTTP/1.1 500")


@pytest.mark.asyncio
async def test_webhook_server_rejects_large_body_without_dispatch():
    received = []

    async def handler(payload):
        received.append(payload)

    server = WebhookServer(host="127.0.0.1", port=0, handler=handler, max_body_size=4)
    try:
        response = await _request(
            server,
            b"POST /webhook HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\n12345",
        )
    finally:
        await server.stop()

    assert response.startswith(b"HTTP/1.1 413")
    assert received == []


@pytest.mark.asyncio
async def test_webhook_server_rejects_chunked_transfer_encoding():
    async def handler(payload):
        raise AssertionError("Handler must not receive chunked request")

    server = WebhookServer(host="127.0.0.1", port=0, handler=handler)
    try:
        response = await _request(
            server,
            b"POST /webhook HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        )
    finally:
        await server.stop()

    assert response.startswith(b"HTTP/1.1 400")


@pytest.mark.asyncio
async def test_webhook_server_health_check():
    async def handler(payload):
        raise AssertionError("Health check must not invoke webhook handler")

    server = WebhookServer(host="127.0.0.1", port=0, handler=handler)
    try:
        response = await _request(server, b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    finally:
        await server.stop()

    assert response.startswith(b"HTTP/1.1 200")
