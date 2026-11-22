from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class _LogContext:
    request_id: str | None = None
    event_id: str | None = None
    message_id: str | None = None


_context: ContextVar[_LogContext] = ContextVar(
    "courier_log_context",
    default=_LogContext(),
)


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    event_id: str | None = None,
    message_id: str | None = None,
) -> Generator[None, None, None]:
    current = _context.get()
    updated = _LogContext(
        request_id=request_id if request_id is not None else current.request_id,
        event_id=event_id if event_id is not None else current.event_id,
        message_id=message_id if message_id is not None else current.message_id,
    )
    token = _context.set(updated)
    try:
        yield
    finally:
        _context.reset(token)


class CourierLogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _context.get()
        fields = [
            f"{name}={value}"
            for name, value in (
                ("request_id", context.request_id),
                ("event_id", context.event_id),
                ("message_id", context.message_id),
            )
            if value
        ]
        record.courier_context = f"[{' '.join(fields)}] " if fields else ""
        return True
