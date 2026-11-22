from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import PurePath

from .domain import Attachment, Message
from .filters import AllOf
from .media import is_mp4_attachment
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
        forward_content: tuple[str, ...] = ("text", "media"),
        media_types: tuple[str, ...] = ("image", "video", "audio", "file"),
    ) -> None:
        """Initialize a forwarder with source and destination chat identifiers.

        Args:
            source_chat_id: Chat identifier that should be monitored for incoming messages.
            target_chat_id: Chat identifier that receives forwarded messages.
            destination: Messaging destination used to send outgoing messages.
            filter_policy: Optional message filter used to accept or reject incoming data.
            recent_ids: Number of recent message IDs to retain for deduplication.
            max_attachment_size: Maximum accepted attachment byte size before filtering.
            forward_content: Message content types to send: text, media, or both.
            media_types: Media categories eligible for forwarding.
        """
        self.source_chat_id = source_chat_id
        self.target_chat_id = target_chat_id
        self.destination = destination
        self.filter_policy = filter_policy or AllOf()
        if recent_ids <= 0:
            raise ValueError("recent_ids must be greater than zero")
        self._recent_ids_limit = recent_ids
        self._recent_ids: OrderedDict[str, None] = OrderedDict()
        self._in_flight_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self.max_attachment_size = max_attachment_size
        self.forward_content = frozenset(forward_content)
        self.media_types = frozenset(media_types)

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
            if message.id in self._recent_ids or message.id in self._in_flight_ids:
                logger.info("Dropping duplicate forwarded message id=%s", message.id)
                return
            self._in_flight_ids.add(message.id)

        if not self.filter_policy.matches(message):
            await self._release_message(message.id)
            logger.info("Filtered out message id=%s chat_id=%s", message.id, message.chat_id)
            return

        formatted_text = self._format_text(message) if "text" in self.forward_content else ""
        attachments = self._sanitize_attachments(message)
        if not formatted_text and not attachments:
            await self._release_message(message.id)
            logger.info("Dropping message id=%s with no eligible content", message.id)
            return
        try:
            await self.destination.send(self.target_chat_id, formatted_text, attachments)
        except Exception:
            await self._release_message(message.id)
            logger.exception("Failed to forward message id=%s to chat_id=%s", message.id, self.target_chat_id)
            raise

        async with self._lock:
            self._in_flight_ids.discard(message.id)
            self._recent_ids[message.id] = None
            if len(self._recent_ids) > self._recent_ids_limit:
                self._recent_ids.popitem(last=False)
        logger.info("Forwarded message id=%s from chat_id=%s", message.id, message.chat_id)

    async def _release_message(self, message_id: str) -> None:
        async with self._lock:
            self._in_flight_ids.discard(message_id)

    def _format_text(self, message: Message) -> str:
        """Preserve the original message text without adding a sender prefix.

        Args:
            message: Message to render into a forwarded text payload.

        Returns:
            The original message text.
        """
        return message.text

    def _sanitize_attachments(self, message: Message) -> tuple[Attachment, ...]:
        """Select eligible attachments and remove oversized files before sending.

        Args:
            message: Message whose attachments should be checked.

        Returns:
            A tuple containing only attachments that fit within the configured size limit.
        """
        sanitized = []
        if "media" not in self.forward_content:
            return ()

        for attachment in message.attachments:
            if self._media_type(attachment) not in self.media_types:
                continue
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

    @staticmethod
    def _media_type(attachment: Attachment) -> str:
        mime_type = attachment.mime_type.lower()
        if mime_type.startswith(("image/", "video/", "audio/")):
            return mime_type.split("/", 1)[0]
        video_extensions = {".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
        filename_suffix = PurePath(attachment.filename or "").suffix.lower()
        if filename_suffix in video_extensions and mime_type == "application/octet-stream":
            return "video"
        if is_mp4_attachment(attachment):
            return "video"
        return "file"
