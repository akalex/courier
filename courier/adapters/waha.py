from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from ..domain import Attachment, Message


logger = logging.getLogger(__name__)


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty value from a mapping for the requested keys."""
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_sender(value: Any) -> str:
    """Normalize sender IDs by removing leading plus signs and email suffixes."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("+"):
        normalized = normalized[1:]
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


def _coalesce(*values: Any) -> str:
    """Return the first non-empty argument converted to a string."""
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


class WAHATransport:
    """Transport implementation for WAHA-based WhatsApp communication via httpx."""

    def __init__(
        self,
        *,
        session_name: str,
        base_url: str,
        api_key: str,
        webhook_url: str,
        loop: asyncio.AbstractEventLoop | None = None,
        listen_port: int = 8000,
        max_attachment_size: int = 20 * 1024 * 1024,
    ) -> None:
        self.session_name = session_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.webhook_url = webhook_url
        self.listen_port = int(listen_port)
        self.max_attachment_size = int(max_attachment_size)
        self.loop = loop
        self.session: httpx.AsyncClient | None = None
        self._on_message = None

    async def start(self, on_message) -> None:
        """Start the WAHA HTTP client so messages can be sent and received.

        Args:
            on_message: Callback invoked for each incoming WAHA message.

        Raises:
            RuntimeError: If the client cannot initialize its HTTP session.
        """
        self._on_message = on_message
        headers = {"User-Agent": "courier/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self.session = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        )
        logger.info("WAHA transport started for %s", self.base_url)

    async def stop(self) -> None:
        """Close the underlying WAHA session.

        Raises:
            Exception: If the async client close operation fails.
        """
        if self.session is not None and not self.session.is_closed:
            await self.session.aclose()
        logger.info("WAHA transport stopped")

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None:
        """Send a text message or first attachment to the WAHA API.

        Args:
            chat_id: Destination WhatsApp chat identifier.
            text: Message text to send.
            attachments: Optional media attachments to include with the message.

        Raises:
            RuntimeError: If the transport has not been started yet.
            httpx.HTTPError: If the underlying WAHA request fails.
        """
        if self.session is None:
            raise RuntimeError("WAHA transport is not started")

        if not attachments:
            await self.session.post(
                "/api/sendText",
                json={"session": self.session_name, "chatId": chat_id, "text": text},
            )
            return

        attachment = attachments[0]
        if len(attachment.data) > self.max_attachment_size:
            logger.warning(
                "Skipping oversized WAHA attachment %s (%d bytes)",
                attachment.filename,
                len(attachment.data),
            )
            await self.session.post(
                "/api/sendText",
                json={"session": self.session_name, "chatId": chat_id, "text": text},
            )
            return

        file_payload = {
            "mimetype": attachment.mime_type,
            "filename": attachment.filename or "attachment",
            "data": base64.b64encode(attachment.data).decode("ascii"),
        }
        payload = {
            "session": self.session_name,
            "chatId": chat_id,
            "caption": text,
            "file": file_payload,
        }
        if attachment.mime_type.startswith("image/"):
            await self.session.post("/api/sendImage", json=payload)
            return
        await self.session.post("/api/sendFile", json=payload)

    async def _dispatch_message(self, message: Message, payload: dict[str, Any]) -> None:
        """Hydrate and dispatch a WAHA message through the forwarder callback.

        Args:
            message: Normalized message model before media hydration.
            payload: Original WAHA message payload.

        Raises:
            Exception: If the callback or hydrated-message flow raises an error.
        """
        try:
            hydrated = await self._hydrate_media(message, payload.get("payload") or payload)
            if self._on_message is not None:
                await self._on_message(hydrated)
        except Exception:
            logger.exception("WAHA webhook processing failed")

    async def _hydrate_media(self, message: Message, payload: dict[str, Any]) -> Message:
        """Attach downloaded media bytes back to a normalized WAHA message.

        Args:
            message: Message to enrich with media data.
            payload: WAHA media payload dictionary.

        Returns:
            The message with media attachments restored when possible.

        Raises:
            ValueError: If the payload cannot be decoded as media content.
        """
        media = payload.get("media") if isinstance(payload, dict) else None
        if not isinstance(media, dict):
            return message

        media_url = _first(media, "url", "mediaUrl")
        if not media_url:
            payload_data = media.get("data")
            if payload_data:
                try:
                    decoded = base64.b64decode(payload_data, validate=False)
                except Exception:
                    decoded = b""
                attachments = tuple(
                    Attachment(
                        mime_type=media.get("mimeType") or media.get("mimetype") or attachment.mime_type,
                        filename=media.get("fileName") or media.get("filename") or attachment.filename,
                        data=decoded if attachment.data == b"" else attachment.data,
                    )
                    for attachment in message.attachments
                )
                return Message(
                    id=message.id,
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    sender_name=message.sender_name,
                    text=message.text,
                    attachments=attachments,
                )
            return message

        try:
            if self.session is None:
                return message
            response = await self.session.get(
                media_url,
                timeout=httpx.Timeout(20.0, connect=10.0, read=20.0),
            )
            data = response.content
        except Exception:
            logger.warning("Failed to download WAHA media for message id=%s from %s", message.id, media_url)
            return message

        attachment = (
            message.attachments[0]
            if message.attachments
            else Attachment(
                mime_type=media.get("mimeType") or media.get("mimetype") or "application/octet-stream",
                filename=media.get("fileName") or media.get("filename") or "attachment",
                data=data,
            )
        )
        attachments = (Attachment(attachment.mime_type, attachment.filename, data),)
        return Message(
            id=message.id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            text=message.text,
            attachments=attachments,
        )

    def _message_from_payload(self, payload: Any) -> Message | None:
        """Parse a WAHA event payload into the normalized Courier message model.

        Args:
            payload: Raw WAHA event payload dictionary.

        Returns:
            A normalized message, or None if the payload is not a message event.

        Raises:
            ValueError: If the payload cannot be parsed into a valid sender/chat tuple.
        """
        if not isinstance(payload, dict):
            return None

        event = payload.get("event")
        if event and event != "message":
            return None

        event_data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if not isinstance(event_data, dict):
            return None

        from_me = event_data.get("fromMe")
        if from_me is True:
            logger.debug("Ignoring WAHA message from self; sender_id=%s", event_data.get("participant"))
            return None

        chat_id = _coalesce(
            event_data.get("chatId"),
            event_data.get("chat_id"),
            (event_data.get("chat") or {}).get("id"),
        )
        if not chat_id:
            return None

        participant = _coalesce(
            event_data.get("participant"),
            event_data.get("from"),
            event_data.get("sender"),
            (event_data.get("sender") or {}).get("id"),
            (event_data.get("author") or {}).get("id"),
        )
        sender_id = _normalize_sender(participant)
        sender_name = _coalesce(
            event_data.get("senderName"),
            event_data.get("pushName"),
            event_data.get("sender_name"),
            (event_data.get("sender") or {}).get("name"),
            (event_data.get("author") or {}).get("name"),
            sender_id,
        )

        logger.debug(
            "WAHA raw sender id=%s name=%s chat_id=%s",
            _coalesce(event_data.get("participant"), event_data.get("from"), event_data.get("sender")),
            _coalesce(event_data.get("senderName"), event_data.get("pushName"), event_data.get("sender_name")),
            chat_id,
        )

        text = _coalesce(
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
            data = b""
            attachments.append(Attachment(mime_type=mime_type, filename=file_name, data=data))

        message_id = _coalesce(event_data.get("id"), event_data.get("messageId"))
        return Message(
            id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            attachments=tuple(attachments),
        )


def build_waha_transport(config: dict[str, Any]) -> WAHATransport:
    """Construct a WAHA transport using values from the app config.

    Args:
        config: Validated application config dictionary.

    Returns:
        A configured WAHA transport ready to be started.

    Raises:
        ValueError: If required transport configuration values are missing.
    """
    return WAHATransport(
        session_name=config.get("waha_session") or config.get("WAHA_SESSION") or "default",
        base_url=config.get("waha_url") or config.get("WAHA_URL") or "http://localhost:3000",
        api_key=config.get("waha_api_key") or config.get("WAHA_API_KEY") or "",
        webhook_url=config.get("webhook_url") or config.get("WHATSAPP_HOOK_URL") or "http://bot:8000/webhook",
        listen_port=int(config.get("listen_port") or 8000),
        max_attachment_size=int(config.get("max_attachment_size") or 20 * 1024 * 1024),
    )
