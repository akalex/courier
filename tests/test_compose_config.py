from pathlib import Path

import yaml


def test_compose_routes_share_configured_alert_chat_id():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    route_names = ("courier-aircraft-losses", "courier-tfl")
    alert_chat_ids = [compose["services"][name]["environment"]["ALERT_CHAT_ID"] for name in route_names]

    assert alert_chat_ids == ["${ALERT_CHAT_ID:-}", "${ALERT_CHAT_ID:-}"]


def test_only_dedicated_health_container_sends_health_alerts():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["courier-aircraft-losses"]["environment"]["HEALTH_ALERTS_ENABLED"] is False
    assert compose["services"]["courier-tfl"]["environment"]["HEALTH_ALERTS_ENABLED"] is False
    assert compose["services"]["courier-health"]["environment"]["HEALTH_ALERTS_ENABLED"] is True
    assert compose["services"]["courier-health"]["environment"]["HEALTH_MONITOR_ONLY"] is True


def test_dedicated_health_container_uses_its_own_config():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["courier-health"]["volumes"] == [
        "./config-health.yaml:/app/config.yaml:ro",
    ]


def test_waha_does_not_print_qr_codes_to_logs():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["waha"]["environment"]["WAHA_PRINT_QR"] == "False"


def test_waha_defaults_to_noweb_arm():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    waha = compose["services"]["waha"]

    assert waha["image"] == "${WAHA_IMAGE:-devlikeapro/waha:noweb-arm}"
    assert waha["environment"]["WHATSAPP_DEFAULT_ENGINE"] == "${WHATSAPP_DEFAULT_ENGINE:-NOWEB}"
