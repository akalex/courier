from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from .. import __version__
from ..config import WahaSettings
from ..core.domain import Attachment
from ..core.health import HealthObservation
from ..core.media import is_mp4_attachment
from .waha_media import WAHAMediaFetcher, is_retryable_status


logger = logging.getLogger(__name__)

HEALTHY_SESSION_STATUSES = frozenset({"WORKING"})
UNHEALTHY_SESSION_STATUSES = frozenset({"FAILED", "STOPPED", "SCAN_QR", "EXPIRED", "TERMINATED", "LOGGED_OUT"})
ACTIVE_SESSION_STATUSES = frozenset({"WORKING", "STARTING", "SCAN_QR"})
SESSION_SETUP_MAX_ATTEMPTS = 12
SESSION_SETUP_RETRY_DELAY_SECONDS = 5
LEGACY_COURIER_WEBHOOK_URL = "http://courier:8000/webhook"
DEFAULT_WEBHOOK_RETRIES = {"policy": "exponential", "delaySeconds": 2, "attempts": 10}
CHROME_REQUIRED_FOR_VIDEO = 'Use "devlikeapro/waha:chrome" docker image to send video in WEBJS'

Sleep = Callable[[float], Awaitable[None]]


class WAHA:
    """One WAHA session, reached through a single HTTP client shared by every WAHA operation."""

    def __init__(
        self,
        *,
        base_url: str,
        session_name: str,
        http: httpx.AsyncClient,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.session_name = session_name
        self._http = http
        self._sleep = sleep
        self._session_path = f"/api/sessions/{quote(session_name, safe='')}"
        self.destination = WAHADestination(http=http, session_name=session_name)
        self.probe = WAHASessionProbe(http=http, session_name=session_name, session_path=self._session_path)
        self.media = WAHAMediaFetcher(base_url=base_url, http=http)

    async def provision(self, webhook_url: str, events: tuple[str, ...]) -> None:
        """Create or update the session so this Courier's webhook receives exactly `events`, then start it.

        Server errors, request timeouts, and connection failures are retried; other client errors are raised.
        """
        for attempt in range(1, SESSION_SETUP_MAX_ATTEMPTS + 1):
            try:
                await self._configure_session(webhook_url, events)
                return
            except httpx.HTTPStatusError as exc:
                if not is_retryable_status(exc.response.status_code) or attempt == SESSION_SETUP_MAX_ATTEMPTS:
                    raise
            except httpx.TransportError:
                if attempt == SESSION_SETUP_MAX_ATTEMPTS:
                    raise
            await self._sleep(SESSION_SETUP_RETRY_DELAY_SECONDS)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _configure_session(self, webhook_url: str, events: tuple[str, ...]) -> None:
        response = await self._http.get(self._session_path)
        if response.status_code == HTTPStatus.NOT_FOUND:
            response = await self._http.post(
                "/api/sessions/",
                json={"name": self.session_name, "config": {"webhooks": [_webhook(webhook_url, events)]}},
            )
            response.raise_for_status()
            status = "STOPPED"
        else:
            response.raise_for_status()
            session_data = _json_object(response, "WAHA returned an invalid session response")
            config = session_data.get("config") or {}
            if not isinstance(config, dict):
                raise ValueError("WAHA returned an invalid session config")
            config["webhooks"] = _with_webhook(config.get("webhooks"), webhook_url, events)
            response = await self._http.put(self._session_path, json={"name": self.session_name, "config": config})
            response.raise_for_status()
            status = str(session_data.get("status", "")).upper()

        if status not in ACTIVE_SESSION_STATUSES:
            response = await self._http.post(f"{self._session_path}/start")
            response.raise_for_status()
        logger.info("Configured WAHA session %s webhook %s for events %s", self.session_name, webhook_url, events)


class WAHADestination:
    """Send messages to WhatsApp chats through a WAHA session."""

    def __init__(self, *, http: httpx.AsyncClient, session_name: str) -> None:
        self._http = http
        self._session_name = session_name

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None:
        """Send text, or the first attachment captioned with the text.

        Raises:
            httpx.HTTPError: If the WAHA request fails.
        """
        if not attachments:
            response = await self._http.post(
                "/api/sendText",
                json={"session": self._session_name, "chatId": chat_id, "text": text},
            )
            _raise_for_status(response)
            return

        attachment = attachments[0]
        mime_type = "video/mp4" if is_mp4_attachment(attachment) else attachment.mime_type
        payload = {
            "session": self._session_name,
            "chatId": chat_id,
            "caption": text,
            "file": {
                "mimetype": mime_type,
                "filename": attachment.filename or "attachment",
                "data": base64.b64encode(attachment.data).decode("ascii"),
            },
        }
        endpoint = _send_endpoint(mime_type)
        response = await self._http.post(endpoint, json=payload)
        if endpoint == "/api/sendVideo" and _requires_chrome_for_video(response):
            logger.warning("WAHA image does not support inline WEBJS video; forwarding as a file instead")
            response = await self._http.post("/api/sendFile", json=payload)
        _raise_for_status(response)

    async def stop(self) -> None:
        """Nothing to release: the shared client is closed by WAHA.aclose()."""


class WAHASessionProbe:
    """Check the WAHA session over HTTP and describe the outcome as a health observation."""

    def __init__(self, *, http: httpx.AsyncClient, session_name: str, session_path: str) -> None:
        self._http = http
        self._session_name = session_name
        self._session_path = session_path

    async def check(self) -> HealthObservation | None:
        name = self._session_name
        try:
            response = await self._http.get(self._session_path)
            response.raise_for_status()
            session_data = _json_object(response, "WAHA returned an invalid session response")
        except httpx.HTTPStatusError as exc:
            logger.warning("WAHA session health check failed", exc_info=True)
            return _http_failure_observation(name, exc.response.status_code)
        except httpx.TransportError:
            logger.warning("WAHA session health check failed", exc_info=True)
            return HealthObservation("worker:unavailable", f"WAHA worker is unavailable while checking session {name}.")
        except ValueError:
            logger.warning("WAHA session health check returned an invalid response", exc_info=True)
            return HealthObservation(
                "api:invalid-response",
                f"WAHA returned an invalid health response for session {name}.",
            )
        return session_status_observation(name, str(session_data.get("status", "")))


def session_status_observation(session_name: str, status: str) -> HealthObservation | None:
    """Describe a WAHA session status as a health observation; None for transitional statuses."""
    status = status.upper()
    if status in HEALTHY_SESSION_STATUSES:
        return HealthObservation(None, f"Recovery update: WAHA session {session_name} and worker are back online.")
    if status in UNHEALTHY_SESSION_STATUSES:
        return HealthObservation("session:unhealthy", f"WAHA session {session_name} is {status}.")
    return None


def build_waha(settings: WahaSettings, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> WAHA:
    """Construct the WAHA session client.

    Args:
        settings: The container's WAHA settings (URL and session).
        api_key: WAHA's API key from WAHA_API_KEY; empty when WAHA does not require one.
        transport: Optional httpx transport replacing the network, for tests.
    """
    headers = {"User-Agent": f"courier/{__version__}"}
    if api_key:
        headers["X-Api-Key"] = api_key
    http = httpx.AsyncClient(
        base_url=settings.url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        transport=transport,
    )
    return WAHA(base_url=settings.url, session_name=settings.session, http=http)


def _http_failure_observation(session_name: str, status_code: int) -> HealthObservation:
    if status_code == HTTPStatus.NOT_FOUND:
        return HealthObservation(
            "session:missing",
            f"WAHA session {session_name} no longer exists (HTTP {status_code}).",
        )
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return HealthObservation(
            "worker:unavailable",
            f"WAHA worker returned HTTP {status_code} while checking session {session_name}.",
        )
    return HealthObservation(
        f"api:http-{status_code}",
        f"WAHA health check for session {session_name} returned HTTP {status_code}.",
    )


def _json_object(response: httpx.Response, error: str) -> dict[str, Any]:
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(error)
    return data


def _webhook(url: str, events: tuple[str, ...]) -> dict[str, Any]:
    return {"url": url, "events": list(events), "retries": dict(DEFAULT_WEBHOOK_RETRIES)}


def _with_webhook(webhooks: Any, webhook_url: str, events: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the session webhooks with this Courier's entry set to exactly `events` and the legacy entry removed."""
    webhooks = webhooks or []
    if not isinstance(webhooks, list) or not all(isinstance(hook, dict) for hook in webhooks):
        raise ValueError("WAHA returned an invalid session webhook config")

    configured = [dict(hook) for hook in webhooks if hook.get("url") != LEGACY_COURIER_WEBHOOK_URL]
    for hook in configured:
        if hook.get("url") == webhook_url:
            hook["events"] = list(events)
            hook.setdefault("retries", dict(DEFAULT_WEBHOOK_RETRIES))
            return configured
    return [*configured, _webhook(webhook_url, events)]


def _send_endpoint(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "/api/sendImage"
    if mime_type.startswith("video/"):
        return "/api/sendVideo"
    return "/api/sendFile"


def _requires_chrome_for_video(response: httpx.Response) -> bool:
    if response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    message = body.get("message") if isinstance(body, dict) else None
    return isinstance(message, str) and CHROME_REQUIRED_FOR_VIDEO in message


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
