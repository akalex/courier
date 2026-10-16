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


def test_message_any_from_linked_account_uses_recipient_chat_id():
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
        include_from_me=True,
    )

    message = transport._message_from_payload(
        {
            "event": "message.any",
            "payload": {
                "id": "msg-self",
                "from": "linked-account@c.us",
                "to": "group-1@g.us",
                "fromMe": True,
                "body": "sent by linked account",
            },
        }
    )

    assert message is not None
    assert message.chat_id == "group-1@g.us"
    assert message.text == "sent by linked account"


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
    transport.session.post.return_value = httpx.Response(
        200,
        request=httpx.Request("POST", "http://waha/api/sendImage"),
    )

    await transport.send("1234567890@c.us", "hello", attachments=(Attachment("image/png", "x.png", b"123"),))

    transport.session.post.assert_called_once()
    args, kwargs = transport.session.post.call_args
    assert args[0].endswith("/sendImage")
    assert kwargs["json"]["chatId"] == "1234567890@c.us"
    assert kwargs["json"]["caption"] == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "expected_endpoint"),
    [
        ("image/jpeg", "/api/sendImage"),
        ("video/mp4", "/api/sendVideo"),
        ("application/pdf", "/api/sendFile"),
    ],
)
async def test_send_uses_media_specific_waha_endpoint(mime_type, expected_endpoint):
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
    )
    transport.session = AsyncMock()
    transport.session.post.return_value = httpx.Response(
        200,
        request=httpx.Request("POST", f"http://waha{expected_endpoint}"),
    )

    await transport.send("target@g.us", "caption", attachments=(Attachment(mime_type, "media", b"data"),))

    assert transport.session.post.call_args.args[0] == expected_endpoint


@pytest.mark.asyncio
async def test_send_video_falls_back_to_file_when_waha_lacks_chrome(caplog):
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
    )
    video_response = httpx.Response(
        422,
        json={
            "message": 'Use "devlikeapro/waha:chrome" docker image to send video in WEBJS',
            "error": "Unprocessable Entity",
            "statusCode": 422,
        },
        request=httpx.Request("POST", "http://waha/api/sendVideo"),
    )
    file_response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://waha/api/sendFile"),
    )
    transport.session = AsyncMock()
    transport.session.post.side_effect = [video_response, file_response]

    await transport.send("target@g.us", "caption", attachments=(Attachment("video/mp4", "clip.mp4", b"video"),))

    assert [call.args[0] for call in transport.session.post.call_args_list] == [
        "/api/sendVideo",
        "/api/sendFile",
    ]
    assert "forwarding as a file" in caplog.text


@pytest.mark.asyncio
async def test_send_logs_waha_error_response(caplog):
    transport = WAHATransport(
        session_name="demo",
        base_url="http://waha",
        api_key="secret",
        webhook_url="http://bot/webhook",
    )
    transport.session = AsyncMock()
    transport.session.post.return_value = httpx.Response(
        422,
        json={"message": ["file.data must be base64 encoded"]},
        request=httpx.Request("POST", "http://waha/api/sendText"),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await transport.send("1234567890@c.us", "hello")
    assert "file.data must be base64 encoded" in caplog.text


@pytest.mark.asyncio
async def test_hydrate_media_rewrites_localhost_url_to_waha_service():
    requested_urls = []

    async def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"video-data")

    transport = WAHATransport(
        session_name="default",
        base_url="http://waha:3000",
        api_key="secret",
        webhook_url="http://courier:8000/webhook",
    )
    message = transport._message_from_payload(
        {
            "event": "message",
            "payload": {
                "id": "message-1",
                "chatId": "group@g.us",
                "fromMe": False,
                "body": "video caption",
                "media": {"url": "http://localhost:3000/api/files/default/message-1.mp4"},
            },
        }
    )
    assert message is not None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport.session = client
        hydrated = await transport._hydrate_media(
            message,
            {"media": {"url": "http://localhost:3000/api/files/default/message-1.mp4"}},
        )

    assert requested_urls == ["http://waha:3000/api/files/default/message-1.mp4"]
    assert hydrated.text == "video caption"
    assert hydrated.attachments[0].data == b"video-data"


@pytest.mark.asyncio
async def test_hydrate_media_failure_keeps_text_without_empty_attachment():
    async def handler(request):
        return httpx.Response(404, request=request)

    transport = WAHATransport(
        session_name="default",
        base_url="http://waha:3000",
        api_key="secret",
        webhook_url="http://courier:8000/webhook",
    )
    message = transport._message_from_payload(
        {
            "event": "message",
            "payload": {
                "id": "message-2",
                "chatId": "group@g.us",
                "fromMe": False,
                "body": "plain text",
                "media": {"url": "http://localhost:3000/api/files/default/message-2.mp4"},
            },
        }
    )
    assert message is not None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport.session = client
        hydrated = await transport._hydrate_media(
            message,
            {"media": {"url": "http://localhost:3000/api/files/default/message-2.mp4"}},
        )

    assert hydrated.text == "plain text"
    assert hydrated.attachments == ()


@pytest.mark.asyncio
async def test_hydrate_media_absent_keeps_text_without_attachment():
    transport = WAHATransport(
        session_name="default",
        base_url="http://waha:3000",
        api_key="secret",
        webhook_url="http://courier:8000/webhook",
    )
    message = transport._message_from_payload(
        {
            "event": "message",
            "payload": {
                "id": "message-3",
                "chatId": "group@g.us",
                "fromMe": False,
                "body": "plain text",
                "media": {},
            },
        }
    )
    assert message is not None

    hydrated = await transport._hydrate_media(message, {"media": {}})

    assert hydrated.text == "plain text"
    assert hydrated.attachments == ()


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
                {"url": "http://courier:8000/webhook", "events": ["session.status", "message", "message.any"]},
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
                "config": {
                    "webhooks": [{"url": "http://courier:8000/webhook", "events": ["message", "message.any"]}]
                },
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
