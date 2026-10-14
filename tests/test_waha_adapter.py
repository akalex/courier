from unittest.mock import AsyncMock

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
