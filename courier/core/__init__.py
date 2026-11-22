"""Courier transport-independent domain and forwarding logic."""

from .domain import Attachment, Message
from .filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from .forwarder import Forwarder
from .media import is_mp4_attachment
from .ports import Filter, MessageDestination, MessageSource


__all__ = [
    "AllOf",
    "AnyOf",
    "Attachment",
    "ExcludeKeywordFilter",
    "Filter",
    "Forwarder",
    "KeywordFilter",
    "Message",
    "MessageDestination",
    "MessageSource",
    "SenderFilter",
    "is_mp4_attachment",
]
