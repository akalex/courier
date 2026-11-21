from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from ..config import AppConfig
from ..domain import Attachment, Message
from ..webhook_server import WebhookServer


logger = logging.getLogger(__name__)
HEALTHY_SESSION_STATUSES = {"WORKING"}
UNHEALTHY_SESSION_STATUSES = {"FAILED", "STOPPED", "SCAN_QR", "EXPIRED", "TERMINATED", "LOGGED_OUT"}
SESSION_MONITOR_INTERVAL = 30
LEGACY_COURIER_WEBHOOK_URL = "http://courier:8000/webhook"


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


def _with_required_events(config: dict[str, Any], webhook_url: str) -> list[dict[str, Any]]:
    required_events = ("message", "message.any", "session.status")
    default_retries = {"policy": "exponential", "delaySeconds": 2, "attempts": 10}
    webhooks = config.get("webhooks") or []
    if not isinstance(webhooks, list) or not all(isinstance(hook, dict) for hook in webhooks):
        raise ValueError("WAHA returned an invalid session webhook config")

    configured_webhooks = [dict(webhook) for webhook in webhooks if webhook.get("url") != LEGACY_COURIER_WEBHOOK_URL]
    for webhook in configured_webhooks:
        if webhook.get("url") == webhook_url:
            events = webhook.get("events") or []
            if not isinstance(events, list):
                raise ValueError("WAHA returned an invalid webhook event config")
            webhook["events"] = [*events, *(event for event in required_events if event not in events)]
            webhook.setdefault("retries", dict(default_retries))
            return configured_webhooks

    configured_webhooks.append(
        {"url": webhook_url, "events": list(required_events), "retries": dict(default_retries)},
    )
    return configured_webhooks


