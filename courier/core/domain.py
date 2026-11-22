from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class MediaRef(Protocol):
    """Media attached to a message whose bytes may not have been downloaded yet."""

    @property
    def mime_type(self) -> str: ...

    @property
    def filename(self) -> str | None: ...

    async def fetch(self, *, max_bytes: int) -> bytes | None:
        """Return the media bytes, or None when unavailable or larger than max_bytes. Never raises."""
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
