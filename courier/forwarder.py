from __future__ import annotations

import asyncio
import logging
from collections import deque

from .domain import Message
from .filters import AllOf
from .ports import Filter, Transport


logger = logging.getLogger(__name__)


class Forwarder:
    """Route source-chat messages to a target chat after filtering and deduplication."""

    def __init__(
        self,
        *,
        source_chat_id: str,
        target_chat_id: str,
        transport: Transport,
        filter_policy: Filter | None = None,
        recent_ids: int = 1000,
        max_attachment_size: int = 20 * 1024 * 1024,
    ) -> None:
        """Initialize a forwarder with source and destination chat identifiers.

        Args:
            source_chat_id: Chat identifier that should be monitored for incoming messages.
            target_chat_id: Chat identifier that receives forwarded messages.
            transport: Transport implementation used to send outgoing messages.
            filter_policy: Optional message filter used to accept or reject incoming data.
            recent_ids: Number of recent message IDs to retain for deduplication.
            max_attachment_size: Maximum accepted attachment byte size before filtering.
        """
        self.source_chat_id = source_chat_id
        self.target_chat_id = target_chat_id
        self.transport = transport
        self.filter_policy = filter_policy or AllOf()
        self._recent_ids: deque[str] = deque(maxlen=recent_ids)
        self._recent_id_set: set[str] = set()
        self._lock = asyncio.Lock()
        self.max_attachment_size = max_attachment_size

    async def handle_message(self, message: Message) -> None:
        """Validate, filter, deduplicate, and forward a single incoming message.

        Args:
            message: The incoming normalized message to process.

        Raises:
            Exception: Any transport send failure is caught and logged internally.
        """
        if message.chat_id != self.source_chat_id:
            logger.debug("Ignoring message from non-source chat_id=%s", message.chat_id)
            return

        if message.id in self._recent_id_set:
            logger.info("Dropping duplicate forwarded message id=%s", message.id)
            return

        if not self.filter_policy.matches(message):
            logger.info("Filtered out message id=%s chat_id=%s", message.id, message.chat_id)
            return

        formatted_text = self._format_text(message)
        attachments = self._sanitize_attachments(message)

        async with self._lock:
            if message.id in self._recent_id_set:
                logger.info("Duplicate observed while forwarding id=%s", message.id)
                return
            self._recent_ids.append(message.id)
            self._recent_id_set.add(message.id)

        try:
            await self.transport.send(self.target_chat_id, formatted_text, attachments)
            logger.info("Forwarded message id=%s from chat_id=%s", message.id, message.chat_id)
        except Exception:
            logger.exception("Failed to forward message id=%s to chat_id=%s", message.id, self.target_chat_id)

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
