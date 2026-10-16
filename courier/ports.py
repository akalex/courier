from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from .domain import Attachment, Message


@runtime_checkable
class MessageSource(Protocol):
    """A messaging source that receives messages and reports them to Courier."""

    async def start(self, on_message: Callable[[Message], Awaitable[None]]) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class MessageDestination(Protocol):
    """A messaging destination that can receive forwarded messages."""

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class Filter(Protocol):
    """A predicate used to decide whether a message should be forwarded."""

    def matches(self, message: Message) -> bool: ...