class WAHATransport:
    """Transport implementation for WAHA-based WhatsApp communication via httpx."""

    def __init__(
        self,
        *,
        session_name: str,
        base_url: str,
        api_key: str,
        webhook_url: str,
        listen_port: int = 8000,
        max_attachment_size: int = 20 * 1024 * 1024,
        include_from_me: bool = False,
    ) -> None:
        self.session_name = session_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.webhook_url = webhook_url
        self.listen_port = int(listen_port)
        self.max_attachment_size = int(max_attachment_size)
        self.include_from_me = include_from_me
        self.session: httpx.AsyncClient | None = None
        self._on_message = None
        self._alert_handler: Callable[[str], Awaitable[None]] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._active_alert: str | None = None
        self._webhook_server = WebhookServer(
            host="0.0.0.0",
            port=self.listen_port,
            handler=self._handle_webhook,
        )

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
            headers["X-Api-Key"] = self.api_key
        self.session = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        )

        try:
            await self._webhook_server.start()
            await self._ensure_waha_session()
            if self._alert_handler is not None:
                self._monitor_task = asyncio.create_task(self._monitor_session())
        except Exception:
            await self.stop()
            raise
        logger.info("WAHA transport started for %s", self.base_url)

    def set_alert_handler(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._alert_handler = handler

    async def _ensure_waha_session(self) -> None:
        """Create or configure the WAHA session and enable message webhooks."""
        if self.session is None:
            raise RuntimeError("WAHA transport is not started")

        session_path = f"/api/sessions/{quote(self.session_name, safe='')}"
        for attempt in range(12):
            try:
                await self._configure_waha_session(session_path)
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 or attempt == 11:
                    raise
            except httpx.TransportError:
                if attempt == 11:
                    raise
            await asyncio.sleep(5)

    async def _configure_waha_session(self, session_path: str) -> None:
        if self.session is None:
            raise RuntimeError("WAHA transport is not started")

        response = await self.session.get(session_path)
        if response.status_code == 404:
            response = await self.session.post(
                "/api/sessions/",
                json={
                    "name": self.session_name,
                    "config": {
                        "webhooks": [
                            {
                                "url": self.webhook_url,
                                "events": ["message", "message.any", "session.status"],
                                "retries": {
                                    "policy": "exponential",
                                    "delaySeconds": 2,
                                    "attempts": 10,
                                },
                            }
                        ]
                    },
                },
            )
            response.raise_for_status()
            status = "STOPPED"
        else:
            response.raise_for_status()
            session_data = response.json()
            if not isinstance(session_data, dict):
                raise ValueError("WAHA returned an invalid session response")
            config = session_data.get("config") or {}
            if not isinstance(config, dict):
                raise ValueError("WAHA returned an invalid session config")
            config["webhooks"] = _with_required_events(config, self.webhook_url)
            response = await self.session.put(
                session_path,
                json={"name": self.session_name, "config": config},
            )
            response.raise_for_status()
            status = str(session_data.get("status", "")).upper()

        if status not in {"WORKING", "STARTING", "SCAN_QR"}:
            response = await self.session.post(f"{session_path}/start")
            response.raise_for_status()
        logger.info("Configured WAHA session %s webhook for %s", self.session_name, self.webhook_url)

    async def _handle_webhook(self, payload: dict[str, Any]) -> None:
        if payload.get("event") == "session.status":
            await self._handle_session_status(payload)
            return
        message = self._message_from_payload(payload)
        if message is not None:
            await self._dispatch_message(message, payload)

    async def _handle_session_status(self, event: dict[str, Any]) -> None:
        event_payload = event.get("payload")
        data = event_payload if isinstance(event_payload, dict) else event
        event_session = data.get("session") or event.get("session")
        if event_session and event_session != self.session_name:
            return

        status = str(data.get("status", "")).upper()
        if status in HEALTHY_SESSION_STATUSES:
            await self._report_health(None)
        elif status in UNHEALTHY_SESSION_STATUSES:
            await self._report_health(
                "session:unhealthy",
                f"WAHA session {self.session_name} is {status}.",
            )

    async def _monitor_session(self) -> None:
        while True:
            await asyncio.sleep(SESSION_MONITOR_INTERVAL)
            if self.session is None:
                return
            await self._check_session_health()

    async def _check_session_health(self) -> None:
        if self.session is None:
            return
        try:
            response = await self.session.get(f"/api/sessions/{quote(self.session_name, safe='')}")
            response.raise_for_status()
            session_data = response.json()
            if not isinstance(session_data, dict):
                raise ValueError("WAHA returned an invalid session response")
            await self._handle_session_status(
                {"session": self.session_name, "payload": {"status": session_data.get("status", "")}}
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 404:
                await self._report_health(
                    "session:missing",
                    f"WAHA session {self.session_name} no longer exists (HTTP 404).",
                )
            elif status_code >= 500:
                await self._report_health(
                    "worker:unavailable",
                    f"WAHA worker returned HTTP {status_code} while checking session {self.session_name}.",
                )
            else:
                await self._report_health(
                    f"api:http-{status_code}",
                    f"WAHA health check for session {self.session_name} returned HTTP {status_code}.",
                )
            logger.warning("WAHA session health check failed", exc_info=True)
        except httpx.TransportError:
            logger.warning("WAHA session health check failed", exc_info=True)
            await self._report_health(
                "worker:unavailable",
                f"WAHA worker is unavailable while checking session {self.session_name}.",
            )
        except ValueError:
            logger.warning("WAHA session health check returned an invalid response", exc_info=True)
            await self._report_health(
                "api:invalid-response",
                f"WAHA returned an invalid health response for session {self.session_name}.",
            )

    async def _report_health(self, issue: str | None, message: str | None = None) -> None:
        if issue == self._active_alert:
            return
        if issue is None and self._active_alert is None:
            return

        self._active_alert = issue
        alert = (
            f"[Courier Bot] Recovery update: WAHA session {self.session_name} and worker are back online."
            if issue is None
            else f"[Courier Bot] {message or f'WAHA health issue: {issue}.'}"
        )
        logger.warning("%s", alert)
        if self._alert_handler is not None:
            try:
                await self._alert_handler(alert)
            except Exception:
                logger.exception("Failed to deliver WAHA health notification")

    async def stop(self) -> None:
        """Close the underlying WAHA session and any local webhooks."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        await self._webhook_server.stop()
        if self.session is not None and not self.session.is_closed:
            await self.session.aclose()
        self.session = None
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
            response = await self.session.post(
                "/api/sendText",
                json={"session": self.session_name, "chatId": chat_id, "text": text},
            )
            self._raise_for_status(response)
            return

        attachment = attachments[0]
        if len(attachment.data) > self.max_attachment_size:
            logger.warning(
                "Skipping oversized WAHA attachment %s (%d bytes)",
                attachment.filename,
                len(attachment.data),
            )
            response = await self.session.post(
                "/api/sendText",
                json={"session": self.session_name, "chatId": chat_id, "text": text},
            )
            self._raise_for_status(response)
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
            endpoint = "/api/sendImage"
        elif attachment.mime_type.startswith("video/"):
            endpoint = "/api/sendVideo"
        else:
            endpoint = "/api/sendFile"
        response = await self.session.post(endpoint, json=payload)
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {}
        error_message = error_payload.get("message", "") if isinstance(error_payload, dict) else ""
        if (
            endpoint == "/api/sendVideo"
            and response.status_code == 422
            and (
                isinstance(error_message, str)
                and 'Use "devlikeapro/waha:chrome" docker image to send video in WEBJS' in error_message
            )
        ):
            logger.warning("WAHA image does not support inline WEBJS video; forwarding as a file instead")
            response = await self.session.post("/api/sendFile", json=payload)
        self._raise_for_status(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error(
                "WAHA request %s %s failed with status %s: %s",
                response.request.method,
                response.request.url.path,
                response.status_code,
                response.text[:1000],
            )
            raise

    async def _dispatch_message(self, message: Message, payload: dict[str, Any]) -> None:
        """Hydrate and dispatch a WAHA message through the forwarder callback.

        Args:
            message: Normalized message model before media hydration.
            payload: Original WAHA message payload.

        Raises:
            Exception: If the callback or hydrated-message flow raises an error.
        """
        hydrated = await self._hydrate_media(message, payload.get("payload") or payload)
        if self._on_message is not None:
            await self._on_message(hydrated)

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
                return replace(message, attachments=attachments)
            return replace(message, attachments=())

        resolved_media_url = self._resolve_media_url(media_url, message.id)
        if resolved_media_url is None:
            return replace(message, attachments=())

        try:
            if self.session is None:
                return replace(message, attachments=())
            response = await self.session.get(
                str(resolved_media_url),
                timeout=httpx.Timeout(20.0, connect=10.0, read=20.0),
            )
            response.raise_for_status()
            data = response.content
            if not data:
                logger.warning("WAHA returned empty media for message id=%s; forwarding text only", message.id)
                return replace(message, attachments=())
        except httpx.HTTPError:
            logger.warning(
                "Failed to download WAHA media for message id=%s from %s; forwarding text only",
                message.id,
                resolved_media_url,
                exc_info=True,
            )
            return replace(message, attachments=())

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
        return replace(message, attachments=attachments)

    def _resolve_media_url(self, media_url: Any, message_id: str) -> httpx.URL | None:
        base_url = httpx.URL(self.base_url)
        try:
            resolved_media_url = base_url.join(str(media_url))
        except TypeError, ValueError:
            logger.warning("Skipping invalid WAHA media URL for message id=%s", message_id)
            return None
        if resolved_media_url.host in {"localhost", "127.0.0.1", "::1"}:
            resolved_media_url = resolved_media_url.copy_with(
                scheme=base_url.scheme,
                host=base_url.host,
                port=base_url.port,
            )
        if (
            resolved_media_url.username
            or resolved_media_url.password
            or resolved_media_url.scheme != base_url.scheme
            or resolved_media_url.host != base_url.host
            or resolved_media_url.port != base_url.port
        ):
            logger.warning("Skipping WAHA media download from a non-WAHA origin for message id=%s", message_id)
            return None
        return resolved_media_url

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
        if event and event not in {"message", "message.any"}:
            return None

        event_data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if not isinstance(event_data, dict):
            return None

        from_me = event_data.get("fromMe")
        if from_me is True and not self.include_from_me:
            logger.debug("Ignoring WAHA message from self; sender_id=%s", event_data.get("participant"))
            return None

        chat_id = _coalesce(
            event_data.get("chatId"),
            event_data.get("chat_id"),
            (event_data.get("chat") or {}).get("id"),
            event_data.get("to") if from_me is True else event_data.get("from"),
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


def build_waha_transport(config: AppConfig) -> WAHATransport:
    """Construct a WAHA transport using values from the app config.

    Args:
        config: Validated application config dictionary.

    Returns:
        A configured WAHA transport ready to be started.

    Raises:
        ValueError: If required transport configuration values are missing.
    """
    return WAHATransport(
        session_name=config.waha_session,
        base_url=config.waha_url,
        api_key=config.waha_api_key,
        webhook_url=config.webhook_url,
        listen_port=config.listen_port,
        max_attachment_size=config.max_attachment_size,
        include_from_me=config.include_from_me,
    )
