"""The shape of a Courier config file: one YAML file per container, validated by role."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveInt,
    TypeAdapter,
    model_validator,
)

from ..core.text_transform import MessageTextTransform


def _as_list(value: Any) -> Any:
    """Accept a YAML list or a comma-separated string, dropping blank items."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list | tuple):
        return value
    items = (str(item).strip() if isinstance(item, str | int | float) else item for item in value)
    return tuple(item for item in items if item != "")


def _as_unique_lowercase_list(value: Any) -> Any:
    items = _as_list(value)
    if not isinstance(items, tuple):
        return items
    return tuple(dict.fromkeys(item.lower() if isinstance(item, str) else item for item in items))


def _lowercase(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _uppercase(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


def _known_log_level(value: str) -> str:
    if value not in logging.getLevelNamesMapping():
        raise ValueError(f"unknown log level '{value}'; use DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return value


NonEmptyStr = Annotated[str, Field(min_length=1)]
# json_schema_input_type makes the editor schema describe what a config file may contain (a list or a
# comma-separated string), not the tuple Courier stores after parsing.
StringList = Annotated[tuple[str, ...], BeforeValidator(_as_list, json_schema_input_type=list[str] | str)]
LogLevel = Annotated[str, BeforeValidator(_uppercase), AfterValidator(_known_log_level)]
ForwardContent = Annotated[
    tuple[Literal["text", "media"], ...],
    BeforeValidator(_as_unique_lowercase_list, json_schema_input_type=list[Literal["text", "media"]] | str),
    Field(min_length=1),
]
MediaTypes = Annotated[
    tuple[Literal["image", "video", "audio", "file"], ...],
    BeforeValidator(
        _as_unique_lowercase_list,
        json_schema_input_type=list[Literal["image", "video", "audio", "file"]] | str,
    ),
    Field(min_length=1),
]
DestinationTransport = Annotated[Literal["waha", "matrix"], BeforeValidator(_lowercase)]
SourceTransport = Annotated[Literal["waha"], BeforeValidator(_lowercase)]


class _Section(BaseModel):
    """A strict mapping: unknown keys are errors, and a key left blank in YAML takes its default."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _blank_means_default(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


class WahaSettings(_Section):
    """How Courier reaches WAHA. The API key is not set here: it comes from WAHA_API_KEY in .env."""

    url: NonEmptyStr = Field(
        default="http://localhost:3000",
        description="Base URL Courier uses to call WAHA; under Docker Compose this is http://waha:3000.",
    )
    session: NonEmptyStr = Field(
        default="default",
        description="WAHA session (the linked WhatsApp account) this container uses.",
    )


class MatrixSettings(_Section):
    """The Matrix account that posts messages when destination_transport is matrix."""

    homeserver_url: NonEmptyStr = Field(description="Matrix homeserver URL, for example https://matrix.example.org.")
    access_token: NonEmptyStr = Field(
        description="Access token of the Matrix account that posts messages. Secret: keep this file private.",
    )


class FilterSettings(_Section):
    """Which source-chat messages are forwarded. With no filters, every message is eligible."""

    keywords: StringList = Field(
        default=(),
        description="Forward a message whose text contains any of these words (case-insensitive). "
        "A list or a comma-separated string.",
    )
    exclude_keywords: StringList = Field(
        default=(),
        description="Never forward a message whose text contains any of these words (case-insensitive), "
        "even if it matches keywords or senders.",
    )
    senders: StringList = Field(
        default=(),
        description="Forward a message from any of these sender IDs or names. "
        "Combined with keywords using OR: matching either is enough.",
    )


class MessageTransformSettings(_Section):
    """Extract named values from message text and render a replacement."""

    pattern: NonEmptyStr = Field(
        description="Python regular expression with at least one named capture group, such as (?P<crew>...).",
    )
    template: NonEmptyStr = Field(
        description="Replacement text using $name or ${name} placeholders for named capture groups.",
    )

    @model_validator(mode="after")
    def _valid_transform(self) -> Self:
        MessageTextTransform(self.pattern, self.template)
        return self

    def build(self) -> MessageTextTransform:
        return MessageTextTransform(self.pattern, self.template)


class _CommonConfig(_Section):
    webhook_url: NonEmptyStr = Field(
        description="URL WAHA calls to deliver events to this container, for example "
        "http://courier-tfl:8000/webhook. Must be unique per container.",
    )
    listen_port: PositiveInt = Field(
        default=8000,
        description="Port Courier listens on inside the container; must match the port in webhook_url "
        "unless a proxy sits in between.",
    )
    log_level: LogLevel = Field(
        default="INFO", description="Python log level: DEBUG, INFO, WARNING, ERROR, or CRITICAL."
    )
    waha: WahaSettings = Field(default=WahaSettings(), description="How Courier reaches WAHA.")
    destination_transport: DestinationTransport = Field(
        default="waha",
        description="Where messages and alerts are sent: waha (WhatsApp) or matrix.",
    )
    matrix: MatrixSettings | None = Field(
        default=None,
        description="Matrix account settings; required when destination_transport is matrix, rejected otherwise.",
    )

    @model_validator(mode="after")
    def _matrix_matches_destination(self) -> Self:
        if self.destination_transport == "matrix" and self.matrix is None:
            raise ValueError(
                "matrix.homeserver_url and matrix.access_token are required when destination_transport is matrix",
            )
        if self.destination_transport != "matrix" and self.matrix is not None:
            raise ValueError("matrix applies only when destination_transport is matrix")
        return self


class ForwarderConfig(_CommonConfig):
    """A container that forwards messages from one source chat to one target chat."""

    role: Literal["forwarder"] = Field(description="What this container does: forwarder or health-monitor.")
    source_transport: SourceTransport = Field(
        default="waha", description="Where messages come from; only waha is supported."
    )
    source_chat_id: NonEmptyStr = Field(description="WhatsApp chat ID messages are forwarded from, e.g. 1203…@g.us.")
    target_chat_id: NonEmptyStr = Field(
        description="Chat or room ID messages are forwarded to: a WhatsApp chat ID, or a Matrix room ID such as "
        "!roomid:example.org.",
    )
    include_from_me: bool = Field(
        default=False,
        description="Also forward messages sent by the linked WhatsApp account. For WhatsApp-to-WhatsApp routes, "
        "source and target must then differ.",
    )
    forward_content: ForwardContent = Field(
        default=("text", "media"),
        description="What to forward: text, media, or both. A media-only route drops captions.",
    )
    media_types: MediaTypes = Field(
        default=("image", "video", "audio", "file"),
        description="Attachment kinds to forward when media is forwarded: image, video, audio, file.",
    )
    max_attachment_size: PositiveInt = Field(
        default=20 * 1024 * 1024,
        description="Largest attachment in bytes; larger ones are skipped (the text is still sent).",
    )
    filters: FilterSettings = Field(default=FilterSettings(), description="Which messages are forwarded.")
    message_transform: MessageTransformSettings | None = Field(
        default=None,
        description="Optionally replace source text with a template populated from the first regex match. "
        "A non-matching message and its media are skipped.",
    )
    health_alerts: bool = Field(
        default=False,
        description="Also watch WAHA session health and send alerts to alert_chat_id. "
        "Normally the health-monitor container does this.",
    )
    alert_chat_id: NonEmptyStr | None = Field(
        default=None,
        description="Chat or room that receives health alerts; required when health_alerts is true, "
        "rejected otherwise.",
    )

    @model_validator(mode="after")
    def _alert_chat_matches_health_alerts(self) -> Self:
        if self.health_alerts and self.alert_chat_id is None:
            raise ValueError("alert_chat_id is required when health_alerts is true")
        if not self.health_alerts and self.alert_chat_id is not None:
            raise ValueError("alert_chat_id applies only when health_alerts is true")
        return self

    @model_validator(mode="after")
    def _no_forwarding_loop(self) -> Self:
        if self.include_from_me and self.destination_transport == "waha" and self.source_chat_id == self.target_chat_id:
            raise ValueError("source_chat_id and target_chat_id must differ when include_from_me is true")
        return self


class HealthMonitorConfig(_CommonConfig):
    """A container that only watches WAHA session health and sends alerts; it forwards nothing."""

    role: Literal["health-monitor"] = Field(description="What this container does: forwarder or health-monitor.")
    alert_chat_id: NonEmptyStr = Field(description="Chat or room that receives health and recovery alerts.")


CourierConfig = Annotated[ForwarderConfig | HealthMonitorConfig, Field(discriminator="role")]
COURIER_CONFIG: TypeAdapter[ForwarderConfig | HealthMonitorConfig] = TypeAdapter(CourierConfig)
