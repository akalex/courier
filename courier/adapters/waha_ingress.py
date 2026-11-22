from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.domain import Message
from ..core.health import HealthObservation
from ..infrastructure.logging_context import log_context
from .waha import WAHA, session_status_observation
from .waha_payload import WAHAWebhookParser, coalesce


logger = logging.getLogger(__name__)

MessageHandler = Callable[[Message], Awaitable[None]]
StatusHandler = Callable[[HealthObservation], Awaitable[None]]

MESSAGE_EVENTS = ("message", "message.any")
SESSION_STATUS_EVENT = "session.status"


class WAHAWebhookIngress:
    """Turn WAHA webhook events into messages and health observations for whichever handlers are wired.

    The events to subscribe to follow from the wiring, so a container without a message handler is
    never sent message events.
    """

    def __init__(
        self,
        waha: WAHA,
        *,
        include_from_me: bool,
        on_message: MessageHandler | None,
        on_status: StatusHandler | None,
    ) -> None:
        self._session_name = waha.session_name
        self._parser = WAHAWebhookParser(include_from_me=include_from_me, media_ref=waha.media.media_ref)
        self._on_message = on_message
        self._on_status = on_status

    @property
    def events(self) -> tuple[str, ...]:
        """WAHA webhook events this ingress needs to receive."""
        message_events = MESSAGE_EVENTS if self._on_message is not None else ()
        status_events = (SESSION_STATUS_EVENT,) if self._on_status is not None else ()
        return (*message_events, *status_events)

    async def handle(self, payload: dict[str, Any]) -> None:
        """Handle one webhook event.

        Raises:
            Exception: If forwarding a message fails; the webhook server then asks WAHA to redeliver it.
        """
        if payload.get("event") == SESSION_STATUS_EVENT:
            await self._handle_session_status(payload)
            return
        on_message = self._on_message
        if on_message is None:
            return
        message = self._parser.message_from_payload(payload)
        if message is not None:
            await self._dispatch(on_message, message, payload)

    async def _dispatch(self, on_message: MessageHandler, message: Message, payload: dict[str, Any]) -> None:
        webhook_metadata = payload.get("_courier_webhook")
        request_id = webhook_metadata.get("request_id") if isinstance(webhook_metadata, dict) else None
        event_id = coalesce(payload.get("id"))
        with log_context(
            request_id=str(request_id) if request_id else None,
            event_id=event_id,
            message_id=message.id,
        ):
            logger.info(
                "Received WAHA message event=%s event_id=%s request_id=%s message_id=%s chat_id=%s",
                payload.get("event") or "unknown",
                event_id or "-",
                str(request_id or "-"),
                message.id or "-",
                message.chat_id,
            )
            await on_message(message)

    async def _handle_session_status(self, payload: dict[str, Any]) -> None:
        if self._on_status is None:
            return
        event_payload = payload.get("payload")
        data = event_payload if isinstance(event_payload, dict) else payload
        session = data.get("session") or payload.get("session")
        if session and session != self._session_name:
            return
        observation = session_status_observation(self._session_name, str(data.get("status", "")))
        if observation is not None:
            await self._on_status(observation)
