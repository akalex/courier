from __future__ import annotations

import logging
from collections import OrderedDict

from .domain import Attachment, MediaRef, Message
from .filters import AllOf
from .media import candidate_media_kinds, media_kind
from .ports import Filter, MessageDestination


logger = logging.getLogger(__name__)


class _DeliveryLedger:
    """Message ids that are being delivered or were delivered recently.

    Every method is synchronous, so a claim cannot interleave with another coroutine's claim.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._in_flight: set[str] = set()
        self._settled: OrderedDict[str, None] = OrderedDict()

    def claim(self, message_id: str) -> bool:
        if not message_id:
            logger.warning("Message has no id; it cannot be deduplicated")
            return True
        if message_id in self._in_flight or message_id in self._settled:
            return False
        self._in_flight.add(message_id)
        return True

    def release(self, message_id: str) -> None:
        self._in_flight.discard(message_id)

    def settle(self, message_id: str) -> None:
        if not message_id:
            return
        self._in_flight.discard(message_id)
        self._settled[message_id] = None
        if len(self._settled) > self._capacity:
            self._settled.popitem(last=False)


class Forwarder:
    """Route source-chat messages to a target chat after filtering and deduplication.

    Checks that need only text and sender run before any media is fetched. A message id is
    claimed just before the first fetch or send, released if sending fails so the source can
    redeliver it, and remembered once handled.
    """

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
            max_attachment_size: Maximum accepted attachment byte size.
            forward_content: Message content types to send: text, media, or both.
            media_types: Media categories eligible for forwarding.
        """
        if recent_ids <= 0:
            raise ValueError("recent_ids must be greater than zero")
        self.source_chat_id = source_chat_id
        self.target_chat_id = target_chat_id
        self.destination = destination
        self.filter_policy = filter_policy or AllOf()
        self.max_attachment_size = max_attachment_size
        self.forward_content = frozenset(forward_content)
        self.media_types = frozenset(media_types)
        self._ledger = _DeliveryLedger(recent_ids)

    async def handle_message(self, message: Message) -> None:
        """Filter, deduplicate, fetch eligible media for, and forward a single incoming message.

        Args:
            message: The incoming normalized message to process.

        Raises:
            Exception: If sending the message fails; the webhook layer uses this to request redelivery.
        """
        if message.chat_id != self.source_chat_id:
            logger.debug("Ignoring message from non-source chat_id=%s", message.chat_id)
            return
        if not self.filter_policy.matches(message):
            logger.info("Filtered out message id=%s chat_id=%s", message.id, message.chat_id)
            return

        text = message.text if "text" in self.forward_content else ""
        candidates = self._candidate_media(message)
        if not text and not candidates:
            logger.info("Dropping message id=%s with no eligible content", message.id)
            return
        if not self._ledger.claim(message.id):
            logger.info("Dropping duplicate message id=%s", message.id)
            return

        try:
            await self._deliver(message, text, candidates)
        except BaseException:
            self._ledger.release(message.id)
            raise
        self._ledger.settle(message.id)

    def _candidate_media(self, message: Message) -> tuple[MediaRef, ...]:
        if "media" not in self.forward_content:
            return ()
        return tuple(
            media
            for media in message.attachments
            if candidate_media_kinds(media.mime_type, media.filename) & self.media_types
        )

    async def _deliver(self, message: Message, text: str, candidates: tuple[MediaRef, ...]) -> None:
        attachments = await self._fetch_eligible(message, candidates)
        if not text and not attachments:
            logger.info("Dropping message id=%s with no eligible media after download", message.id)
            return
        try:
            await self.destination.send(self.target_chat_id, text, attachments)
        except Exception:
            logger.exception("Failed to forward message id=%s to chat_id=%s", message.id, self.target_chat_id)
            raise
        logger.info("Forwarded message id=%s from chat_id=%s", message.id, message.chat_id)

    async def _fetch_eligible(self, message: Message, candidates: tuple[MediaRef, ...]) -> tuple[Attachment, ...]:
        attachments = []
        for media in candidates:
            data = await media.fetch(max_bytes=self.max_attachment_size)
            if data is None:
                logger.warning(
                    "Skipping unavailable or oversized attachment for message id=%s: %s",
                    message.id,
                    media.filename or media.mime_type,
                )
                continue
            attachment = Attachment(media.mime_type, media.filename, data)
            if media_kind(attachment) in self.media_types:
                attachments.append(attachment)
        return tuple(attachments)
