import json

import httpx

from courier.adapters.matrix import MatrixDestination
from courier.adapters.waha import build_waha
from courier.config import LoadedConfig, load_config
from courier.main import build_route


FORWARDER_YAML = """\
role: forwarder
webhook_url: http://courier-tfl:8000/webhook
source_chat_id: source@g.us
target_chat_id: target@g.us
"""
HEALTH_YAML = """\
role: health-monitor
webhook_url: http://courier-health:8000/webhook
alert_chat_id: alerts@g.us
"""


def _load(tmp_path, text) -> LoadedConfig:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path, environ={"WAHA_API_KEY": "secret"})


def test_route_sends_to_matrix_destination(tmp_path):
    loaded = _load(
        tmp_path,
        FORWARDER_YAML
        + "destination_transport: matrix\nmatrix:\n  homeserver_url: https://matrix.example.org\n  access_token: t\n",
    )

    route = build_route(loaded.config, build_waha(loaded.config.waha, loaded.waha_api_key))

    assert isinstance(route.destination, MatrixDestination)


def test_forwarding_route_without_alerts_subscribes_to_messages_only(tmp_path):
    loaded = _load(tmp_path, FORWARDER_YAML)

    route = build_route(loaded.config, build_waha(loaded.config.waha, loaded.waha_api_key))

    assert route.monitor is None
    assert route.ingress.events == ("message", "message.any")


def test_forwarding_route_with_alerts_also_watches_health(tmp_path):
    loaded = _load(tmp_path, FORWARDER_YAML + "health_alerts: true\nalert_chat_id: alerts@g.us\n")

    route = build_route(loaded.config, build_waha(loaded.config.waha, loaded.waha_api_key))

    assert route.monitor is not None
    assert route.ingress.events == ("message", "message.any", "session.status")


def test_health_only_route_subscribes_to_session_status_only(tmp_path):
    loaded = _load(tmp_path, HEALTH_YAML)

    route = build_route(loaded.config, build_waha(loaded.config.waha, loaded.waha_api_key))

    assert route.monitor is not None
    assert route.ingress.events == ("session.status",)


async def test_health_alerts_go_to_the_alert_chat_through_the_route_destination(tmp_path):
    loaded = _load(tmp_path, HEALTH_YAML)
    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, request=request)

    waha = build_waha(loaded.config.waha, loaded.waha_api_key, transport=httpx.MockTransport(handler))
    route = build_route(loaded.config, waha)

    await route.ingress.handle({"event": "session.status", "session": "default", "payload": {"status": "FAILED"}})
    await waha.aclose()

    assert sent == [
        (
            "/api/sendText",
            {"session": "default", "chatId": "alerts@g.us", "text": "[Courier Bot] WAHA session default is FAILED."},
        ),
    ]
