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
  senders: [alice]
transport: waha
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.source_chat_id == "source"
    assert cfg.filters.keywords == ("urgent",)
    assert cfg.listen_port == 8000
    assert cfg.waha_session == "default"


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
