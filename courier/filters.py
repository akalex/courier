from __future__ import annotations

from collections.abc import Iterable

from .domain import Message
from .ports import Filter


class _FilterBase:
    """Common interface for message filters."""

    def matches(self, message: Message) -> bool:
        raise NotImplementedError


class KeywordFilter(_FilterBase):
    """Accept messages whose text contains at least one configured keyword."""

    def __init__(self, keywords: Iterable[str] | None):
        values = tuple(str(item).strip() for item in (keywords or ()) if str(item).strip())
        self.keywords = tuple(v.lower() for v in values)

    def matches(self, message: Message) -> bool:
        if not self.keywords:
            return True
        haystack = (message.text or "").lower()
        return any(keyword in haystack for keyword in self.keywords)


class SenderFilter(_FilterBase):
    """Accept messages from a configured sender or sender alias."""

    def __init__(self, senders: Iterable[str] | None):
        self.senders = {self._normalize(value) for value in (senders or ()) if str(value).strip()}

    @staticmethod
    def _normalize(value: str) -> str:
        item = str(value).strip().lower()
        item = item.removeprefix("+")
        if "@" in item:
            item = item.split("@", 1)[0]
        return item

    def matches(self, message: Message) -> bool:
        if not self.senders:
            return True
        sender_id = self._normalize(message.sender_id)
        sender_name = self._normalize(message.sender_name)
        return sender_id in self.senders or sender_name in self.senders


class AllOf(_FilterBase):
    """Require every nested filter to match."""

    def __init__(self, *filters: Filter):
        self.filters: tuple[Filter, ...] = tuple(filters)

    def matches(self, message: Message) -> bool:
        return all(filter_.matches(message) for filter_ in self.filters)


class AnyOf(_FilterBase):
    """Require at least one nested filter to match."""

    def __init__(self, *filters: Filter):
        self.filters: tuple[Filter, ...] = tuple(filters)

    def matches(self, message: Message) -> bool:
        return any(filter_.matches(message) for filter_ in self.filters)
