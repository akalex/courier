from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from .domain import Message
from .filters import AllOf
from .ports import Filter, MessageDestination


logger = logging.getLogger(__name__)


class Forwarder:
    """Route source-chat messages to a target chat after filtering and deduplication."""

    def __init__(
        self,
        *,
        source_chat_id: str,
        target_chat_id: str,
        destination: MessageDestination,
        filter_policy: Filter | None = None,
        recent_ids: int = 1000,
        max_attachment_size: int = 20 * 1024 * 1024,
    ) -> None:
        """Initialize a forwarder with source and destination chat identifiers.

        Args:
            source_chat_id: Chat identifier that should be monitored for incoming messages.
            target_chat_id: Chat identifier that receives forwarded messages.
            destination: Messaging destination used to send outgoing messages.
            filter_policy: Optional message filter used to accept or reject incoming data.
            recent_ids: Number of recent message IDs to retain for deduplication.
            max_attachment_size: Maximum accepted attachment byte size before filtering.
        """
        self.source_chat_id = source_chat_id
        self.target_chat_id = target_chat_id
        self.destination = destination
        self.filter_policy = filter_policy or AllOf()
        if recent_ids <= 0:
            raise ValueError("recent_ids must be greater than zero")
        self._recent_ids_limit = recent_ids
        self._recent_ids: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()
        self.max_attachment_size = max_attachment_size

    async def handle_message(self, message: Message) -> None:
        """Validate, filter, deduplicate, and forward a single incoming message.

        Args:
            message: The incoming normalized message to process.

        Raises:
            Exception: If sending the message fails; the webhook layer uses this to request redelivery.
        """
        if message.chat_id != self.source_chat_id:
            logger.debug("Ignoring message from non-source chat_id=%s", message.chat_id)
            return

        async with self._lock:
            if message.id in self._recent_ids:
                logger.info("Dropping duplicate forwarded message id=%s", message.id)
                return

            if not self.filter_policy.matches(message):
                logger.info("Filtered out message id=%s chat_id=%s", message.id, message.chat_id)
                return

            formatted_text = self._format_text(message)
            attachments = self._sanitize_attachments(message)
            try:
                await self.destination.send(self.target_chat_id, formatted_text, attachments)
            except Exception:
                logger.exception("Failed to forward message id=%s to chat_id=%s", message.id, self.target_chat_id)
                raise

            self._recent_ids[message.id] = None
            if len(self._recent_ids) > self._recent_ids_limit:
                self._recent_ids.popitem(last=False)
            logger.info("Forwarded message id=%s from chat_id=%s", message.id, message.chat_id)

    def _format_text(self, message: Message) -> str:
        """Preserve the original message text without adding a sender prefix.

        Args:
            message: Message to render into a forwarded text payload.

        Returns:
            The original message text.
        """
        return message.text

    def _sanitize_attachments(self, message: Message):
        """Remove oversized attachments before sending them downstream.

        Args:
            message: Message whose attachments should be checked.

        Returns:
            A tuple containing only attachments that fit within the configured size limit.
        """
        sanitized = []
        for attachment in message.attachments:
            if attachment.data and len(attachment.data) > self.max_attachment_size:
                logger.warning(
                    "Skipping oversized attachment for message id=%s: %s (%d bytes)",
                    message.id,
                    attachment.filename or attachment.mime_type,
                    len(attachment.data),
                )
                continue
            sanitized.append(attachment)
        return tuple(sanitized)
