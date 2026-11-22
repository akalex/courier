import json

import httpx

from courier.adapters.matrix import MatrixDestination
from courier.adapters.waha import build_waha
from courier.config import load_config
from courier.main import build_route


def _config(tmp_path, extra=""):
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_chat_id: source@g.us\ntarget_chat_id: target@g.us\nalert_chat_id: alerts@g.us\n" + extra,
        encoding="utf-8",
    )
    return load_config(path)


def test_route_sends_to_matrix_destination(tmp_path):
    config = _config(
        tmp_path,
        "destination_transport: matrix\nmatrix:\n  homeserver_url: https://matrix.example.org\n  access_token: token\n",
    )

    route = build_route(config, build_waha(config))

    assert isinstance(route.destination, MatrixDestination)


def test_forwarding_route_without_alerts_subscribes_to_messages_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_ALERTS_ENABLED", "false")
    config = _config(tmp_path)

    route = build_route(config, build_waha(config))

    assert route.monitor is None
    assert route.ingress.events == ("message", "message.any")


def test_health_only_route_subscribes_to_session_status_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_MONITOR_ONLY", "true")
    monkeypatch.setenv("HEALTH_ALERTS_ENABLED", "true")
    config = _config(tmp_path)

    route = build_route(config, build_waha(config))

    assert route.monitor is not None
    assert route.ingress.events == ("session.status",)


async def test_health_alerts_go_to_the_alert_chat_through_the_route_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_ALERTS_ENABLED", "true")
    monkeypatch.delenv("HEALTH_MONITOR_ONLY", raising=False)
    config = _config(tmp_path)
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, request=request)

    waha = build_waha(config, transport=httpx.MockTransport(handler))
    route = build_route(config, waha)

    await route.ingress.handle({"event": "session.status", "session": "default", "payload": {"status": "FAILED"}})
    await waha.aclose()

    assert route.monitor is not None
    assert sent == [
        (
            "/api/sendText",
            {"session": "default", "chatId": "alerts@g.us", "text": "[Courier Bot] WAHA session default is FAILED."},
        ),
    ]
