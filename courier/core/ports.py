from __future__ import annotations

from typing import Protocol, runtime_checkable

from .domain import Attachment, Message


@runtime_checkable
class MessageDestination(Protocol):
    """A messaging destination that can receive forwarded messages."""

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class Filter(Protocol):
    """A predicate used to decide whether a message should be forwarded."""

    def matches(self, message: Message) -> bool: ...
