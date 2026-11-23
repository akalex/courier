import pytest
from pydantic import ValidationError

from courier.config.model import COURIER_CONFIG, ForwarderConfig, HealthMonitorConfig


FORWARDER = {
    "role": "forwarder",
    "webhook_url": "http://courier-tfl:8000/webhook",
    "source_chat_id": "src@g.us",
    "target_chat_id": "dst@g.us",
}
HEALTH = {
    "role": "health-monitor",
    "webhook_url": "http://courier-health:8000/webhook",
    "alert_chat_id": "15551234567@c.us",
}
MATRIX = {"homeserver_url": "https://matrix.example.org", "access_token": "token"}


def test_minimal_forwarder_gets_documented_defaults():
    config = COURIER_CONFIG.validate_python(FORWARDER)

    assert isinstance(config, ForwarderConfig)
    assert (config.listen_port, config.log_level, config.waha.url, config.waha.session) == (
        8000,
        "INFO",
        "http://localhost:3000",
        "default",
    )
    assert (config.destination_transport, config.include_from_me, config.health_alerts) == ("waha", False, False)
    assert config.forward_content == ("text", "media")
    assert config.media_types == ("image", "video", "audio", "file")
    assert config.max_attachment_size == 20971520
    assert config.alert_chat_id is None
    assert config.matrix is None


def test_health_monitor_carries_no_forwarding_settings():
    config = COURIER_CONFIG.validate_python(HEALTH)

    assert isinstance(config, HealthMonitorConfig)
    assert not hasattr(config, "source_chat_id")
    assert not hasattr(config, "filters")


@pytest.mark.parametrize(
    "raw",
    [
        {**FORWARDER, "colour": "red"},
        {**FORWARDER, "filters": {"keyword": ["a"]}},
        {**FORWARDER, "waha": {"api_key": "x"}},
        {**HEALTH, "source_chat_id": "src@g.us"},
    ],
)
def test_unknown_or_foreign_keys_are_rejected(raw):
    with pytest.raises(ValidationError) as exc:
        COURIER_CONFIG.validate_python(raw)

    assert {error["type"] for error in exc.value.errors()} == {"extra_forbidden"}


def test_alert_chat_id_follows_health_alerts():
    config = COURIER_CONFIG.validate_python({**FORWARDER, "health_alerts": True, "alert_chat_id": "a@c.us"})
    assert config.alert_chat_id == "a@c.us"

    for raw in (
        {**FORWARDER, "health_alerts": True},
        {**FORWARDER, "alert_chat_id": "a@c.us"},
        {key: value for key, value in HEALTH.items() if key != "alert_chat_id"},
    ):
        with pytest.raises(ValidationError):
            COURIER_CONFIG.validate_python(raw)


def test_matrix_settings_follow_destination_transport():
    config = COURIER_CONFIG.validate_python({**FORWARDER, "destination_transport": "matrix", "matrix": MATRIX})
    assert config.matrix is not None
    assert config.matrix.homeserver_url == "https://matrix.example.org"

    for raw in ({**FORWARDER, "destination_transport": "matrix"}, {**FORWARDER, "matrix": MATRIX}):
        with pytest.raises(ValidationError):
            COURIER_CONFIG.validate_python(raw)


def test_include_from_me_requires_distinct_waha_chats():
    looping = {**FORWARDER, "include_from_me": True, "target_chat_id": "src@g.us"}

    with pytest.raises(ValidationError):
        COURIER_CONFIG.validate_python(looping)
    config = COURIER_CONFIG.validate_python({**looping, "destination_transport": "matrix", "matrix": MATRIX})
    assert config.include_from_me is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("listen_port", 0),
        ("max_attachment_size", -1),
        ("log_level", "LOUD"),
        ("forward_content", []),
        ("media_types", ["gif"]),
        ("destination_transport", "telegram"),
        ("webhook_url", ""),
    ],
)
def test_invalid_values_are_rejected(key, value):
    with pytest.raises(ValidationError):
        COURIER_CONFIG.validate_python({**FORWARDER, key: value})


def test_numeric_scalars_are_read_as_strings():
    config = COURIER_CONFIG.validate_python({**HEALTH, "alert_chat_id": 15551234567, "waha": {"session": 2024}})

    assert (config.alert_chat_id, config.waha.session) == ("15551234567", "2024")


def test_empty_sections_mean_defaults():
    config = COURIER_CONFIG.validate_python({**FORWARDER, "waha": None, "filters": {"keywords": None}})

    assert config.waha.url == "http://localhost:3000"
    assert config.filters.keywords == ()


def test_enum_values_are_case_insensitive():
    config = COURIER_CONFIG.validate_python(
        {**FORWARDER, "media_types": ["Video"], "log_level": "debug", "forward_content": ["MEDIA"]},
    )

    assert (config.media_types, config.log_level, config.forward_content) == (("video",), "DEBUG", ("media",))


def test_list_keys_accept_a_comma_separated_string():
    config = COURIER_CONFIG.validate_python(
        {**FORWARDER, "media_types": "video, video", "filters": {"keywords": "a, b", "senders": "+1555"}},
    )

    assert (config.media_types, config.filters.keywords, config.filters.senders) == (
        ("video",),
        ("a", "b"),
        ("+1555",),
    )
