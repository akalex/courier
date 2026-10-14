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
async def test_start_sets_bearer_auth(monkeypatch):
    captured = {}

    class DummyAsyncClient:
        is_closed = False

        def __init__(self, **kwargs):
            captured["headers"] = kwargs["headers"]

        async def aclose(self):
            pass

    async def fake_server(*args, **kwargs):
        return AsyncMock()

    monkeypatch.setattr(httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(__import__("asyncio"), "start_server", fake_server)

    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        loop=None,
    )

    await transport.start(lambda message: None)

    assert captured["headers"]["Authorization"] == "Bearer secret"


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
