from __future__ import annotations

from typing import Any

from ..core.domain import Attachment, Message


def first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def coalesce(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def normalize_sender(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("+"):
        normalized = normalized[1:]
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


class WAHAWebhookParser:
    def __init__(self, *, include_from_me: bool) -> None:
        self.include_from_me = include_from_me

    def message_from_payload(self, payload: Any) -> Message | None:
        if not isinstance(payload, dict):
            return None

        event = payload.get("event")
        if event and event not in {"message", "message.any"}:
            return None

        event_data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if not isinstance(event_data, dict):
            return None

        from_me = event_data.get("fromMe")
        if from_me is True and not self.include_from_me:
            return None

        chat_id = coalesce(
            event_data.get("chatId"),
            event_data.get("chat_id"),
            (event_data.get("chat") or {}).get("id"),
            event_data.get("to") if from_me is True else event_data.get("from"),
        )
        if not chat_id:
            return None

        participant = coalesce(
            event_data.get("participant"),
            event_data.get("from"),
            event_data.get("sender"),
            (event_data.get("sender") or {}).get("id"),
            (event_data.get("author") or {}).get("id"),
        )
        sender_id = normalize_sender(participant)
        sender_name = coalesce(
            event_data.get("senderName"),
            event_data.get("pushName"),
            event_data.get("sender_name"),
            (event_data.get("sender") or {}).get("name"),
            (event_data.get("author") or {}).get("name"),
            sender_id,
        )

        text = coalesce(
            event_data.get("body"),
            event_data.get("text"),
            event_data.get("caption"),
            event_data.get("content"),
            event_data.get("conversation"),
        )

        attachments: list[Attachment] = []
        media = event_data.get("media")
        if isinstance(media, dict):
            mime_type = media.get("mimeType") or media.get("mimetype") or "application/octet-stream"
            file_name = media.get("fileName") or media.get("filename") or "attachment"
            attachments.append(Attachment(mime_type=mime_type, filename=file_name, data=b""))

        message_id = coalesce(event_data.get("id"), event_data.get("messageId"))
        return Message(
            id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            attachments=tuple(attachments),
        )
