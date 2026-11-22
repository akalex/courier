from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class TransientMediaError(Exception):
    """Media could not be fetched now, but fetching it again later may succeed."""


class MediaRef(Protocol):
    """Media attached to a message whose bytes may not have been downloaded yet."""

    @property
    def mime_type(self) -> str: ...

    @property
    def filename(self) -> str | None: ...

    async def fetch(self, *, max_bytes: int) -> bytes | None:
        """Return the media bytes, or None when they will never be available or exceed max_bytes.

        Raises:
            TransientMediaError: If the download failed in a way a later attempt may not.
        """
        ...


@dataclass(frozen=True)
class Attachment:
    """A single file or media payload whose bytes are already in memory."""

    mime_type: str
    filename: str | None = None
    data: bytes = b""

    async def fetch(self, *, max_bytes: int) -> bytes | None:
        if not self.data or len(self.data) > max_bytes:
            return None
        return self.data


@dataclass(frozen=True)
class Message:
    """A normalized message exchanged between transports and the forwarder."""

    id: str
    chat_id: str
    sender_id: str
    sender_name: str
    text: str = ""
    attachments: tuple[MediaRef, ...] = field(default_factory=tuple)
