import base64
import json

import httpx
import pytest

from courier import __version__
from courier.adapters.waha import WAHA, build_waha
from courier.config import load_config
from courier.core.domain import Attachment
from courier.core.health import HealthObservation


def _waha(handler, *, session_name="demo", sleep=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://waha:3000")
    kwargs = {"sleep": sleep} if sleep is not None else {}
    return WAHA(base_url="http://waha:3000", session_name=session_name, http=http, **kwargs)


class RecordingSleep:
    def __init__(self):
        self.delays = []

    async def __call__(self, seconds):
        self.delays.append(seconds)


# --- sending -----------------------------------------------------------------------------------------------------


async def test_send_text_posts_message_to_waha_session():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, request=request)

    waha = _waha(handler)
    await waha.destination.send("target@g.us", "hello")

    assert [(request.url.path, json.loads(request.content)) for request in requests] == [
        ("/api/sendText", {"session": "demo", "chatId": "target@g.us", "text": "hello"}),
    ]


@pytest.mark.parametrize(
    ("mime_type", "expected_endpoint"),
    [("image/jpeg", "/api/sendImage"), ("video/mp4", "/api/sendVideo"), ("application/pdf", "/api/sendFile")],
)
async def test_send_uses_media_specific_waha_endpoint(mime_type, expected_endpoint):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, request=request)

    waha = _waha(handler)
    await waha.destination.send("target@g.us", "caption", (Attachment(mime_type, "media", b"data"),))

    assert len(requests) == 1
    assert requests[0].url.path == expected_endpoint
    assert json.loads(requests[0].content) == {
        "session": "demo",
        "chatId": "target@g.us",
        "caption": "caption",
        "file": {"mimetype": mime_type, "filename": "media", "data": "ZGF0YQ=="},
    }


@pytest.mark.parametrize(
    ("filename", "data"),
    [("clip.mp4", b"video-data"), ("attachment", b"\x00\x00\x00\x18ftypisom")],
)
async def test_send_infers_mp4_mime_type_for_generic_binary_video(filename, data):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, request=request)

    waha = _waha(handler)
    await waha.destination.send("target@g.us", "", (Attachment("application/octet-stream", filename, data),))

    assert requests[0].url.path == "/api/sendVideo"
    assert json.loads(requests[0].content)["file"]["mimetype"] == "video/mp4"


async def test_send_video_falls_back_to_file_when_waha_lacks_chrome(caplog):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/sendVideo":
            return httpx.Response(
                422,
                json={"message": 'Use "devlikeapro/waha:chrome" docker image to send video in WEBJS'},
                request=request,
            )
        return httpx.Response(201, request=request)

    waha = _waha(handler)
    await waha.destination.send("target@g.us", "caption", (Attachment("video/mp4", "clip.mp4", b"video"),))

    assert requests == ["/api/sendVideo", "/api/sendFile"]
    assert "forwarding as a file" in caplog.text


async def test_send_raises_and_logs_waha_error_response(caplog):
    def handler(request):
        return httpx.Response(422, json={"message": ["file.data must be base64 encoded"]}, request=request)

    waha = _waha(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await waha.destination.send("target@g.us", "hello")

    assert "file.data must be base64 encoded" in caplog.text


# --- session health probe ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(200, json={"name": "demo", "status": "WORKING"}),
            HealthObservation(None, "Recovery update: WAHA session demo and worker are back online."),
        ),
        (
            httpx.Response(200, json={"name": "demo", "status": "SCAN_QR"}),
            HealthObservation("session:unhealthy", "WAHA session demo is SCAN_QR."),
        ),
        (httpx.Response(200, json={"name": "demo", "status": "STARTING"}), None),
        (
            httpx.Response(404),
            HealthObservation("session:missing", "WAHA session demo no longer exists (HTTP 404)."),
        ),
        (
            httpx.Response(503),
            HealthObservation(
                "worker:unavailable",
                "WAHA worker returned HTTP 503 while checking session demo.",
            ),
        ),
        (
            httpx.Response(401),
            HealthObservation("api:http-401", "WAHA health check for session demo returned HTTP 401."),
        ),
        (
            httpx.Response(200, json=["not", "a", "session"]),
            HealthObservation(
                "api:invalid-response",
                "WAHA returned an invalid health response for session demo.",
            ),
        ),
    ],
)
async def test_probe_maps_waha_session_responses_to_health_observations(response, expected):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return response

    waha = _waha(handler)

    assert await waha.probe.check() == expected
    assert paths == ["/api/sessions/demo"]


