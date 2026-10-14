from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Attachment:
    """A single file or media payload attached to a message."""

    mime_type: str
    filename: str | None = None
    data: bytes = b""


@dataclass(frozen=True)
class Message:
    """A normalized message exchanged between transports and the forwarder."""

    id: str
    chat_id: str
    sender_id: str
    sender_name: str
    text: str = ""
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
