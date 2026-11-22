from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar


_request_id: ContextVar[str] = ContextVar("courier_request_id", default="-")
_event_id: ContextVar[str] = ContextVar("courier_event_id", default="-")
_message_id: ContextVar[str] = ContextVar("courier_message_id", default="-")


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    event_id: str | None = None,
    message_id: str | None = None,
) -> Generator[None, None, None]:
    tokens = []
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(request_id)))
    if event_id is not None:
        tokens.append((_event_id, _event_id.set(event_id)))
    if message_id is not None:
        tokens.append((_message_id, _message_id.set(message_id)))
    try:
        yield
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)


class CourierLogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id.get()
        event_id = _event_id.get()
        message_id = _message_id.get()
        fields = []
        if request_id != "-":
            fields.append(f"request_id={request_id}")
        if event_id != "-":
            fields.append(f"event_id={event_id}")
        if message_id != "-":
            fields.append(f"message_id={message_id}")
        record.courier_context = f"[{' '.join(fields)}] " if fields else ""
        return True