async def test_probe_reports_unreachable_worker():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    waha = _waha(handler)

    assert await waha.probe.check() == HealthObservation(
        "worker:unavailable",
        "WAHA worker is unavailable while checking session demo.",
    )


# --- media -------------------------------------------------------------------------------------------------------


async def test_media_download_rewrites_localhost_url_to_waha_origin():
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, content=b"video-data")

    waha = _waha(handler)
    media = waha.media.media_ref(
        {"url": "http://localhost:3000/api/files/demo/m1.mp4", "mimetype": "video/mp4", "filename": "m1.mp4"},
        "m1",
    )

    assert media is not None
    assert (media.mime_type, media.filename) == ("video/mp4", "m1.mp4")
    assert requested == []
    assert await media.fetch(max_bytes=1024) == b"video-data"
    assert requested == ["http://waha:3000/api/files/demo/m1.mp4"]


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/media.mp4",
        "http://attacker.example:3000/api/files/demo/m1.mp4",
        "http://waha:8080/api/files/demo/m1.mp4",
        "http://user:pass@waha:3000/api/files/demo/m1.mp4",
    ],
)
async def test_media_from_outside_waha_origin_is_never_requested(url):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, content=b"video-data")

    waha = _waha(handler)
    media = waha.media.media_ref({"url": url}, "m1")

    assert media is not None
    assert await media.fetch(max_bytes=1024) is None
    assert requested == []


@pytest.mark.parametrize("response", [httpx.Response(404), httpx.Response(200, content=b"")])
async def test_failed_or_empty_media_download_is_unavailable(response):
    waha = _waha(lambda request: response)
    media = waha.media.media_ref({"url": "/api/files/demo/m1.jpg"}, "m1")

    assert await media.fetch(max_bytes=1024) is None


async def test_media_larger_than_limit_is_unavailable():
    waha = _waha(lambda request: httpx.Response(200, content=b"12345"))
    media = waha.media.media_ref({"url": "/api/files/demo/m1.jpg"}, "m1")

    assert await media.fetch(max_bytes=4) is None
    assert await media.fetch(max_bytes=5) == b"12345"


async def test_inline_base64_media_is_available_without_request():
    requested = []

    def handler(request):
        requested.append(request)
        return httpx.Response(500)

    waha = _waha(handler)
    media = waha.media.media_ref(
        {"data": base64.b64encode(b"image-bytes").decode(), "mimeType": "image/png", "fileName": "x.png"},
        "m1",
    )

    assert media is not None
    assert (media.mime_type, media.filename) == ("image/png", "x.png")
    assert await media.fetch(max_bytes=1024) == b"image-bytes"
    assert requested == []


@pytest.mark.parametrize("media", [None, {}, {"mimetype": "image/jpeg"}, {"data": "%%% not base64 %%%"}])
async def test_media_without_retrievable_content_has_no_reference(media):
    waha = _waha(lambda request: httpx.Response(500))

    assert waha.media.media_ref(media, "m1") is None


# --- session provisioning ----------------------------------------------------------------------------------------


