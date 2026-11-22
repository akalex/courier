import asyncio
import logging

import httpx
import pytest

from courier.adapters.waha import WAHA
from courier.adapters.waha_ingress import WAHAWebhookIngress
from courier.core.domain import Attachment, TransientMediaError
from courier.core.filters import KeywordFilter
from courier.core.forwarder import Forwarder
from courier.core.health import HealthObservation


class Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, item):
        self.calls.append(item)


class RecordingDestination:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, attachments=()):
        self.sent.append((chat_id, text, attachments))

    async def stop(self):
        pass


def _waha(handler=None, *, session_name="demo"):
    def refuse(request):
        raise AssertionError(f"Unexpected WAHA request: {request.method} {request.url}")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler or refuse), base_url="http://waha:3000")
    return WAHA(base_url="http://waha:3000", session_name=session_name, http=http)


def _ingress(waha=None, *, include_from_me=False, on_message=None, on_status=None):
    return WAHAWebhookIngress(
        waha or _waha(),
        include_from_me=include_from_me,
        on_message=on_message,
        on_status=on_status,
    )


def _message_event(message_id, *, chat_id="group@g.us", body="", media_url=None, event="message", **extra):
    payload = {"id": message_id, "chatId": chat_id, "fromMe": False, "body": body, **extra}
    if media_url is not None:
        payload["media"] = {"url": media_url, "mimetype": "image/jpeg", "filename": f"{message_id}.jpg"}
    return {"event": event, "session": "demo", "id": f"evt-{message_id}-{event}", "payload": payload}


def _status_event(status, *, session="demo"):
    return {"event": "session.status", "session": session, "payload": {"status": status}}


# --- message parsing ---------------------------------------------------------------------------------------------


async def test_message_event_is_normalized_for_the_forwarder():
    received = Recorder()
    ingress = _ingress(on_message=received)

    await ingress.handle(
        {
            "event": "message",
            "session": "demo",
            "payload": {
                "id": "msg-1",
                "chatId": "group-1@g.us",
                "fromMe": False,
                "participant": "+1234567890@c.us",
                "senderName": "Alice",
                "body": "urgent please",
            },
        },
    )

    [message] = received.calls
    assert (message.id, message.chat_id, message.sender_id, message.sender_name, message.text) == (
        "msg-1",
        "group-1@g.us",
        "1234567890",
        "Alice",
        "urgent please",
    )
    assert message.attachments == ()


async def test_messages_from_linked_account_are_opt_in():
    payload = _message_event("msg-self", fromMe=True)
    default_received, enabled_received = Recorder(), Recorder()

    await _ingress(on_message=default_received).handle(payload)
    await _ingress(include_from_me=True, on_message=enabled_received).handle(payload)

    assert default_received.calls == []
    assert [message.id for message in enabled_received.calls] == ["msg-self"]


async def test_message_any_from_linked_account_uses_recipient_chat_id():
    received = Recorder()
    ingress = _ingress(include_from_me=True, on_message=received)

    await ingress.handle(
        {
            "event": "message.any",
            "payload": {"id": "msg-self", "from": "linked@c.us", "to": "group-1@g.us", "fromMe": True, "body": "hi"},
        },
    )

    assert [(message.chat_id, message.text) for message in received.calls] == [("group-1@g.us", "hi")]


async def test_message_is_logged_with_webhook_correlation_ids(caplog):
    caplog.set_level(logging.INFO, logger="courier.adapters.waha_ingress")
    ingress = _ingress(on_message=Recorder())
    payload = _message_event("msg-2", chat_id="group-1@g.us")
    payload["id"] = "evt-2"
    payload["_courier_webhook"] = {"request_id": "wh_123"}

    await ingress.handle(payload)

    assert "event=message event_id=evt-2 request_id=wh_123 message_id=msg-2 chat_id=group-1@g.us" in caplog.text


# --- session status ----------------------------------------------------------------------------------------------


async def test_session_status_events_for_this_session_become_health_observations():
    observed = Recorder()
    ingress = _ingress(on_status=observed)

    await ingress.handle(_status_event("STOPPED"))
    await ingress.handle(_status_event("STARTING"))
    await ingress.handle(_status_event("FAILED", session="other"))
    await ingress.handle(_status_event("WORKING"))

    assert observed.calls == [
        HealthObservation("session:unhealthy", "WAHA session demo is STOPPED."),
        HealthObservation(None, "Recovery update: WAHA session demo and worker are back online."),
    ]


# --- wiring ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("forwarding", "alerting", "expected_events"),
    [
        (True, True, ("message", "message.any", "session.status")),
        (True, False, ("message", "message.any")),
        (False, True, ("session.status",)),
    ],
)
def test_subscribed_events_follow_the_wired_handlers(forwarding, alerting, expected_events):
    ingress = _ingress(on_message=Recorder() if forwarding else None, on_status=Recorder() if alerting else None)

    assert ingress.events == expected_events


async def test_health_only_ingress_ignores_message_events():
    observed = Recorder()
    ingress = _ingress(on_status=observed)

    await ingress.handle(_message_event("m1", media_url="/api/files/demo/m1.jpg"))

    assert observed.calls == []


async def test_only_new_source_chat_messages_that_pass_filters_download_media():
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, content=b"jpeg")

    destination = RecordingDestination()
    forwarder = Forwarder(
        source_chat_id="source@g.us",
        target_chat_id="target@g.us",
        destination=destination,
        filter_policy=KeywordFilter(["urgent"]),
    )
    ingress = _ingress(_waha(handler), on_message=forwarder.handle_message)
    local_url = "http://localhost:3000/api/files/demo/{}.jpg"

    await ingress.handle(_message_event("other", chat_id="other@g.us", body="urgent", media_url=local_url.format("o")))
    await ingress.handle(
        _message_event("boring", chat_id="source@g.us", body="boring", media_url=local_url.format("b"))
    )
    hit = _message_event("hit", chat_id="source@g.us", body="urgent", media_url=local_url.format("hit"))
    await asyncio.gather(ingress.handle(hit), ingress.handle({**hit, "event": "message.any"}))

    assert fetched == ["http://waha:3000/api/files/demo/hit.jpg"]
    assert destination.sent == [("target@g.us", "urgent", (Attachment("image/jpeg", "hit.jpg", b"jpeg"),))]


async def test_media_only_route_asks_waha_to_redeliver_after_a_temporary_download_failure():
    responses = [httpx.Response(503), httpx.Response(200, content=b"video")]
    fetched = []

    def handler(request):
        fetched.append(request.url.path)
        return responses.pop(0)

    destination = RecordingDestination()
    forwarder = Forwarder(
        source_chat_id="source@g.us",
        target_chat_id="target@g.us",
        destination=destination,
        forward_content=("media",),
        media_types=("video",),
    )
    ingress = _ingress(_waha(handler), on_message=forwarder.handle_message)
    event = {
        "event": "message",
        "id": "evt-clip",
        "payload": {
            "id": "clip",
            "chatId": "source@g.us",
            "fromMe": False,
            "media": {"url": "/api/files/demo/clip.mp4", "mimetype": "video/mp4", "filename": "clip.mp4"},
        },
    }

    with pytest.raises(TransientMediaError):
        await ingress.handle(event)
    await ingress.handle(event)

    assert fetched == ["/api/files/demo/clip.mp4", "/api/files/demo/clip.mp4"]
    assert destination.sent == [("target@g.us", "", (Attachment("video/mp4", "clip.mp4", b"video"),))]
