from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

from .http import HTTPRequestReader, IncomingRequest, PayloadTooLargeError, write_response
from .logging_context import log_context


logger = logging.getLogger(__name__)

WebhookHandler = Callable[[dict[str, Any]], Awaitable[None]]

__all__ = ["IncomingRequest", "PayloadTooLargeError", "WebhookServer"]


class WebhookServer:
    """Small HTTP ingress server for WAHA webhook events and health checks."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        handler: WebhookHandler,
        max_body_size: int = 1024 * 1024,
        max_header_size: int = 64 * 1024,
        request_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.max_body_size = max_body_size
        self.max_header_size = max_header_size
        self.request_timeout = request_timeout
        self._http_reader = HTTPRequestReader(
            max_body_size=max_body_size,
            max_header_size=max_header_size,
        )
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle_request,
                self.host,
                self.port,
                limit=self.max_header_size,
            )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(self._http_reader.read(reader), timeout=self.request_timeout)
        except TimeoutError:
            await write_response(writer, HTTPStatus.REQUEST_TIMEOUT, b"Request timeout")
            return
        except PayloadTooLargeError:
            await write_response(writer, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Payload too large")
            return
        except asyncio.IncompleteReadError, ValueError, UnicodeDecodeError:
            await write_response(writer, HTTPStatus.BAD_REQUEST, b"Bad request")
            return

        if request.method == "GET" and request.target == "/health":
            await write_response(writer, HTTPStatus.OK, b"OK")
            return
        if request.method != "POST" or request.target != "/webhook":
            status = HTTPStatus.METHOD_NOT_ALLOWED if request.target == "/webhook" else HTTPStatus.NOT_FOUND
            await write_response(writer, status, b"Not found")
            return

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError, UnicodeDecodeError:
            await write_response(writer, HTTPStatus.BAD_REQUEST, b"Invalid JSON")
            return
        if not isinstance(payload, dict):
            await write_response(writer, HTTPStatus.BAD_REQUEST, b"Expected JSON object")
            return
        request_id = request.headers.get("x-webhook-request-id")
        if request_id:
            payload["_courier_webhook"] = {"request_id": request_id}

        try:
            with log_context(
                request_id=request_id or None,
                event_id=str(payload.get("id") or "-"),
            ):
                await self.handler(payload)
        except Exception:
            logger.exception("Webhook event processing failed")
            await write_response(writer, HTTPStatus.INTERNAL_SERVER_ERROR, b"Webhook processing failed")
            return
        await write_response(writer, HTTPStatus.OK, b"OK")
