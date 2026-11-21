from pathlib import Path

import yaml


def test_compose_routes_share_configured_alert_chat_id():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    route_names = ("courier-aircraft-losses", "courier-lupynis")
    alert_chat_ids = [compose["services"][name]["environment"]["ALERT_CHAT_ID"] for name in route_names]

    assert alert_chat_ids == ["${ALERT_CHAT_ID:-}", "${ALERT_CHAT_ID:-}"]


def test_only_dedicated_health_container_sends_health_alerts():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["courier-aircraft-losses"]["environment"]["HEALTH_ALERTS_ENABLED"] is False
    assert compose["services"]["courier-lupynis"]["environment"]["HEALTH_ALERTS_ENABLED"] is False
    assert compose["services"]["courier-health"]["environment"]["HEALTH_ALERTS_ENABLED"] is True
    assert compose["services"]["courier-health"]["environment"]["HEALTH_MONITOR_ONLY"] is True