async def test_provision_sets_exactly_the_requested_events_on_its_own_webhook():
    puts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "demo",
                    "status": "WORKING",
                    "config": {
                        "webhooks": [
                            {"url": "http://courier:8000/webhook", "events": ["session.status"]},
                            {
                                "url": "http://courier-health:8000/webhook",
                                "events": ["message", "message.any", "session.status"],
                                "retries": {"policy": "linear", "delaySeconds": 5, "attempts": 3},
                            },
                            {"url": "http://other/webhook", "events": ["message"]},
                        ],
                    },
                },
            )
        puts.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "demo"})

    waha = _waha(handler)
    await waha.provision("http://courier-health:8000/webhook", ("session.status",))

    assert puts == [
        {
            "name": "demo",
            "config": {
                "webhooks": [
                    {
                        "url": "http://courier-health:8000/webhook",
                        "events": ["session.status"],
                        "retries": {"policy": "linear", "delaySeconds": 5, "attempts": 3},
                    },
                    {"url": "http://other/webhook", "events": ["message"]},
                ],
            },
        },
    ]


async def test_provision_adds_its_webhook_with_default_retries_and_starts_stopped_session():
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"name": "demo", "status": "STOPPED", "config": {}})
        if request.method == "PUT":
            assert json.loads(request.content)["config"]["webhooks"] == [
                {
                    "url": "http://courier-tfl:8000/webhook",
                    "events": ["message", "message.any"],
                    "retries": {"policy": "exponential", "delaySeconds": 2, "attempts": 10},
                },
            ]
        return httpx.Response(200, json={"name": "demo"})

    waha = _waha(handler)
    await waha.provision("http://courier-tfl:8000/webhook", ("message", "message.any"))

    assert requests == [
        ("GET", "/api/sessions/demo"),
        ("PUT", "/api/sessions/demo"),
        ("POST", "/api/sessions/demo/start"),
    ]


async def test_provision_creates_and_starts_missing_session():
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path == "/api/sessions/":
            assert json.loads(request.content) == {
                "name": "demo",
                "config": {
                    "webhooks": [
                        {
                            "url": "http://courier:8000/webhook",
                            "events": ["message", "message.any", "session.status"],
                            "retries": {"policy": "exponential", "delaySeconds": 2, "attempts": 10},
                        },
                    ],
                },
            }
            return httpx.Response(201, json={"name": "demo"})
        return httpx.Response(200, json={"name": "demo", "status": "SCAN_QR"})

    waha = _waha(handler)
    await waha.provision("http://courier:8000/webhook", ("message", "message.any", "session.status"))

    assert requests == [
        ("GET", "/api/sessions/demo"),
        ("POST", "/api/sessions/"),
        ("POST", "/api/sessions/demo/start"),
    ]


async def test_provision_retries_request_timeout_after_a_delay():
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"name": "demo", "status": "WORKING", "config": {"webhooks": []}})
        if requests.count("PUT") == 1:
            return httpx.Response(408)
        return httpx.Response(200, json={"name": "demo"})

    sleep = RecordingSleep()
    waha = _waha(handler, sleep=sleep)
    await waha.provision("http://courier:8000/webhook", ("message", "message.any"))

    assert requests == ["GET", "PUT", "GET", "PUT"]
    assert sleep.delays == [5]


async def test_provision_does_not_retry_client_errors():
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(401)

    sleep = RecordingSleep()
    waha = _waha(handler, sleep=sleep)
    with pytest.raises(httpx.HTTPStatusError):
        await waha.provision("http://courier:8000/webhook", ("message",))

    assert requests == ["GET"]
    assert sleep.delays == []


# --- construction from config ------------------------------------------------------------------------------------


async def test_build_waha_identifies_courier_and_authenticates_with_api_key(tmp_path, monkeypatch):
    for name in ("WAHA_URL", "WAHA_API_KEY", "WAHA_SESSION"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_chat_id: source@g.us
target_chat_id: target@g.us
waha:
  url: http://waha:3000
  api_key: secret
  session: demo
""".strip(),
        encoding="utf-8",
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, request=request)

    waha = build_waha(load_config(path), transport=httpx.MockTransport(handler))
    await waha.destination.send("target@g.us", "hello")
    await waha.aclose()

    assert str(requests[0].url) == "http://waha:3000/api/sendText"
    assert requests[0].headers["X-Api-Key"] == "secret"
    assert requests[0].headers["User-Agent"] == f"courier/{__version__}"
