from __future__ import annotations

import asyncio
import base64
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from .. import __version__
from ..config import AppConfig
from ..core.domain import Attachment, Message
from ..core.media import is_mp4_attachment
from ..infrastructure.logging_context import log_context
from ..infrastructure.webhook_server import WebhookServer
from .waha_media import WAHAMediaHydrator
from .waha_payload import WAHAWebhookParser, coalesce


logger = logging.getLogger(__name__)
HEALTHY_SESSION_STATUSES = {"WORKING"}
UNHEALTHY_SESSION_STATUSES = {"FAILED", "STOPPED", "SCAN_QR", "EXPIRED", "TERMINATED", "LOGGED_OUT"}
SESSION_MONITOR_INTERVAL = 30
SESSION_SETUP_MAX_ATTEMPTS = 12
SESSION_SETUP_RETRY_DELAY_SECONDS = 5
LEGACY_COURIER_WEBHOOK_URL = "http://courier:8000/webhook"
WAHA_RECENT_MESSAGE_IDS = 1000


def _coalesce(*values: Any) -> str:
    return coalesce(*values)


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
        self._recent_message_ids: OrderedDict[str, None] = OrderedDict()
        self._recent_message_ids_lock = asyncio.Lock()
        self._payload_parser = WAHAWebhookParser(include_from_me=include_from_me)
        self._media_hydrator = WAHAMediaHydrator(base_url=base_url)
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
        headers = {"User-Agent": f"courier/{__version__}"}
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
        for attempt in range(SESSION_SETUP_MAX_ATTEMPTS):
            try:
                await self._configure_waha_session(session_path)
                return
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
                    and exc.response.status_code != HTTPStatus.REQUEST_TIMEOUT
                ) or attempt + 1 == SESSION_SETUP_MAX_ATTEMPTS:
                    raise
            except httpx.TransportError:
                if attempt + 1 == SESSION_SETUP_MAX_ATTEMPTS:
                    raise
            await asyncio.sleep(SESSION_SETUP_RETRY_DELAY_SECONDS)

    async def _configure_waha_session(self, session_path: str) -> None:
        if self.session is None:
            raise RuntimeError("WAHA transport is not started")

        response = await self.session.get(session_path)
        if response.status_code == HTTPStatus.NOT_FOUND:
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
            if not await self._claim_message(message.id):
                logger.info("Dropping duplicate WAHA message before media hydration id=%s", message.id)
                return
            webhook_metadata = payload.get("_courier_webhook")
            request_id = webhook_metadata.get("request_id") if isinstance(webhook_metadata, dict) else None
            event_id = _coalesce(payload.get("id"))
            message_id = message.id
            with log_context(
                request_id=str(request_id) if request_id else None,
                event_id=event_id,
                message_id=message_id,
            ):
                logger.info(
                    "Received WAHA message event=%s event_id=%s request_id=%s message_id=%s chat_id=%s",
                    payload.get("event") or "unknown",
                    event_id or "-",
                    str(request_id or "-"),
                    message_id or "-",
                    message.chat_id,
                )
                try:
                    await self._dispatch_message(message, payload)
                except Exception:
                    await self._release_message(message.id)
                    raise

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
            if status_code == HTTPStatus.NOT_FOUND:
                await self._report_health(
                    "session:missing",
                    f"WAHA session {self.session_name} no longer exists (HTTP {HTTPStatus.NOT_FOUND.value}).",
                )
            elif status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
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

        mime_type = attachment.mime_type
        if is_mp4_attachment(attachment):
            mime_type = "video/mp4"

        file_payload = {
            "mimetype": mime_type,
            "filename": attachment.filename or "attachment",
            "data": base64.b64encode(attachment.data).decode("ascii"),
        }
        payload = {
            "session": self.session_name,
            "chatId": chat_id,
            "caption": text,
            "file": file_payload,
        }
        if mime_type.startswith("image/"):
            endpoint = "/api/sendImage"
        elif mime_type.startswith("video/"):
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
            and response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
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

    async def _claim_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        async with self._recent_message_ids_lock:
            if message_id in self._recent_message_ids:
                return False
            self._recent_message_ids[message_id] = None
            if len(self._recent_message_ids) > WAHA_RECENT_MESSAGE_IDS:
                self._recent_message_ids.popitem(last=False)
        return True

    async def _release_message(self, message_id: str) -> None:
        if not message_id:
            return
        async with self._recent_message_ids_lock:
            self._recent_message_ids.pop(message_id, None)

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
        """Attach downloaded media bytes back to a normalized WAHA message."""
        return await self._media_hydrator.hydrate(message, payload, self.session)

    def _resolve_media_url(self, media_url: Any, message_id: str) -> httpx.URL | None:
        return self._media_hydrator.resolve_media_url(media_url, message_id)

    def _message_from_payload(self, payload: Any) -> Message | None:
        """Parse a WAHA event payload into the normalized Courier message model."""
        return self._payload_parser.message_from_payload(payload)


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
