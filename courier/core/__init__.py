"""Courier transport-independent domain, forwarding, and health logic."""

from .domain import Attachment, MediaRef, Message
from .filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from .forwarder import Forwarder
from .health import HealthObservation, HealthProbe, SessionHealthMonitor
from .media import candidate_media_kinds, is_mp4_attachment, media_kind
from .ports import Filter, MessageDestination


__all__ = [
    "AllOf",
    "AnyOf",
    "Attachment",
    "ExcludeKeywordFilter",
    "Filter",
    "Forwarder",
    "HealthObservation",
    "HealthProbe",
    "KeywordFilter",
    "MediaRef",
    "Message",
    "MessageDestination",
    "SenderFilter",
    "SessionHealthMonitor",
    "candidate_media_kinds",
    "is_mp4_attachment",
    "media_kind",
]
