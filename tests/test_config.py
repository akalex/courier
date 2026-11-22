import pytest

from courier.config import load_config


def test_valid_config_loads(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_chat_id: source
target_chat_id: target
filters:
  keywords: [urgent]
  exclude_keywords: [city, "New York"]
  senders: [alice]
transport: waha
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.source_chat_id == "source"
    assert cfg.alert_chat_id == "target"
    assert cfg.filters.keywords == ("urgent",)
    assert cfg.filters.exclude_keywords == ("city", "New York")
    assert cfg.listen_port == 8000
    assert cfg.waha_session == "default"
    assert cfg.source_transport == "waha"
    assert cfg.destination_transport == "waha"
    assert cfg.forward_content == ("text", "media")
    assert cfg.media_types == ("image", "video", "audio", "file")


def test_forward_content_and_media_types_can_be_selected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_chat_id: source\ntarget_chat_id: target\nforward_content: [media]\nmedia_types: [video]\n",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.forward_content == ("media",)
    assert cfg.media_types == ("video",)


@pytest.mark.parametrize(
    ("setting", "value", "error"),
    [
        ("forward_content", "[audio]", "forward_content"),
        ("media_types", "[document]", "media_types"),
        ("forward_content", "[]", "at least one value"),
    ],
)
def test_invalid_forward_content_settings_fail_validation(tmp_path, setting, value, error):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"source_chat_id: source\ntarget_chat_id: target\n{setting}: {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error):
        load_config(path)


def test_matrix_destination_config_loads_and_requires_credentials(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_transport: waha
destination_transport: matrix
source_chat_id: source
target_chat_id: "!room:example.org"
matrix:
  homeserver_url: https://matrix.example.org
  access_token: secret-token
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.source_transport == "waha"
    assert cfg.destination_transport == "matrix"
    assert cfg.target_chat_id == "!room:example.org"
    assert cfg.matrix_homeserver_url == "https://matrix.example.org"
    assert cfg.matrix_access_token == "secret-token"

    path.write_text(
        "source_transport: waha\ndestination_transport: matrix\nsource_chat_id: source\n"
        "target_chat_id: '!room:example.org'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MATRIX_HOMESERVER_URL", raising=False)
    monkeypatch.delenv("MATRIX_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="matrix.homeserver_url"):
        load_config(path)


def test_matrix_credentials_can_be_configured_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_transport: waha\ndestination_transport: matrix\n"
        "source_chat_id: source\ntarget_chat_id: '!room:example.org'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATRIX_HOMESERVER_URL", "https://matrix.example.org/")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "environment-token")

    cfg = load_config(path)

    assert cfg.matrix_homeserver_url == "https://matrix.example.org"
    assert cfg.matrix_access_token == "environment-token"


def test_alert_chat_id_can_be_configured_separately(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_chat_id: source\ntarget_chat_id: target\nalert_chat_id: '15551234567@c.us'\n",
        encoding="utf-8",
    )

    assert load_config(path).alert_chat_id == "15551234567@c.us"

    monkeypatch.setenv("ALERT_CHAT_ID", "15557654321@c.us")
    assert load_config(path).alert_chat_id == "15557654321@c.us"

    monkeypatch.setenv("ALERT_CHAT_ID", "")
    assert load_config(path).alert_chat_id == "15551234567@c.us"


def test_health_alerts_can_be_disabled(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\ntarget_chat_id: target\n", encoding="utf-8")

    assert load_config(path).health_alerts_enabled is True

    monkeypatch.setenv("HEALTH_ALERTS_ENABLED", "false")
    assert load_config(path).health_alerts_enabled is False


def test_health_monitor_only_can_be_enabled(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\ntarget_chat_id: target\n", encoding="utf-8")

    assert load_config(path).health_monitor_only is False

    monkeypatch.setenv("HEALTH_MONITOR_ONLY", "true")
    assert load_config(path).health_monitor_only is True


def test_transport_setting_remains_a_legacy_default_for_source_and_destination(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "transport: waha\nsource_chat_id: source\ntarget_chat_id: target\n",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.source_transport == "waha"
    assert cfg.destination_transport == "waha"


def test_unsupported_destination_transport_fails_validation(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_transport: waha\ndestination_transport: signal\nsource_chat_id: source\ntarget_chat_id: target\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported destination_transport"):
        load_config(path)


def test_invalid_config_fails_fast(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def test_webhook_url_can_be_configured_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\ntarget_chat_id: target\n", encoding="utf-8")
    monkeypatch.setenv("WHATSAPP_HOOK_URL", "http://courier:8000/webhook")

    cfg = load_config(path)

    assert cfg.webhook_url == "http://courier:8000/webhook"


def test_include_from_me_defaults_to_false_and_can_be_enabled(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\ntarget_chat_id: target\n", encoding="utf-8")

    assert load_config(path).include_from_me is False

    path.write_text(
        "source_chat_id: source\ntarget_chat_id: target\ninclude_from_me: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INCLUDE_FROM_ME", "")
    assert load_config(path).include_from_me is True

    monkeypatch.setenv("INCLUDE_FROM_ME", "true")
    assert load_config(path).include_from_me is True


def test_include_from_me_requires_distinct_source_and_target(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: same\ntarget_chat_id: same\n", encoding="utf-8")
    monkeypatch.setenv("INCLUDE_FROM_ME", "true")

    with pytest.raises(ValueError, match="must differ"):
        load_config(path)


def test_include_from_me_rejects_invalid_boolean(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\ntarget_chat_id: target\n", encoding="utf-8")
    monkeypatch.setenv("INCLUDE_FROM_ME", "sometimes")

    with pytest.raises(ValueError, match="include_from_me"):
        load_config(path)


@pytest.mark.parametrize(
    ("key", "value", "expected_error"),
    [
        ("listen_port", 0, "greater than zero"),
        ("max_attachment_size", -1, "greater than zero"),
        ("log_level", "NOPE", "unsupported value"),
    ],
)
def test_invalid_typed_settings_fail_validation(tmp_path, key, value, expected_error):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"source_chat_id: source\ntarget_chat_id: target\n{key}: {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=expected_error):
        load_config(path)
