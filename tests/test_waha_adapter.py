import json
from unittest.mock import AsyncMock

import httpx
import pytest

from courier.adapters.waha import WAHATransport
from courier.domain import Attachment


@pytest.mark.asyncio
async def test_message_payload_mapping():
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        loop=None,
    )

    payload = {
        "event": "message",
        "session": "demo",
        "payload": {
            "id": "msg-1",
            "chatId": "group-1@c.us",
            "fromMe": False,
            "participant": "1234567890@c.us",
            "body": "urgent please",
            "hasMedia": True,
            "media": {
                "mimeType": "image/jpeg",
                "fileName": "photo.jpg",
                "url": "http://example.com/file.jpg",
            },
            "senderName": "Alice",
        },
    }

    message = transport._message_from_payload(payload)
    assert message.id == "msg-1"
    assert message.chat_id == "group-1@c.us"
    assert message.sender_id == "1234567890"
    assert message.sender_name == "Alice"
    assert message.text == "urgent please"
    assert message.attachments[0].mime_type == "image/jpeg"


def test_messages_from_linked_account_are_opt_in():
    payload = {
        "event": "message",
        "payload": {
            "id": "msg-self",
            "chatId": "group-1@g.us",
            "fromMe": True,
            "body": "sent by linked account",
        },
    }
    default_transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
    )
    enabled_transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        include_from_me=True,
    )

    assert default_transport._message_from_payload(payload) is None
    message = enabled_transport._message_from_payload(payload)
    assert message is not None
    assert message.id == "msg-self"
    assert message.chat_id == "group-1@g.us"


@pytest.mark.asyncio
async def test_send_builds_expected_request_body():
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        loop=None,
    )
    transport.session = AsyncMock()

    await transport.send("1234567890@c.us", "hello", attachments=(Attachment("image/png", "x.png", b"123"),))

    transport.session.post.assert_called_once()
    args, kwargs = transport.session.post.call_args
    assert args[0].endswith("/sendImage")
    assert kwargs["json"]["chatId"] == "1234567890@c.us"
    assert kwargs["json"]["caption"] == "hello"


@pytest.mark.asyncio
async def test_start_sets_api_key_header_and_configures_session(monkeypatch):
    captured = {}

    class DummyAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            captured["headers"] = kwargs["headers"]

        async def aclose(self):
            pass

    async def fake_server(*args, **kwargs):
        return AsyncMock()

    configure_session = AsyncMock()
    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(__import__("asyncio"), "start_server", fake_server)
    monkeypatch.setattr(WAHATransport, "_ensure_waha_session", configure_session)

    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        loop=None,
    )

    await transport.start(lambda message: None)

    assert captured["headers"]["X-Api-Key"] == "secret"
    configure_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_waha_session_configures_existing_session():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "demo",
                    "status": "WORKING",
                    "config": {
                        "webhooks": [
                            {"url": "http://courier:8000/webhook", "events": ["session.status"]},
                            {"url": "http://other/webhook", "events": ["message"]},
                        ]
                    },
                },
            )
        if request.method == "PUT":
            assert request.url.path == "/api/sessions/demo"
            config = json.loads(request.content)["config"]
            assert config["webhooks"] == [
                {"url": "http://courier:8000/webhook", "events": ["session.status", "message"]},
                {"url": "http://other/webhook", "events": ["message"]},
            ]
            return httpx.Response(200, json={"name": "demo"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://courier:8000/webhook",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://waha") as client:
        transport.session = client
        await transport._ensure_waha_session()

    assert [request.method for request in requests] == ["GET", "PUT"]


@pytest.mark.asyncio
async def test_ensure_waha_session_creates_and_starts_missing_session():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path == "/api/sessions/":
            body = json.loads(request.content)
            assert body == {
                "name": "demo",
                "config": {"webhooks": [{"url": "http://courier:8000/webhook", "events": ["message"]}]},
            }
            return httpx.Response(201, json={"name": "demo"})
        if request.method == "POST" and request.url.path == "/api/sessions/demo/start":
            return httpx.Response(200, json={"name": "demo", "status": "SCAN_QR"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://courier:8000/webhook",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://waha") as client:
        transport.session = client
        await transport._ensure_waha_session()

    assert [request.url.path for request in requests] == [
        "/api/sessions/demo",
        "/api/sessions/",
        "/api/sessions/demo/start",
    ]


@pytest.mark.asyncio
async def test_dispatches_message_from_webhook_payload():
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        loop=None,
    )
    transport._on_message = AsyncMock()

    payload = {
        "event": "message",
        "session": "demo",
        "payload": {
            "id": "msg-2",
            "chatId": "group-1@c.us",
            "fromMe": False,
            "participant": "1234567890@c.us",
            "body": "hello from webhook",
            "senderName": "Bob",
        },
    }

    message = transport._message_from_payload(payload)
    await transport._dispatch_message(message, payload)

    transport._on_message.assert_awaited_once()
    assert transport._on_message.await_args.args[0].id == "msg-2"
