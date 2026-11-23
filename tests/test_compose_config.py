from pathlib import Path

import yaml


COURIER_SERVICES = {
    "courier-aircraft-losses": "./config.yaml",
    "courier-tfl": "./config-tfl.yaml",
    "courier-health": "./config-health.yaml",
}


def load_compose():
    return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))


def test_courier_services_receive_only_the_waha_api_key():
    compose = load_compose()
    shared_environment = compose["x-courier-common"]["environment"]

    assert shared_environment == {"WAHA_API_KEY": "${WAHA_API_KEY:?Set WAHA_API_KEY in .env}"}
    for name in COURIER_SERVICES:
        assert compose["services"][name].get("environment", shared_environment) == shared_environment


def test_each_courier_service_mounts_its_own_config():
    services = load_compose()["services"]

    for name, host_file in COURIER_SERVICES.items():
        assert services[name]["volumes"] == [f"{host_file}:/app/config.yaml:ro"]


def test_env_example_holds_only_waha_settings_and_courier_tag():
    lines = Path(".env.example").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if line.strip() and not line.startswith("#")}

    assert keys == {
        "WAHA_API_KEY",
        "WAHA_DASHBOARD_USERNAME",
        "WAHA_DASHBOARD_PASSWORD",
        "WHATSAPP_SWAGGER_USERNAME",
        "WHATSAPP_SWAGGER_PASSWORD",
        "WAHA_IMAGE",
        "WHATSAPP_DEFAULT_ENGINE",
        "COURIER_TAG",
    }


def test_waha_does_not_print_qr_codes_to_logs():
    compose = load_compose()

    assert compose["services"]["waha"]["environment"]["WAHA_PRINT_QR"] == "False"


def test_waha_defaults_to_noweb_arm():
    compose = load_compose()
    waha = compose["services"]["waha"]

    assert waha["image"] == "${WAHA_IMAGE:-devlikeapro/waha:noweb-arm}"
    assert waha["environment"]["WHATSAPP_DEFAULT_ENGINE"] == "${WHATSAPP_DEFAULT_ENGINE:-NOWEB}"
