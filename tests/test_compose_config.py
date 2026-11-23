from pathlib import Path

import yaml


def test_alert_chat_id_is_not_in_shared_compose_environment():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert "ALERT_CHAT_ID" not in compose["x-courier-common"]["environment"]


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


def test_route_chat_ids_come_from_each_config_file():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    aircraft = compose["services"]["courier-aircraft-losses"]
    tfl = compose["services"]["courier-tfl"]

    assert aircraft["volumes"] == ["./config.yaml:/app/config.yaml:ro"]
    assert tfl["volumes"] == ["./config-tfl.yaml:/app/config.yaml:ro"]
    assert "SOURCE_CHAT_ID" not in aircraft["environment"]
    assert "TARGET_CHAT_ID" not in aircraft["environment"]
    assert "SOURCE_CHAT_ID" not in tfl["environment"]
    assert "TARGET_CHAT_ID" not in tfl["environment"]


def test_waha_does_not_print_qr_codes_to_logs():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["waha"]["environment"]["WAHA_PRINT_QR"] == "False"


def test_waha_defaults_to_noweb_arm():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    waha = compose["services"]["waha"]

    assert waha["image"] == "${WAHA_IMAGE:-devlikeapro/waha:noweb-arm}"
    assert waha["environment"]["WHATSAPP_DEFAULT_ENGINE"] == "${WHATSAPP_DEFAULT_ENGINE:-NOWEB}"
